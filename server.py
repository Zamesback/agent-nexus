#!/usr/bin/env python3
"""
多Agent协作看板 - 最小原型后端服务器
零依赖（Python标准库），提供静态文件 + JSON读写API

用法：
    python3 server.py [端口]
默认端口 8766，打开 http://localhost:8766 即可
"""

import json
import os
import sys
import time
import base64
import threading
import fcntl
from contextlib import contextmanager
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# 配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOARD_FILE = os.path.join(BASE_DIR, "collab_board.json")
CONFIG_FILE = os.path.join(BASE_DIR, "project_config.json")
DEFAULT_PORT = 8766

# 多项目根目录（可通过环境变量 COLLAB_PROJECTS_ROOT 覆盖）
PROJECTS_ROOT = os.environ.get("COLLAB_PROJECTS_ROOT", "/Users/zames/Desktop/Project/agent-collab-projects")

# 全局锁，防止同进程内并发写冲突
board_lock = threading.Lock()
config_lock = threading.Lock()


# ===== 多项目管理 =====
def ensure_projects_root():
    """确保项目根目录存在"""
    if not os.path.exists(PROJECTS_ROOT):
        os.makedirs(PROJECTS_ROOT, exist_ok=True)
    return PROJECTS_ROOT


def list_projects():
    """列出所有项目（根目录下的子文件夹，包含collab_board.json的才算有效项目）"""
    ensure_projects_root()
    projects = []
    for name in sorted(os.listdir(PROJECTS_ROOT)):
        project_path = os.path.join(PROJECTS_ROOT, name)
        if os.path.isdir(project_path):
            board_file = os.path.join(project_path, "collab_board.json")
            config_file = os.path.join(project_path, "project_config.json")
            if os.path.exists(board_file):
                # 读取项目信息
                info = {"name": name, "path": project_path}
                try:
                    with open(board_file, "r", encoding="utf-8") as f:
                        board = json.load(f)
                    info["project_name"] = board.get("project_info", {}).get("name", name)
                    info["description"] = board.get("project_info", {}).get("description", "")
                    info["task_count"] = len(board.get("tasks", []))
                    info["done_count"] = len([t for t in board.get("tasks", []) if t.get("status") == "done"])
                    info["message_count"] = len(board.get("messages", []))
                    info["agents"] = [a.get("name", a.get("id")) for a in board.get("agents", [])]
                except Exception:
                    pass
                info["created"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getctime(project_path)))
                info["modified"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(board_file)))
                projects.append(info)
    return projects


def get_project_path(project_name):
    """获取项目路径，如果项目不存在返回None"""
    if not project_name:
        return None
    # 安全检查：防止路径遍历
    if ".." in project_name or "/" in project_name or "\\" in project_name:
        return None
    project_path = os.path.join(PROJECTS_ROOT, project_name)
    if os.path.isdir(project_path):
        return project_path
    return None


def _build_onboarding_md(project_name, project_title, description, workspace, agents, agent_id=None):
    """T9.1: 生成 Agent 接入引导文档（AGENT_ONBOARDING.md）

    供新接入的 AI Agent 阅读：项目信息、看板路径、协作规则、触发方式。
    如果指定 agent_id，则生成专属接入引导，开头明确身份信息。
    """
    board_path = os.path.join(PROJECTS_ROOT, project_name, "collab_board.json")
    url = f"http://localhost:8766/collab_board_nothing.html?project={project_name}"
    agent_lines_list = []
    for a in agents:
        if isinstance(a, dict):
            agent_lines_list.append(f"- **{a.get('name', a['id'])}**（`{a['id']}`）：{a.get('role', '')}")
        else:
            agent_lines_list.append(f"- **{a}**（`{a}`）")
    agent_lines = "\n".join(agent_lines_list)

    # 如果指定了agent_id，生成专属身份头部
    identity_header = ""
    if agent_id:
        target_agent = None
        for a in agents:
            if isinstance(a, dict) and a.get("id") == agent_id:
                target_agent = a
                break
        if target_agent:
            agent_name = target_agent.get("name", agent_id)
            agent_role = target_agent.get("role", "")

            # 判断Agent类型，生成对应的身份认知说明
            agent_id_lower = agent_id.lower()
            agent_name_lower = agent_name.lower()
            is_planner = "plan" in agent_id_lower or "规划" in agent_name or "planner" in agent_name_lower
            is_builder = "build" in agent_id_lower or "执行" in agent_name or "builder" in agent_name_lower or "开发" in agent_name
            is_user = "user" in agent_id_lower or "用户" in agent_name or "zames" in agent_name_lower

            if is_planner:
                identity_detail = f"""### 🎯 你的核心身份

你是 **规划Agent（Planner）**，是这个项目的**规划者和协调者**。

### 📋 你的职责

- **任务规划**：把用户需求拆解成具体可执行的任务
- **方案评审**：评估技术方案的可行性和合理性
- **进度协调**：跟踪任务进度，协调各Agent之间的工作
- **质量验收**：验收执行Agent完成的任务，确保质量达标
- **风险识别**：提前识别项目风险和依赖关系

### 🚫 你不是谁

- 你**不是执行Agent**，不负责具体的代码开发和功能实现
- 你**不是用户**，不做最终的产品决策和需求确认
- 遇到需要用户决策的问题，应该@用户确认，而不是自己决定

### 🤝 你和其他Agent的协作

- **用户**：需求来源和最终决策者，你需要向用户汇报进度和方案
- **执行Agent**：你的规划需要执行Agent来落地，你需要给执行Agent清晰的任务描述和验收标准
- **其他协同Agent**：协调各自的工作，避免重复和冲突

### 💡 工作原则

1. 先规划，后执行——不要跳过规划直接动手
2. 任务要可验收——每个任务都要有明确的完成标准
3. 及时同步进度——定期在看板上更新任务状态
4. 遇到问题及时沟通——不要闷头苦干，有问题@相关方
"""
            elif is_builder:
                identity_detail = f"""### 🎯 你的核心身份

你是 **执行Agent（Builder/Executor）**，是这个项目的**执行者和实现者**。

### 📋 你的职责

- **功能开发**：根据规划Agent拆解的任务，实现具体的功能和代码
- **技术实现**：把方案和设计变成可运行的代码
- **测试验证**：对自己实现的功能进行测试，确保质量
- **文档编写**：编写必要的技术文档和使用说明
- **问题修复**：修复测试和验收中发现的bug

### 🚫 你不是谁

- 你**不是规划Agent**，不负责整体的任务规划和拆解
- 你**不是用户**，不做产品决策和需求确认
- 遇到需求不明确或方案有争议时，应该@规划Agent或用户确认，而不是自己拍脑袋决定

### 🤝 你和其他Agent的协作

- **规划Agent**：你的任务来源，你需要按照规划Agent的任务描述和验收标准来执行
- **用户**：最终决策者，你实现的功能最终需要用户验收
- **其他协同Agent**：做好分工配合，避免重复开发，及时同步接口约定

### 💡 工作原则

1. 先理解，后动手——确保理解任务需求和验收标准再开始
2. 质量第一——代码要可维护、可测试、有注释
3. 及时同步进度——在看板上更新任务状态，让规划Agent知道进度
4. 有问题及时问——不要自己猜，有疑问@规划Agent确认
"""
            elif is_user:
                identity_detail = f"""### 🎯 你的核心身份

你是 **用户（User/Product Owner）**，是这个项目的**产品决策者和最终验收者**。

### 📋 你的职责

- **需求定义**：明确项目目标和功能需求
- **产品决策**：对技术方案和功能设计做最终决策
- **优先级排序**：决定任务的优先级和开发顺序
- **最终验收**：验收所有完成的功能，确认是否符合预期
- **资源协调**：协调项目所需的各种资源

### 🤝 你和其他Agent的协作

- **规划Agent**：帮你把需求拆解成可执行的任务，你需要确认规划是否合理
- **执行Agent**：帮你实现具体功能，你需要验收实现结果
- 你是项目的最终决策者，所有重要决策都需要你确认

### 💡 工作原则

1. 需求要明确——尽量给Agent清晰的需求描述，减少反复沟通
2. 及时做决策——Agent遇到需要决策的问题时，及时给出方向
3. 验收要严格——确保交付的功能符合你的预期
"""
            else:
                identity_detail = f"""### 🎯 你的核心身份

你是 **{agent_name}**，Agent ID 是 `{agent_id}`，角色是：{agent_role or '（未指定）'}。

### 📋 你的职责

根据你的角色描述，你负责：{agent_role or '（请根据项目需要自行明确职责）'}

### 🤝 你和其他Agent的协作

- 读取看板了解项目状态和其他Agent的工作
- 响应@你的消息，及时沟通和协作
- 在看板上更新你的任务状态，让其他Agent知道进度

### 💡 工作原则

1. 先理解任务，再动手执行
2. 及时同步进度，保持信息透明
3. 有问题及时沟通，不要闷头苦干
"""

            identity_header = f"""## 你的身份认知

> **重要**：请仔细阅读以下内容，明确你在这个项目中的身份、职责和协作关系。这是你开展工作的基础。

{identity_detail}
### 📝 基本信息

- **Agent名称**：{agent_name}
- **Agent ID**：`{agent_id}`
- **角色描述**：{agent_role or '（未指定）'}

### 🔧 操作规范

- 注册时使用这个 ID：`{agent_id}`
- 在看板中发消息时，sender 字段请填写：`{agent_id}`
- 认领任务时，assignee 字段请填写：`{agent_id}`

---

"""

    return f"""# {project_title} — Agent 接入引导

{identity_header}本文件是「{project_title}」的 Agent 协作入口。接入本项目的 AI Agent 请先完整阅读本文件。

## 项目信息
- 项目：{project_title}
- 描述：{description or '（无）'}
- 工作区：{workspace or '（未指定）'}

## 看板位置
- 看板数据文件：`{board_path}`
- 看板访问地址：{url}

## 已接入 Agent
{agent_lines}

## 协作规则
1. 读取看板文件了解项目状态（任务、消息、Agent 列表）
2. 响应消息中 `@你的名字或ID` 的提及
3. 处理相关任务，用以下结构化格式更新任务状态：
   - 认领任务：`[CLAIM_TASK id=任务ID]`
   - 更新任务：`[UPDATE_TASK id=任务ID status=done progress=100 assignee=xxx]`
4. 把处理结果作为新消息写回看板文件（追加，不覆盖）
5. 看板文件是协作的单一事实源，**只做增量修改，绝不整体覆盖**

## 触发方式
- 混合模式：人类复制触发指令给你（手动）
- API 模式：配置 LLM API 后 `@` 自动触发
- 定时模式：定时轮询扫描未处理 `@` 消息
- Webhook 推送：注册 HTTP webhook 后，`@` 消息实时推送给你

## 注册方式
调用注册 API 接入看板：
```
POST /api/agents/register?project={project_name}
Content-Type: application/json

{{
  "agent_id": "{agent_id or '你的ID'}",
  "entry": {{
    "type": "http",
    "target": "你的webhook地址"
  }}
}}
```

## 版本管理约定
- 遵守项目 CONTRIBUTING.md 的版本管理、分支、提交规范
- 敏感文件（project_config.json 等）禁止入库
"""


def create_project(project_name, agents=None, workspace="", handoff="", project_title="", description=""):
    """创建新项目

    Args:
        project_name: 项目文件夹名（唯一标识）
        agents: 接入的Agent列表，如 ["user", "planner", "builder"]
        workspace: 工作区路径
        handoff: 交接文件内容
        project_title: 项目显示名称
        description: 项目描述

    Returns:
        (success, message, project_path)
    """
    ensure_projects_root()

    # 安全检查
    if not project_name or not project_name.strip():
        return False, "项目名不能为空", None
    if ".." in project_name or "/" in project_name or "\\" in project_name:
        return False, "项目名不能包含路径分隔符", None

    project_path = os.path.join(PROJECTS_ROOT, project_name.strip())
    if os.path.exists(project_path):
        return False, f"项目 '{project_name}' 已存在", None

    # 创建项目文件夹
    os.makedirs(project_path, exist_ok=True)

    # 默认Agent定义
    all_agents = {
        "user": {"id": "user", "name": "Zames", "role": "项目主导方/最终决策者", "status": "online"},
        "planner": {"id": "planner", "name": "规划Agent", "role": "任务拆解/方案评审/进度协调/验收", "status": "online"},
        "builder": {"id": "builder", "name": "执行Agent", "role": "前端开发/后端开发/测试/文档", "status": "online"}
    }

    # 选择接入的Agent
    if agents is None:
        agents = ["user", "planner", "builder"]
    selected_agents = [all_agents[a] for a in agents if a in all_agents]
    if "user" not in agents:
        selected_agents.insert(0, all_agents["user"])  # 用户必须在

    # 创建看板数据
    now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    board_data = {
        "project_info": {
            "name": project_title or project_name,
            "description": description or "",
            "created": now,
            "version": "1.0"
        },
        "agents": selected_agents,
        "tasks": [],
        "messages": [
            {
                "id": 1,
                "sender": "system",
                "content": f"项目 '{project_title or project_name}' 已创建。\n\n接入Agent: {', '.join([a['name'] for a in selected_agents])}\n工作区: {workspace or '未指定'}\n\n{handoff or '暂无交接文件'}",
                "timestamp": now,
                "type": "system"
            }
        ]
    }

    # 保存看板数据
    board_file = os.path.join(project_path, "collab_board.json")
    with open(board_file, "w", encoding="utf-8") as f:
        json.dump(board_data, f, ensure_ascii=False, indent=2)

    # 创建项目配置
    config_data = {
        "project_name": project_title or project_name,
        "workspace": workspace or "",
        "agents": agents,
        "handoff": handoff or "",
        "api_config": {
            "enabled": False,
            "api_key": "",
            "base_url": "https://api.openai.com/v1",
            "model": "gpt-4o-mini",
            "auto_trigger": False,
            "max_chain_length": 3,
            "request_timeout": 60,
            "schedule": {
                "enabled": False,
                "interval_minutes": 30,
                "next_run_time": None,
                "last_run_time": None,
                "total_runs": 0
            },
            "stats": {
                "total_calls": 0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "last_call_time": None,
                "last_error": None
            }
        }
    }
    config_file = os.path.join(project_path, "project_config.json")
    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config_data, f, ensure_ascii=False, indent=2)

    # 生成 Agent 接入引导文档（T9.1）
    try:
        onboarding_md = _build_onboarding_md(
            project_name,
            project_title or project_name,
            description or "",
            workspace or "",
            selected_agents
        )
        onboarding_file = os.path.join(project_path, "AGENT_ONBOARDING.md")
        with open(onboarding_file, "w", encoding="utf-8") as f:
            f.write(onboarding_md)
    except Exception as e:
        print(f"[T9.1] 生成接入引导失败: {e}")

    return True, "项目创建成功", project_path


def delete_project(project_name, scope="data_only"):
    """删除项目

    Args:
        project_name: 项目文件夹名
        scope: 删除范围
            - "data_only": 只删除数据文件（看板/配置/日志/锁），保留空文件夹
            - "all": 删除整个项目文件夹

    Returns:
        (success, message, deleted_files)
    """
    project_path = get_project_path(project_name)
    if not project_path:
        return False, f"项目 '{project_name}' 不存在", []

    deleted_files = []

    if scope == "data_only":
        # 只删除数据文件，保留文件夹
        data_files = [
            "collab_board.json",
            "project_config.json",
            "processed_msg_ids.json",
            "api_calls.log",
        ]
        for filename in data_files:
            filepath = os.path.join(project_path, filename)
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    deleted_files.append(filename)
                except Exception as e:
                    return False, f"删除 {filename} 失败: {str(e)}", deleted_files

        # 删除所有 .lock 文件
        for filename in os.listdir(project_path):
            if filename.endswith(".lock"):
                filepath = os.path.join(project_path, filename)
                try:
                    os.remove(filepath)
                    deleted_files.append(filename)
                except Exception:
                    pass

        return True, f"已删除 {len(deleted_files)} 个数据文件，项目文件夹保留", deleted_files

    elif scope == "all":
        # 删除整个项目文件夹
        try:
            # 先记录删除的文件
            for root, dirs, files in os.walk(project_path):
                for f in files:
                    deleted_files.append(os.path.relpath(os.path.join(root, f), project_path))

            # 删除整个文件夹
            import shutil
            shutil.rmtree(project_path)
            return True, f"项目 '{project_name}' 已完全删除（{len(deleted_files)} 个文件）", deleted_files
        except Exception as e:
            return False, f"删除项目文件夹失败: {str(e)}", deleted_files

    else:
        return False, f"未知的删除范围: {scope}", []


def get_project_files(project_name):
    """获取项目的数据文件路径，如果项目不存在返回默认路径（向后兼容）"""
    project_path = get_project_path(project_name)
    if project_path:
        return {
            "board_file": os.path.join(project_path, "collab_board.json"),
            "config_file": os.path.join(project_path, "project_config.json"),
            "api_log_file": os.path.join(project_path, "api_calls.log"),
            "processed_ids_file": os.path.join(project_path, "processed_msg_ids.json"),
            "project_path": project_path
        }
    # 向后兼容：没有指定项目时使用当前目录
    return {
        "board_file": BOARD_FILE,
        "config_file": CONFIG_FILE,
        "api_log_file": os.path.join(BASE_DIR, "api_calls.log"),
        "processed_ids_file": os.path.join(BASE_DIR, "processed_msg_ids.json"),
        "project_path": BASE_DIR
    }


@contextmanager
def file_lock(filepath, exclusive=True):
    """跨进程文件锁（fcntl.flock，macOS/Linux可用）
    exclusive=True: 排他锁（写入用）
    exclusive=False: 共享锁（读取用）
    """
    lock_path = filepath + ".lock"
    lock_fd = open(lock_path, "w")
    try:
        if exclusive:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        else:
            fcntl.flock(lock_fd, fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def load_board(project=None):
    """读取看板数据（共享锁，防止读到写了一半的文件）
    project: 项目名，None表示使用当前目录（向后兼容）
    """
    files = get_project_files(project)
    board_file = files["board_file"]
    with file_lock(board_file, exclusive=False):
        with open(board_file, "r", encoding="utf-8") as f:
            return json.load(f)


def save_board(data, project=None):
    """保存看板数据（排他锁 + 原子写入）
    project: 项目名，None表示使用当前目录（向后兼容）
    """
    files = get_project_files(project)
    board_file = files["board_file"]
    with file_lock(board_file, exclusive=True):
        # 原子写入：先写临时文件，再rename
        tmp_path = board_file + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, board_file)


def load_config(project=None):
    """读取项目配置（共享锁），api_key 自动解密为明文供上层使用
    project: 项目名，None表示使用当前目录（向后兼容）
    """
    files = get_project_files(project)
    config_file = files["config_file"]
    if not os.path.exists(config_file):
        return {"meta": {}, "workspace": {}, "agents": []}
    with file_lock(config_file, exclusive=False):
        with open(config_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    _decrypt_config_inplace(data)
    return data


def save_config(data, project=None):
    """保存项目配置（排他锁 + 原子写入），api_key 自动加密存储
    project: 项目名，None表示使用当前目录（向后兼容）
    """
    files = get_project_files(project)
    config_file = files["config_file"]
    _encrypt_config_inplace(data)
    with file_lock(config_file, exclusive=True):
        tmp_path = config_file + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, config_file)


# ===== Agent 入口注册（T8.1）=====
def register_agent(project, agent_id, entry):
    """T8.1: 注册 Agent 入口，标记接通状态

    entry: {"type": "http"|"session", "target": "url或会话描述"}
    注册后 connected=True，记录 registered_at / last_seen，并同步到看板。
    """
    config = load_config(project)
    agents = config.get("agents", [])
    now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    found = False
    for agent in agents:
        if agent.get("id") == agent_id:
            agent["entry"] = entry
            agent["connected"] = True
            agent["registered_at"] = agent.get("registered_at", now)
            agent["last_seen"] = now
            found = True
            break
    if not found:
        # 注册即创建（Agent 首次接入自动加入列表）
        agents.append({
            "id": agent_id,
            "name": agent_id,
            "role": "",
            "color": "#a1a1a6",
            "connected": True,
            "entry": entry,
            "registered_at": now,
            "last_seen": now
        })
    config["agents"] = agents
    save_config(config, project)

    # 同步 agents 到看板，保持一致
    try:
        data = load_board(project)
        data["agents"] = agents
        save_board(data, project)
    except Exception as e:
        print(f"[T8.1] 同步 agents 到看板失败: {e}")

    return True, f"Agent {agent_id} 注册成功"


# ===== Agent @消息推送与接通确认（T8.2）=====
def push_message_to_agent(agent, msg, project=None):
    """T8.2: 推送@消息给已注册HTTP webhook的Agent

    Args:
        agent: Agent配置对象（含entry字段）
        msg: 消息对象
        project: 项目名（可选）

    Returns:
        (success, message)
    """
    entry = agent.get("entry")
    if not entry or entry.get("type") != "http":
        return False, "Agent未注册HTTP webhook"

    webhook_url = entry.get("target", "")
    if not webhook_url:
        return False, "webhook URL为空"

    # 构建推送payload
    payload = {
        "event": "message.mention",
        "project": project or "",
        "agent_id": agent.get("id", ""),
        "agent_name": agent.get("name", ""),
        "message": {
            "id": msg.get("id"),
            "sender": msg.get("sender"),
            "content": msg.get("content"),
            "timestamp": msg.get("timestamp"),
            "type": msg.get("type", "text")
        },
        "board_path": os.path.join(PROJECTS_ROOT, project, "collab_board.json") if project else BOARD_FILE,
        "reply_api": f"/api/messages"
    }

    try:
        import urllib.request
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                # 推送成功，更新last_seen（接通确认）
                _update_agent_last_seen(agent.get("id"), project)
                return True, "推送成功"
            else:
                return False, f"推送失败，HTTP状态: {resp.status}"
    except Exception as e:
        # 推送失败，标记Agent为offline
        _mark_agent_offline(agent.get("id"), project)
        return False, f"推送异常: {str(e)}"


def _update_agent_last_seen(agent_id, project=None):
    """更新Agent的last_seen时间（接通确认）"""
    try:
        config = load_config(project)
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        for agent in config.get("agents", []):
            if agent.get("id") == agent_id:
                agent["last_seen"] = now
                agent["connected"] = True
                break
        save_config(config, project)
        # 同步到看板
        data = load_board(project)
        data["agents"] = config.get("agents", [])
        save_board(data, project)
    except Exception as e:
        print(f"[T8.2] 更新last_seen失败: {e}")


def _mark_agent_offline(agent_id, project=None):
    """推送失败时标记Agent为offline"""
    try:
        config = load_config(project)
        for agent in config.get("agents", []):
            if agent.get("id") == agent_id:
                agent["connected"] = False
                break
        save_config(config, project)
        data = load_board(project)
        data["agents"] = config.get("agents", [])
        save_board(data, project)
    except Exception as e:
        print(f"[T8.2] 标记offline失败: {e}")


def check_agent_online(agent, timeout_seconds=300):
    """检查Agent是否在线（基于last_seen，默认5分钟超时）"""
    last_seen = agent.get("last_seen")
    if not last_seen:
        return False
    try:
        last_time = time.strptime(last_seen, "%Y-%m-%dT%H:%M:%S+08:00")
        elapsed = time.time() - time.mktime(last_time)
        return elapsed < timeout_seconds
    except Exception:
        return False


# ===== api_key 加密存储（T2.6）=====
# 仅对 api_key 字段做加密，其余配置字段保持明文可编辑。
# 密文带类型前缀，解密方式由前缀决定（与运行环境解耦）：
#   enc:b:  = base64 编码
#   enc:x:  = 环境变量 COLLAB_API_ENC_KEY 做 XOR 混淆 + base64
# 解密失败或无前缀时按明文处理（兼容旧配置）。
ENC_PREFIX = "enc:"
ENC_BASE64 = "enc:b:"
ENC_XOR = "enc:x:"


def _encrypt_secret(plain):
    """加密敏感字段。已带 enc: 前缀的原样返回，避免双重加密。"""
    if not plain or plain.startswith(ENC_PREFIX):
        return plain
    raw = plain.encode("utf-8")
    env_key = os.environ.get("COLLAB_API_ENC_KEY", "")
    if env_key:
        kb = env_key.encode("utf-8")
        payload = bytes(b ^ kb[i % len(kb)] for i, b in enumerate(raw))
        return ENC_XOR + base64.b64encode(payload).decode("ascii")
    return ENC_BASE64 + base64.b64encode(raw).decode("ascii")


def _decrypt_secret(enc):
    """解密敏感字段。无 enc: 前缀按明文返回；解密失败保持原样避免崩溃。"""
    if not enc or not enc.startswith(ENC_PREFIX):
        return enc
    try:
        if enc.startswith(ENC_BASE64):
            return base64.b64decode(enc[len(ENC_BASE64):].encode("ascii")).decode("utf-8")
        if enc.startswith(ENC_XOR):
            env_key = os.environ.get("COLLAB_API_ENC_KEY", "")
            if not env_key:
                return enc  # 缺少密钥无法解密，保持原样
            kb = env_key.encode("utf-8")
            payload = base64.b64decode(enc[len(ENC_XOR):].encode("ascii"))
            return bytes(b ^ kb[i % len(kb)] for i, b in enumerate(payload)).decode("utf-8")
        # 兼容无子类型的早期 enc: 前缀（按 base64 尝试）
        try:
            return base64.b64decode(enc[len(ENC_PREFIX):].encode("ascii")).decode("utf-8")
        except Exception:
            return enc
    except Exception:
        return enc


def _encrypt_config_inplace(config):
    """就地加密配置中的敏感字段（save_config 调用）"""
    api = config.get("api_config")
    if api and api.get("api_key"):
        api["api_key"] = _encrypt_secret(api["api_key"])


def _decrypt_config_inplace(config):
    """就地解密配置中的敏感字段（load_config 调用）"""
    api = config.get("api_config")
    if api and api.get("api_key"):
        api["api_key"] = _decrypt_secret(api["api_key"])


# ===== LLM API 调用封装（T2.3，OpenAI兼容格式）=====
import urllib.request
import urllib.error


# ===== Agent 角色定义与 System Prompt 模板（T3.1）=====
AGENT_PROMPTS = {
    "planner": """你是「规划Agent」，多Agent协同看板中的项目规划和协调专家。

【你的职责】
1. 任务拆解：把用户需求拆解成可执行的任务，分配优先级和依赖关系
2. 方案评审：评审执行Agent提交的方案和产物，提出改进意见
3. 进度协调：跟踪任务进度，协调Agent之间的依赖和沟通
4. 验收确认：对完成的任务进行验收，确认是否符合要求

【你可以执行的动作】
- 发消息：直接回复内容即可
- 更新任务：用 [UPDATE_TASK id=xxx status=xxx progress=xxx assignee=xxx artifact=xxx] 格式
- 认领任务：用 [CLAIM_TASK id=xxx] 格式

【回复要求】
- 简洁结构化，不要废话
- 有明确的行动项
- 涉及任务更新时必须用指定格式
- 先给结论，再给细节""",

    "builder": """你是「执行Agent」，多Agent协同看板中的全栈开发工程师。

【你的职责】
1. 前端开发：HTML/CSS/JS界面开发，交互实现
2. 后端开发：Python API开发，数据处理，系统集成
3. 测试验证：功能测试，边界测试，回归测试
4. 文档编写：使用文档，API文档，最佳实践

【你可以执行的动作】
- 发消息：直接回复内容即可
- 更新任务：用 [UPDATE_TASK id=xxx status=xxx progress=xxx artifact=xxx] 格式
- 认领任务：用 [CLAIM_TASK id=xxx] 格式

【回复要求】
- 先说明做了什么，再给出技术细节
- 完成任务后更新状态并填写artifact
- 遇到问题及时说明，不要静默失败
- 代码修改要具体，给出文件和函数名""",

    "user": """你是「Zames」，项目主导方和最终决策者。

【你的职责】
1. 产品决策：决定项目方向、优先级、资源分配
2. 需求确认：确认需求理解，提供业务背景和约束
3. 最终验收：对交付物做最终验收，决定是否通过
4. 冲突仲裁：当Agent之间有分歧时，做最终裁决

【回复要求】
- 决策明确，不模棱两可
- 给出理由和背景
- 对不确定的事情明确说需要再想想或先按A方案试"""
}

DEFAULT_AGENT_PROMPT = """你是「{agent_name}」，多Agent协同看板中的参与者。

【你的职责】
{agent_role}

【你可以执行的动作】
- 发消息：直接回复内容即可
- 更新任务：用 [UPDATE_TASK id=xxx status=xxx progress=xxx artifact=xxx] 格式
- 认领任务：用 [CLAIM_TASK id=xxx] 格式

【回复要求】
- 简洁明了，有行动项
- 完成任务后更新状态"""


def get_agent_prompt(agent_id, agent_name="", agent_role=""):
    """获取Agent的system prompt（T3.1）"""
    if agent_id in AGENT_PROMPTS:
        return AGENT_PROMPTS[agent_id]
    return DEFAULT_AGENT_PROMPT.format(agent_name=agent_name or agent_id, agent_role=agent_role or "参与项目协作")


def build_context_prompt(board_data, trigger_message=None):
    """构建看板上下文prompt（T3.1），把当前看板状态压缩成文本"""
    lines = []
    lines.append("【当前看板状态】")
    tasks = board_data.get("tasks", [])
    done = len([t for t in tasks if t["status"] == "done"])
    in_progress = len([t for t in tasks if t["status"] == "in_progress"])
    todo = len([t for t in tasks if t["status"] == "todo"])
    lines.append(f"任务总数: {len(tasks)}（已完成{done} / 进行中{in_progress} / 待办{todo}）")
    active_tasks = [t for t in tasks if t["status"] == "in_progress"]
    if active_tasks:
        lines.append("\n【进行中的任务】")
        for t in active_tasks:
            lines.append(f"- {t['id']}: {t['title']}（负责人: {t.get('assignee', '未分配')}, 进度: {t.get('progress', 0)}%）")
    high_todo = [t for t in tasks if t["status"] == "todo" and t.get("priority") == "high"]
    if high_todo:
        lines.append("\n【高优先级待办】")
        for t in high_todo[:5]:
            lines.append(f"- {t['id']}: {t['title']}")
    messages = board_data.get("messages", [])
    if messages:
        lines.append("\n【最近消息】")
        for m in messages[-5:]:
            sender = m.get("sender", "unknown")
            content = m.get("content", "")[:100]
            lines.append(f"- [{sender}]: {content}")
    if trigger_message:
        lines.append("\n【触发你的消息】")
        sender = trigger_message.get("sender", "unknown")
        content = trigger_message.get("content", "")
        lines.append(f"[{sender}]: {content}")
    return "\n".join(lines)


# ===== @提及检测与自动触发（T3.2）=====
# T3.4: 触发链状态（防循环）
_chain_lock = threading.Lock()
_chain_state = {
    "chain_id": None,
    "depth": 0,
    "last_sender": None,
    "message_ids": set()
}


def check_and_update_chain(msg, max_depth=3):
    """T3.4: 检查并更新触发链状态，返回(是否允许触发, 原因)

    防循环逻辑：
    - 用户发的消息（非auto_triggered）重置链深度
    - 自动触发的消息增加链深度
    - 链深度超过max_depth时停止触发，提示用户介入
    - 同一条消息只触发一次
    """
    global _chain_state

    with _chain_lock:
        msg_id = msg.get("id")
        sender = msg.get("sender")
        is_auto = msg.get("auto_triggered", False)

        # 同一条消息只触发一次
        if msg_id in _chain_state["message_ids"]:
            return False, "消息已处理过"

        # 用户发的消息重置链
        if not is_auto:
            _chain_state = {
                "chain_id": f"chain_{int(time.time())}",
                "depth": 0,
                "last_sender": sender,
                "message_ids": {msg_id}
            }
            return True, "用户消息，重置链"

        # 自动触发的消息，增加深度
        _chain_state["depth"] += 1
        _chain_state["message_ids"].add(msg_id)
        _chain_state["last_sender"] = sender

        # 检查深度限制
        if _chain_state["depth"] > max_depth:
            return False, f"触发链深度超过限制({_chain_state['depth']}/{max_depth})，停止自动触发"

        return True, f"链深度 {_chain_state['depth']}/{max_depth}"


def get_chain_state():
    """获取当前触发链状态（用于前端显示）"""
    with _chain_lock:
        return dict(_chain_state)


# T4.3: 已处理消息ID持久化
PROCESSED_MSG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "processed_msg_ids.json")
MAX_PROCESSED_IDS = 1000  # 最多保留1000条已处理ID


def load_processed_msg_ids():
    """T4.3: 从文件加载已处理的消息ID"""
    try:
        if os.path.exists(PROCESSED_MSG_FILE):
            with open(PROCESSED_MSG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("ids", []))
    except Exception as e:
        print(f"[T4.3] 加载已处理消息ID失败: {e}")
    return set()


def save_processed_msg_ids(ids):
    """T4.3: 保存已处理的消息ID到文件（只保留最近MAX_PROCESSED_IDS条）"""
    try:
        ids_list = list(ids)
        if len(ids_list) > MAX_PROCESSED_IDS:
            ids_list = ids_list[-MAX_PROCESSED_IDS:]
        with open(PROCESSED_MSG_FILE, "w", encoding="utf-8") as f:
            json.dump({"ids": ids_list, "count": len(ids_list)}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[T4.3] 保存已处理消息ID失败: {e}")


# ===== 定时任务调度器（T4.1）=====
# 支持的频率（分钟）
SCHEDULE_INTERVALS = {
    5: "5分钟",
    15: "15分钟",
    30: "30分钟",
    60: "1小时",
    1440: "1天"
}


class ScheduleManager:
    """定时任务调度器（T4.1）

    支持5档频率：5/15/30分钟、1小时、1天
    使用threading.Timer实现，服务器启动时根据配置自动启动
    """

    def __init__(self):
        self._timer = None
        # 用可重入锁：start() 持锁调用 _schedule_next()，后者需再次获取同一把锁
        self._lock = threading.RLock()
        self._running = False
        # T4.2: 定时检查已处理的消息ID（内存去重）
        # T4.3: 从文件加载已处理的消息ID（持久化，服务器重启后不丢失）
        self._processed_msg_ids = load_processed_msg_ids()
        print(f"[T4.3] 已加载 {len(self._processed_msg_ids)} 条已处理消息ID")

    def start(self):
        """启动定时调度器"""
        with self._lock:
            if self._running:
                return
            config = load_config()
            schedule = config.get("api_config", {}).get("schedule", {})
            if schedule.get("enabled", False):
                self._running = True
                self._schedule_next()
                print(f"[定时调度器] 已启动，频率: {schedule.get('interval_minutes', 30)}分钟")

    def stop(self):
        """停止定时调度器"""
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self._running = False
            print("[定时调度器] 已停止")

    def restart(self):
        """重启调度器（配置变更后调用）"""
        self.stop()
        self.start()

    def _schedule_next(self):
        """安排下一次触发"""
        with self._lock:
            config = load_config()
            schedule = config.get("api_config", {}).get("schedule", {})
            interval_minutes = schedule.get("interval_minutes", 30)
            interval_seconds = interval_minutes * 60

            # 计算下次触发时间
            next_run = time.time() + interval_seconds
            schedule["next_run_time"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime(next_run))
            config["api_config"]["schedule"] = schedule
            save_config(config)

            # 设置定时器
            self._timer = threading.Timer(interval_seconds, self._run_task)
            self._timer.daemon = True
            self._timer.start()

    def _run_task(self):
        """执行定时任务（T4.2会实现具体检查逻辑）"""
        try:
            print(f"[定时调度器] 触发检查 at {time.strftime('%H:%M:%S')}")

            # 更新统计
            config = load_config()
            schedule = config.get("api_config", {}).get("schedule", {})
            schedule["last_run_time"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
            schedule["total_runs"] = schedule.get("total_runs", 0) + 1
            config["api_config"]["schedule"] = schedule
            save_config(config)

            # T4.2: 扫描未处理的@消息并触发（这里先留空，T4.2实现）
            self._check_and_trigger()

        except Exception as e:
            print(f"[定时调度器] 执行异常: {e}")
        finally:
            # 安排下一次
            if self._running:
                self._schedule_next()

    def _check_and_trigger(self):
        """T4.2: 扫描未处理的@消息并触发

        定时检查逻辑：
        1. 扫描最近N条消息，找到包含@的消息
        2. 跳过已处理的消息（内存去重，T4.3会做持久化）
        3. 对未处理的@消息调用trigger_agent_if_mentioned
        4. 记录处理结果
        """
        try:
            board_data = load_board()
            messages = board_data.get("messages", [])
            agents = board_data.get("agents", [])

            # 只扫描最近100条消息，避免性能问题
            recent_messages = messages[-100:] if len(messages) > 100 else messages

            triggered_count = 0
            for msg in recent_messages:
                msg_id = msg.get("id")
                content = msg.get("content", "")
                msg_type = msg.get("type", "text")

                # 跳过系统消息和已处理的消息
                if msg_type == "system":
                    continue
                if msg_id in self._processed_msg_ids:
                    continue

                # 检测是否包含@
                mentioned = detect_mentions(content, agents)
                if not mentioned:
                    self._processed_msg_ids.add(msg_id)
                    continue

                # 排除自己@自己
                sender = msg.get("sender", "")
                mentioned = [a for a in mentioned if a != sender]

                if not mentioned:
                    self._processed_msg_ids.add(msg_id)
                    continue

                # 调用trigger_agent_if_mentioned（内部会检查auto_trigger开关和防循环）
                trigger_agent_if_mentioned(msg, board_data)
                self._processed_msg_ids.add(msg_id)
                triggered_count += 1

            if triggered_count > 0:
                print(f"[定时调度器] 扫描完成，触发了 {triggered_count} 条@消息")

            # T4.3: 保存已处理的消息ID到文件（持久化）
            save_processed_msg_ids(self._processed_msg_ids)

        except Exception as e:
            print(f"[定时调度器] 检查异常: {e}")

    def get_status(self):
        """获取调度器状态"""
        config = load_config()
        schedule = config.get("api_config", {}).get("schedule", {})
        return {
            "running": self._running,
            "enabled": schedule.get("enabled", False),
            "interval_minutes": schedule.get("interval_minutes", 30),
            "interval_label": SCHEDULE_INTERVALS.get(schedule.get("interval_minutes", 30), "30分钟"),
            "next_run_time": schedule.get("next_run_time"),
            "last_run_time": schedule.get("last_run_time"),
            "total_runs": schedule.get("total_runs", 0),
            "available_intervals": SCHEDULE_INTERVALS
        }


# 全局调度器实例
schedule_manager = ScheduleManager()


def detect_mentions(content, agents):
    """检测消息中@了哪些Agent，返回agent_id列表"""
    mentioned = []
    if not content:
        return mentioned
    for agent in agents:
        agent_id = agent.get("id", "")
        agent_name = agent.get("name", "")
        if f"@{agent_id}" in content or f"@{agent_name}" in content:
            mentioned.append(agent_id)
    return mentioned


def trigger_agent_if_mentioned(msg, board_data, project=None):
    """T3.2: 检测消息中的@提及，如果auto_trigger开启则异步触发对应Agent
    T8.2: 如果Agent注册了http webhook，独立推送@消息（不依赖API模式）
    """
    try:
        config = load_config(project)
        api_config = config.get("api_config", {})

        # 检测@提及
        agents = board_data.get("agents", [])
        mentioned = detect_mentions(msg.get("content", ""), agents)

        # 排除自己@自己
        sender = msg.get("sender", "")
        mentioned = [a for a in mentioned if a != sender]

        if not mentioned:
            return

        # T4.3改进：即时触发的@消息也记录到_processed_msg_ids，避免定时扫描重复触发
        msg_id = msg.get("id")
        if msg_id is not None:
            schedule_manager._processed_msg_ids.add(msg_id)
            # 异步保存到文件（不阻塞）
            threading.Thread(target=save_processed_msg_ids, args=(schedule_manager._processed_msg_ids,), daemon=True).start()

        # T8.2: Webhook推送 —— 独立于API模式，只要Agent注册了http webhook就推送
        for agent_id in mentioned:
            agent = next((a for a in agents if a.get("id") == agent_id), None)
            if not agent:
                continue
            entry = agent.get("entry")
            if entry and entry.get("type") == "http" and entry.get("target"):
                # 异步推送，不阻塞HTTP响应
                t = threading.Thread(
                    target=push_message_to_agent,
                    args=(agent, msg, project),
                    daemon=True
                )
                t.start()

        # ===== 以下为API模式自动触发（需要API配置）=====
        # 检查是否启用自动触发
        if not api_config.get("enabled", False) or not api_config.get("auto_trigger", False):
            return

        # 检查API key是否配置
        if not api_config.get("api_key", ""):
            return

        # T3.4: 防循环检查
        max_depth = api_config.get("max_chain_length", 3)
        allowed, reason = check_and_update_chain(msg, max_depth)
        if not allowed:
            # 如果是因为深度超限，发系统消息提示
            if "深度超过限制" in reason:
                _append_system_message(
                    f"[防循环] {reason}。请用户介入确认后继续。",
                    project
                )
            return

        # 异步触发每个被@的Agent（API模式）
        for agent_id in mentioned:
            agent = next((a for a in agents if a.get("id") == agent_id), None)
            if not agent:
                continue
            agent_name = agent.get("name", agent_id)
            agent_role = agent.get("role", "")
            trigger_msg_id = msg.get("id")

            # 启动后台线程，不阻塞HTTP响应
            t = threading.Thread(
                target=_trigger_agent_worker,
                args=(agent_id, agent_name, agent_role, trigger_msg_id),
                daemon=True
            )
            t.start()

    except Exception as e:
        print(f"[T3.2] 触发Agent失败: {e}")


def _trigger_agent_worker(agent_id, agent_name, agent_role, trigger_msg_id):
    """T3.2: 后台线程worker，调用LLM并把回复写回看板"""
    try:
        # 读取最新看板数据
        board_data = load_board()
        config = load_config()

        # 找到触发消息
        trigger_msg = None
        for m in board_data.get("messages", []):
            if m.get("id") == trigger_msg_id:
                trigger_msg = m
                break

        # 构建prompt
        system_prompt = get_agent_prompt(agent_id, agent_name, agent_role)
        user_prompt = build_context_prompt(board_data, trigger_msg)

        # 调用LLM
        result = call_llm(system_prompt, user_prompt, config_override=config)

        if not result.get("success"):
            error_msg = f"[自动触发失败] {result.get('error', '未知错误')}"
            _append_agent_message(agent_id, agent_name, error_msg)
            return

        # T3.3: 解析回复中的动作并执行
        reply_content = result.get("content", "").strip()
        pure_text, actions_executed = parse_and_execute_actions(reply_content, agent_id)

        # 构建最终回复（纯文本 + 动作执行结果）
        final_reply = pure_text
        if actions_executed:
            action_summary = "\n\n[已执行动作]\n"
            for a in actions_executed:
                if a.get("success"):
                    action_summary += f"- ✅ {a['action']} {a.get('task_id', '')}\n"
                else:
                    action_summary += f"- ❌ {a['action']} {a.get('task_id', '')}: {a.get('error', '')}\n"
            final_reply += action_summary

        if final_reply.strip():
            _append_agent_message(agent_id, agent_name, final_reply.strip())

    except Exception as e:
        print(f"[T3.2] Agent worker异常: {e}")
        try:
            _append_agent_message(agent_id, agent_name, f"[自动触发异常] {str(e)}")
        except:
            pass


def _append_agent_message(agent_id, agent_name, content):
    """把Agent的回复追加到看板消息中"""
    with board_lock:
        data = load_board()
        new_id = max([m["id"] for m in data["messages"]], default=0) + 1
        new_msg = {
            "id": new_id,
            "sender": agent_id,
            "content": content,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "type": "text",
            "auto_triggered": True
        }
        data["messages"].append(new_msg)
        save_board(data)


def _append_system_message(content, project=None):
    """T5.2: 追加系统通知消息（如API降级通知），独立于Agent回复
    project: 项目名。为 None 时跳过写入（多项目模式下 load_board(None)
    会落到服务 cwd 导致 FileNotFoundError 崩溃）"""
    if not project:
        print(f"[T5.2] 跳过系统消息（无 project 上下文）: {content[:60]}")
        return
    with board_lock:
        data = load_board(project)
        new_id = max([m["id"] for m in data["messages"]], default=0) + 1
        new_msg = {
            "id": new_id,
            "sender": "system",
            "content": content,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "type": "system"
        }
        data["messages"].append(new_msg)
        save_board(data, project)


# ===== Agent回复动作解析与执行（T3.3）=====
import re


def parse_and_execute_actions(content, agent_id):
    """T3.3: 解析Agent回复中的动作标记并执行，返回(纯文本, 执行结果列表)

    支持的动作格式：
    - [UPDATE_TASK id=xxx status=xxx progress=xxx assignee=xxx artifact=xxx note=xxx]
    - [CLAIM_TASK id=xxx]
    """
    actions_executed = []
    pure_text = content

    # 解析 [UPDATE_TASK ...]
    update_pattern = r'\[UPDATE_TASK\s+([^\]]+)\]'
    for match in re.finditer(update_pattern, content):
        attrs_str = match.group(1)
        attrs = {}
        for attr_match in re.finditer(r'(\w+)=("[^"]*"|\S+)', attrs_str):
            key = attr_match.group(1)
            value = attr_match.group(2).strip('"')
            attrs[key] = value

        task_id = attrs.get("id", "")
        if task_id:
            result = _execute_update_task(task_id, attrs, agent_id)
            actions_executed.append(result)

    # 解析 [CLAIM_TASK ...]
    claim_pattern = r'\[CLAIM_TASK\s+([^\]]+)\]'
    for match in re.finditer(claim_pattern, content):
        attrs_str = match.group(1)
        attrs = {}
        for attr_match in re.finditer(r'(\w+)=("[^"]*"|\S+)', attrs_str):
            key = attr_match.group(1)
            value = attr_match.group(2).strip('"')
            attrs[key] = value

        task_id = attrs.get("id", "")
        if task_id:
            result = _execute_claim_task(task_id, agent_id)
            actions_executed.append(result)

    # 去掉动作标记，保留纯文本
    pure_text = re.sub(update_pattern, '', pure_text)
    pure_text = re.sub(claim_pattern, '', pure_text)
    pure_text = pure_text.strip()

    return pure_text, actions_executed


def _execute_update_task(task_id, attrs, agent_id):
    """执行UPDATE_TASK动作"""
    try:
        with board_lock:
            data = load_board()
            found = False
            for task in data["tasks"]:
                if task["id"] == task_id:
                    for field in ["status", "progress", "assignee", "artifact", "note", "priority"]:
                        if field in attrs:
                            if field == "progress":
                                try:
                                    task[field] = int(attrs[field])
                                except ValueError:
                                    pass
                            else:
                                task[field] = attrs[field]
                    task["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
                    found = True
                    break
            if found:
                save_board(data)
                return {"action": "UPDATE_TASK", "task_id": task_id, "success": True, "attrs": attrs}
            return {"action": "UPDATE_TASK", "task_id": task_id, "success": False, "error": "任务不存在"}
    except Exception as e:
        return {"action": "UPDATE_TASK", "task_id": task_id, "success": False, "error": str(e)}


def _execute_claim_task(task_id, agent_id):
    """执行CLAIM_TASK动作"""
    try:
        with board_lock:
            data = load_board()
            found = False
            for task in data["tasks"]:
                if task["id"] == task_id:
                    task["assignee"] = agent_id
                    task["status"] = "in_progress"
                    task["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
                    found = True
                    break
            if found:
                save_board(data)
                return {"action": "CLAIM_TASK", "task_id": task_id, "success": True, "assignee": agent_id}
            return {"action": "CLAIM_TASK", "task_id": task_id, "success": False, "error": "任务不存在"}
    except Exception as e:
        return {"action": "CLAIM_TASK", "task_id": task_id, "success": False, "error": str(e)}


# T5.3: API调用日志
API_LOG_FILE = os.path.join(BASE_DIR, "api_calls.log")


def _log_api_call(result):
    """T5.3: 记录每次API调用日志（时间/模型/token/耗时/是否成功）"""
    try:
        entry = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "model": result.get("model", ""),
            "success": result.get("success", False),
            "prompt_tokens": result.get("prompt_tokens", 0),
            "completion_tokens": result.get("completion_tokens", 0),
            "total_tokens": result.get("total_tokens", 0),
            "elapsed": round(result.get("elapsed", 0), 2),
            "error": result.get("error") or "",
            "error_type": result.get("error_type") or ""
        }
        with file_lock(API_LOG_FILE, exclusive=True):
            with open(API_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[T5.3] 写调用日志失败: {e}")


def call_llm(system_prompt, user_message, temperature=0.7, max_tokens=2000, config_override=None):
    """调用LLM API（OpenAI兼容格式，支持DeepSeek/通义/OpenAI等）

    Args:
        system_prompt: 系统提示词
        user_message: 用户消息
        temperature: 温度（0-1）
        max_tokens: 最大生成token数

    Returns:
        dict: {
            "success": bool,
            "content": str (回复内容),
            "prompt_tokens": int,
            "completion_tokens": int,
            "total_tokens": int,
            "error": str (失败时的错误信息),
            "elapsed": float (耗时秒)
        }
    """
    start_time = time.time()
    result = {
        "success": False,
        "content": "",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "error": None,
        "error_type": None,
        "model": "",
        "elapsed": 0
    }

    try:
        config = config_override if config_override is not None else load_config()
        api_config = config.get("api_config", {})

        if not api_config.get("enabled"):
            result["error"] = "API未启用"
            result["elapsed"] = time.time() - start_time
            _log_api_call(result)
            return result

        api_key = api_config.get("api_key", "")
        base_url = api_config.get("base_url", "https://api.openai.com/v1").rstrip("/")
        model = api_config.get("model", "gpt-4o-mini")
        timeout = api_config.get("request_timeout", 60)
        result["model"] = model

        if not api_key:
            result["error"] = "API key未配置"
            result["elapsed"] = time.time() - start_time
            _log_api_call(result)
            return result

        # 构建请求
        url = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "temperature": temperature,
            "max_tokens": max_tokens
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            },
            method="POST"
        )

        # 发送请求
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            response_data = json.loads(resp.read().decode("utf-8"))

        # 解析响应
        result["content"] = response_data["choices"][0]["message"]["content"]
        usage = response_data.get("usage", {})
        result["prompt_tokens"] = usage.get("prompt_tokens", 0)
        result["completion_tokens"] = usage.get("completion_tokens", 0)
        result["total_tokens"] = usage.get("total_tokens", 0)
        result["success"] = True

        # 更新调用统计（测试连接时不落盘）
        _update_api_stats(result, config, save=(config_override is None))

    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8")
        except:
            pass
        # 错误分类
        if e.code in (401, 403):
            error_type = "auth_error"
            result["error"] = f"API密钥错误（HTTP {e.code}）：请检查api_key是否正确"
        elif e.code == 429:
            error_type = "rate_limit"
            result["error"] = f"请求限流（HTTP 429）：API调用频率超限，请稍后重试"
        elif e.code == 404:
            error_type = "not_found"
            result["error"] = f"接口不存在（HTTP 404）：请检查base_url和model是否正确"
        elif e.code >= 500:
            error_type = "server_error"
            result["error"] = f"API服务器错误（HTTP {e.code}）：{error_body[:150]}"
        else:
            error_type = "http_error"
            result["error"] = f"HTTP {e.code}: {error_body[:200]}"
        result["error_type"] = error_type
        _record_api_error(result["error"], error_type, config, save=(config_override is None))
    except urllib.error.URLError as e:
        result["error"] = f"网络连接错误：无法连接到API服务器（{str(e.reason)}），请检查base_url和网络"
        result["error_type"] = "network_error"
        _record_api_error(result["error"], "network_error", config, save=(config_override is None))
    except TimeoutError:
        result["error"] = f"请求超时（{api_config.get('request_timeout', 60)}秒）：API响应太慢，请检查网络或增加超时时间"
        result["error_type"] = "timeout"
        _record_api_error(result["error"], "timeout", config, save=(config_override is None))
    except Exception as e:
        result["error"] = f"未知错误: {str(e)}"
        result["error_type"] = "unknown"
        _record_api_error(result["error"], "unknown", config, save=(config_override is None))

    result["elapsed"] = time.time() - start_time
    _log_api_call(result)
    return result


def _update_api_stats(result, config, save=True):
    """更新API调用统计（内部函数），成功时重置连续失败计数
    save=False 时不写盘（用于测试连接的临时配置）"""
    try:
        api_config = config.get("api_config", {})
        stats = api_config.get("stats", {})
        stats["total_calls"] = stats.get("total_calls", 0) + 1
        stats["total_prompt_tokens"] = stats.get("total_prompt_tokens", 0) + result["prompt_tokens"]
        stats["total_completion_tokens"] = stats.get("total_completion_tokens", 0) + result["completion_tokens"]
        stats["last_call_time"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        stats["last_error"] = None
        stats["last_error_type"] = None
        stats["consecutive_failures"] = 0  # 成功重置
        api_config["stats"] = stats
        config["api_config"] = api_config
        if save:
            save_config(config)
    except Exception as e:
        print(f"更新统计失败: {e}")


def _record_api_error(error_msg, error_type, config, save=True):
    """记录API错误（内部函数），连续失败3次自动降级关闭auto_trigger
    save=False 时不写盘（用于测试连接的临时配置）"""
    try:
        api_config = config.get("api_config", {})
        stats = api_config.get("stats", {})
        stats["last_error"] = error_msg
        stats["last_error_type"] = error_type
        stats["last_call_time"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        stats["consecutive_failures"] = stats.get("consecutive_failures", 0) + 1

        # 连续失败3次自动降级
        if stats["consecutive_failures"] >= 3 and api_config.get("auto_trigger", False):
            api_config["auto_trigger"] = False
            stats["auto_degraded"] = True
            stats["degraded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
            print(f"[警告] API连续失败{stats['consecutive_failures']}次，自动关闭auto_trigger")
            # T5.2: 真实调用降级时在看板写入系统通知（测试连接 save=False 不误报）
            if save:
                try:
                    _append_system_message(
                        f"【API降级通知】API连续失败{stats['consecutive_failures']}次"
                        f"（最近错误：{error_type}：{str(error_msg)[:80]}），"
                        f"已自动关闭自动触发（auto_trigger=False）。看板手动模式不受影响，"
                        f"请在「API 设置」中检查 key/base_url/model 后重新开启自动触发。"
                    )
                except Exception as e:
                    print(f"[T5.2] 写入降级系统通知失败: {e}")

        api_config["stats"] = stats
        config["api_config"] = api_config
        if save:
            save_config(config)
    except Exception as e:
        print(f"记录错误失败: {e}")


class CollabHandler(SimpleHTTPRequestHandler):
    """协作看板请求处理器"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def log_message(self, format, *args):
        """简化日志"""
        print(f"[{time.strftime('%H:%M:%S')}] {args[0]}")

    def _get_project(self):
        """从查询参数获取项目名"""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        project = params.get("project", [None])[0]
        return project

    def do_GET(self):
        """处理 GET 请求"""
        parsed = urlparse(self.path)
        project = self._get_project()

        # API: 项目列表（多项目管理）
        if parsed.path == "/api/projects":
            self._send_json({"projects": list_projects(), "root": PROJECTS_ROOT})
            return

        # API: 获取看板数据
        if parsed.path == "/api/board":
            self._send_json(load_board(project))
            return

        # API: 获取项目配置
        if parsed.path == "/api/config":
            self._send_json(load_config(project))
            return

        # API: 获取新消息（长轮询简化版，直接返回全部）
        if parsed.path == "/api/messages":
            data = load_board(project)
            self._send_json({"messages": data.get("messages", [])})
            return

        # API: Agent 接入状态（T8.1）
        if parsed.path == "/api/agents/status":
            config = load_config(project)
            status = []
            for a in config.get("agents", []):
                status.append({
                    "id": a.get("id"),
                    "name": a.get("name"),
                    "role": a.get("role", ""),
                    "connected": a.get("connected", False),
                    "entry": a.get("entry"),
                    "registered_at": a.get("registered_at"),
                    "last_seen": a.get("last_seen")
                })
            self._send_json({"agents": status})
            return

        # API: Agent 接入引导文本（T9.2）
        if parsed.path == "/api/onboarding":
            config = load_config(project)
            # 支持agent_id参数，生成专属接入引导
            params = parse_qs(parsed.query)
            agent_id = params.get("agent_id", [None])[0]
            onboarding_content = ""
            if project:
                project_path = get_project_path(project)
                onboarding_file = os.path.join(project_path, "AGENT_ONBOARDING.md")
                if os.path.exists(onboarding_file) and not agent_id:
                    # 通用引导：读取文件
                    with open(onboarding_file, "r", encoding="utf-8") as f:
                        onboarding_content = f.read()
                else:
                    # 专属引导（指定agent_id）或旧项目没有引导文件时，动态生成
                    onboarding_content = _build_onboarding_md(
                        project,
                        config.get("project_name", project),
                        config.get("meta", {}).get("project_desc", ""),
                        config.get("workspace", ""),
                        config.get("agents", []),
                        agent_id=agent_id
                    )
            self._send_json({"ok": True, "project": project, "agent_id": agent_id, "content": onboarding_content})
            return

        # API: 获取API状态和统计（T2.4）
        if parsed.path == "/api/api-status":
            config = load_config(project)
            api_config = config.get("api_config", {})
            stats = api_config.get("stats", {})
            self._send_json({
                "enabled": api_config.get("enabled", False),
                "auto_trigger": api_config.get("auto_trigger", False),
                "base_url": api_config.get("base_url", ""),
                "model": api_config.get("model", ""),
                "has_api_key": bool(api_config.get("api_key", "")),
                "consecutive_failures": stats.get("consecutive_failures", 0),
                "total_calls": stats.get("total_calls", 0),
                "total_tokens": stats.get("total_prompt_tokens", 0) + stats.get("total_completion_tokens", 0),
                "last_call_time": stats.get("last_call_time"),
                "last_error": stats.get("last_error"),
                "last_error_type": stats.get("last_error_type"),
                "auto_degraded": stats.get("auto_degraded", False)
            })
            return

        # API: 获取定时调度器状态（T4.1）
        if parsed.path == "/api/schedule-status":
            self._send_json(schedule_manager.get_status())
            return

        # 静态文件 — 默认进入项目管理器首页
        if parsed.path == "/" or parsed.path == "":
            self.path = "/index.html"

        return super().do_GET()

    def do_POST(self):
        """处理 POST 请求"""
        parsed = urlparse(self.path)
        project = self._get_project()

        # API: 创建新项目（多项目管理）
        if parsed.path == "/api/projects":
            self._handle_create_project()
            return

        # API: 更新任务
        if parsed.path.startswith("/api/tasks/"):
            task_id = parsed.path.split("/api/tasks/")[1].strip("/")
            self._handle_update_task(task_id, project)
            return

        # API: 发送消息
        if parsed.path == "/api/messages":
            self._handle_send_message(project)
            return

        # API: 认领任务
        if parsed.path == "/api/claim":
            self._handle_claim_task(project)
            return

        # API: 保存项目配置
        if parsed.path == "/api/config":
            self._handle_save_config(project)
            return

        # API: Agent 入口注册（T8.1）
        if parsed.path == "/api/agents/register":
            self._handle_register_agent(project)
            return

        # API: 测试API连接（T2.4）
        if parsed.path == "/api/test-connection":
            self._handle_test_connection(project)
            return

        # API: 设置定时调度配置（T4.1）
        if parsed.path == "/api/schedule-config":
            self._handle_schedule_config(project)
            return

        self._send_error(404, "Not Found")

    def do_DELETE(self):
        """处理 DELETE 请求"""
        parsed = urlparse(self.path)

        # API: 删除项目
        if parsed.path == "/api/project":
            self._handle_delete_project()
            return

        self._send_error(404, "Not Found")

    def _handle_delete_project(self):
        """删除项目"""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
            req = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError):
            self._send_error(400, "Invalid JSON")
            return

        project_name = req.get("project_name", "").strip()
        scope = req.get("scope", "data_only")  # data_only / all
        confirm_name = req.get("confirm_name", "").strip()

        # 验证
        if not project_name:
            self._send_json({"ok": False, "error": "项目名不能为空"})
            return

        if scope not in ("data_only", "all"):
            self._send_json({"ok": False, "error": "scope 必须是 data_only 或 all"})
            return

        # 安全确认：删除整个项目需要输入项目名确认
        if scope == "all" and confirm_name != project_name:
            self._send_json({"ok": False, "error": "删除整个项目需要输入项目名确认"})
            return

        # 执行删除
        success, message, deleted_files = delete_project(project_name, scope)

        if success:
            self._send_json({
                "ok": True,
                "message": message,
                "project_name": project_name,
                "scope": scope,
                "deleted_files": deleted_files
            })
        else:
            self._send_json({"ok": False, "error": message})

    def _handle_create_project(self):
        """创建新项目"""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
            req = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError):
            self._send_error(400, "Invalid JSON")
            return

        project_name = req.get("project_name", "").strip()
        project_title = req.get("project_title", "").strip()
        description = req.get("description", "").strip()
        agents = req.get("agents", ["user", "planner", "builder"])
        workspace = req.get("workspace", "").strip()
        handoff = req.get("handoff", "").strip()

        success, message, project_path = create_project(
            project_name=project_name,
            agents=agents,
            workspace=workspace,
            handoff=handoff,
            project_title=project_title,
            description=description
        )

        if success:
            self._send_json({
                "ok": True,
                "message": message,
                "project_name": project_name,
                "project_path": project_path,
                "board_url": f"/collab_board_nothing.html?project={project_name}"
            })
        else:
            self._send_json({"ok": False, "error": message})

    def _handle_update_task(self, task_id, project=None):
        """更新任务状态"""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            update = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_error(400, "Invalid JSON")
            return

        with board_lock:
            data = load_board(project)
            found = False
            for task in data["tasks"]:
                if task["id"] == task_id:
                    # 允许更新的字段
                    for field in ["status", "progress", "assignee", "artifact", "note", "priority"]:
                        if field in update:
                            task[field] = update[field]
                    task["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
                    found = True
                    break

            if not found:
                self._send_error(404, f"Task {task_id} not found")
                return

            save_board(data, project)

        # 同时发一条系统消息
        self._append_system_message(
            f"任务 {task_id} 状态更新：{update.get('status', '?')}"
            + (f"（{update.get('progress', '?')}%）" if "progress" in update else ""),
            project
        )

        self._send_json({"ok": True, "task_id": task_id})

    def _handle_send_message(self, project=None):
        """发送聊天消息"""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            msg = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_error(400, "Invalid JSON")
            return

        if "sender" not in msg or "content" not in msg:
            self._send_error(400, "Missing sender or content")
            return

        with board_lock:
            data = load_board(project)
            new_id = max([m["id"] for m in data["messages"]], default=0) + 1
            new_msg = {
                "id": new_id,
                "sender": msg["sender"],
                "content": msg["content"],
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                "type": msg.get("type", "text")
            }
            data["messages"].append(new_msg)
            save_board(data, project)

        # T3.2: 消息写入钩子 — 检测@提及，异步触发对应Agent
        if new_msg.get("type") != "system":
            trigger_agent_if_mentioned(new_msg, data, project)

        self._send_json({"ok": True, "message_id": new_id})

    def _handle_claim_task(self, project=None):
        """认领任务"""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            claim = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_error(400, "Invalid JSON")
            return

        task_id = claim.get("task_id")
        assignee = claim.get("assignee")
        if not task_id or not assignee:
            self._send_error(400, "Missing task_id or assignee")
            return

        with board_lock:
            data = load_board(project)
            found = False
            for task in data["tasks"]:
                if task["id"] == task_id:
                    task["assignee"] = assignee
                    task["status"] = "in_progress"
                    task["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
                    found = True
                    break

            if not found:
                self._send_error(404, f"Task {task_id} not found")
                return

            save_board(data, project)

        agent_name = assignee
        for a in data.get("agents", []):
            if a["id"] == assignee:
                agent_name = a["name"]
                break

        self._append_system_message(f"{agent_name} 认领了任务 {task_id}", project)
        self._send_json({"ok": True, "task_id": task_id, "assignee": assignee})

    def _handle_save_config(self, project=None):
        """保存项目配置"""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            config = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_error(400, "Invalid JSON")
            return

        with config_lock:
            save_config(config, project)

        # 同步更新看板里的 agents 列表
        with board_lock:
            data = load_board(project)
            if "agents" in config:
                data["agents"] = config["agents"]
            if "meta" in config and "project_name" in config["meta"]:
                data["meta"]["project"] = config["meta"]["project_name"]
            save_board(data, project)

        self._send_json({"ok": True, "config_saved": True})

    def _handle_register_agent(self, project=None):
        """T8.1: 注册 Agent 入口"""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            req = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_error(400, "Invalid JSON")
            return

        agent_id = (req.get("agent_id") or "").strip()
        entry = req.get("entry")
        if not agent_id:
            self._send_json({"ok": False, "error": "agent_id 不能为空"})
            return
        if entry is not None and not isinstance(entry, dict):
            self._send_json({"ok": False, "error": "entry 必须是 {type, target} 对象"})
            return
        if isinstance(entry, dict) and entry.get("type") not in ("http", "session"):
            self._send_json({"ok": False, "error": "entry.type 必须是 http 或 session"})
            return

        success, message = register_agent(project, agent_id, entry)
        self._send_json({"ok": success, "message": message, "agent_id": agent_id})

    def _handle_test_connection(self, project=None):
        """测试API连接（T2.4）"""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
            test_config = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError):
            self._send_error(400, "Invalid JSON")
            return

        # 用弹窗传入的配置临时测试（config_override，测试结果不写回磁盘）
        config = load_config(project)
        api_config = config.get("api_config", {})

        # 如果传入了测试配置，临时覆盖
        if test_config.get("api_key"):
            api_config["api_key"] = test_config["api_key"]
        if test_config.get("base_url"):
            api_config["base_url"] = test_config["base_url"]
        if test_config.get("model"):
            api_config["model"] = test_config["model"]

        # 临时启用以便测试
        api_config["enabled"] = True
        config["api_config"] = api_config

        # 用一个简单的prompt测试
        result = call_llm(
            system_prompt="你是一个连接测试助手，请只回复'连接成功'四个字。",
            user_message="请回复连接成功。",
            max_tokens=50,
            config_override=config
        )

        self._send_json({
            "success": result["success"],
            "content": result["content"],
            "error": result["error"],
            "error_type": result.get("error_type"),
            "elapsed": round(result["elapsed"], 2),
            "model": api_config.get("model"),
            "prompt_tokens": result["prompt_tokens"],
            "completion_tokens": result["completion_tokens"]
        })

    def _handle_schedule_config(self, project=None):
        """设置定时调度配置（T4.1）"""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
            update = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError):
            self._send_error(400, "Invalid JSON")
            return

        config = load_config(project)
        schedule = config.get("api_config", {}).get("schedule", {})

        # 允许更新的字段
        if "enabled" in update:
            schedule["enabled"] = bool(update["enabled"])
        if "interval_minutes" in update:
            interval = int(update["interval_minutes"])
            if interval in SCHEDULE_INTERVALS:
                schedule["interval_minutes"] = interval
            else:
                self._send_error(400, f"不支持的频率: {interval}分钟，可选: {list(SCHEDULE_INTERVALS.keys())}")
                return

        config["api_config"]["schedule"] = schedule
        save_config(config, project)

        # 重启调度器以应用新配置
        schedule_manager.restart()

        self._send_json({
            "ok": True,
            "schedule": schedule_manager.get_status()
        })

    def _append_system_message(self, content, project=None):
        """追加系统消息（不带锁版本，调用方需已持锁或单独加锁）"""
        with board_lock:
            data = load_board(project)
            new_id = max([m["id"] for m in data["messages"]], default=0) + 1
            data["messages"].append({
                "id": new_id,
                "sender": "system",
                "content": content,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                "type": "system"
            })
            save_board(data, project)

    def _send_json(self, data, status=200):
        """发送 JSON 响应"""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status, message):
        """发送错误响应"""
        self._send_json({"error": message}, status)


def main():
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print(f"无效端口: {sys.argv[1]}，使用默认 {DEFAULT_PORT}")

    # 检查数据文件（多项目模式下不强制要求默认项目存在）
    if not os.path.exists(BOARD_FILE):
        print(f"警告：找不到默认数据文件 {BOARD_FILE}")
        print("提示：多项目模式下，请通过 ?project=xxx 参数指定项目，或在项目管理页面创建新项目。")
        # 不退出，继续启动（多项目模式）
    else:
        print(f"数据文件: {BOARD_FILE}")

    server = HTTPServer(("0.0.0.0", port), CollabHandler)
    print(f"\n{'='*60}")
    print(f"  多Agent协作看板 · 最小原型")
    print(f"{'='*60}")
    print(f"  地址: http://localhost:{port}")
    print(f"  数据: {BOARD_FILE}")
    print(f"  按 Ctrl+C 停止")
    print(f"{'='*60}\n")

    # T4.1: 启动定时调度器
    schedule_manager.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
        schedule_manager.stop()
        server.server_close()


if __name__ == "__main__":
    main()
