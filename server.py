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
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
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
                    tasks = board.get("tasks", [])
                    msgs = board.get("messages", [])
                    info["project_name"] = board.get("project_info", {}).get("name", name)
                    info["description"] = board.get("project_info", {}).get("description", "")
                    info["task_count"] = len(tasks)
                    info["done_count"] = len([t for t in tasks if t.get("status") == "done"])
                    info["active_count"] = len([t for t in tasks if t.get("status") == "in_progress"])
                    info["blocked_count"] = len([t for t in tasks if t.get("status") == "blocked"])
                    info["todo_count"] = len([t for t in tasks if t.get("status") == "todo"])
                    info["message_count"] = len(msgs)
                    info["agents"] = [a.get("name", a.get("id")) for a in board.get("agents", [])]
                    info["agent_count"] = len(board.get("agents", []))
                    info["progress"] = round(info["done_count"] / info["task_count"] * 100) if info["task_count"] else 0
                    # 最近活动：取最后一条消息时间，否则留空由前端回退到 modified
                    if msgs:
                        info["last_active"] = msgs[-1].get("timestamp", "") or ""
                    else:
                        info["last_active"] = ""
                except Exception:
                    pass
                # 交接/背景文档标记：目录内存在 md 文档
                try:
                    files = os.listdir(project_path)
                    info["has_handoff"] = any(f.lower().endswith(".md") for f in files)
                except Exception:
                    info["has_handoff"] = False
                info["created"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getctime(project_path)))
                info["modified"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(board_file)))
                projects.append(info)
    return projects


def dashboard_stats():
    # Dashboard 聚合统计：跨项目指标 + 项目列表
    projects = list_projects()
    today = time.strftime("%Y-%m-%d")
    total_tasks = sum(p.get("task_count", 0) for p in projects)
    total_done = sum(p.get("done_count", 0) for p in projects)
    total_messages = sum(p.get("message_count", 0) for p in projects)
    total_agents = sum(p.get("agent_count", 0) for p in projects)
    today_messages = 0
    for p in projects:
        board_file = os.path.join(p["path"], "collab_board.json")
        try:
            with open(board_file, "r", encoding="utf-8") as f:
                board = json.load(f)
            today_messages += len([m for m in board.get("messages", [])
                                   if (m.get("timestamp") or "").startswith(today)])
        except Exception:
            pass
    active_projects = sum(1 for p in projects if p.get("active_count", 0) > 0)
    return {
        "project_count": len(projects),
        "active_projects": active_projects,
        "total_tasks": total_tasks,
        "total_done": total_done,
        "completion": round(total_done / total_tasks * 100) if total_tasks else 0,
        "total_messages": total_messages,
        "today_messages": today_messages,
        "total_agents": total_agents,
        "projects": projects,
        "root": PROJECTS_ROOT,
    }


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


def _resolve_workspace(project_path, workspace, mode):
    """解析工作区：
    - mode=='new'：空路径 -> 项目内 workspace/ 子目录；非空路径 -> 创建该目录
    - mode=='existing'：路径必须已存在（目录），否则报错
    - 其他（auto/向后兼容）：空 -> 项目内 workspace/ 子目录；非空 -> 存在则关联、不存在则创建
    返回 (workspace_str, status, message)
    """
    ws = (workspace or "").strip()
    if mode == "existing":
        if not ws:
            return "", "error", "关联已有工作区需要填写路径"
        p = os.path.abspath(os.path.expanduser(ws))
        if not os.path.isdir(p):
            return "", "error", f"路径不存在：{p}（请检查，或改选「新建工作区」）"
        return p, "existing", f"已关联已有工作区：{p}"
    # new / auto：空则默认项目内 workspace/ 子目录
    if not ws:
        p = os.path.join(project_path, "workspace")
    else:
        p = os.path.abspath(os.path.expanduser(ws))
    if os.path.isdir(p):
        return p, "existing", f"已关联已有工作区：{p}"
    try:
        os.makedirs(p, exist_ok=True)
        return p, "created", f"已新建工作区：{p}"
    except OSError as e:
        return ws, "error", f"工作区创建失败：{e}"


def create_project(project_name, agents=None, workspace="", handoff="", project_title="", description="", workspace_mode=""):
    """创建新项目

    Args:
        project_name: 项目文件夹名（唯一标识）
        agents: 接入的Agent列表，支持 ["user", "planner", "builder"] 字符串
                  或自定义 [{"id": "agent_1", "name": "品牌设计师", "role": "视觉/品牌"}]
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
        return False, "项目名不能为空", None, "", ""
    if ".." in project_name or "/" in project_name or "\\" in project_name:
        return False, "项目名不能包含路径分隔符", None, "", ""

    project_path = os.path.join(PROJECTS_ROOT, project_name.strip())
    if os.path.exists(project_path):
        return False, f"项目 '{project_name}' 已存在", None, "", ""

    # 创建项目文件夹
    existed_before = os.path.exists(project_path)
    os.makedirs(project_path, exist_ok=True)

    # 工作区解析（v2.11：新建 / 关联已有 二选一）
    resolved_workspace, ws_status, ws_message = _resolve_workspace(project_path, workspace, workspace_mode)
    if ws_status == "error":
        if not existed_before:
            try: os.rmdir(project_path)  # 回滚刚创建的空目录
            except OSError: pass
        return False, ws_message, None, "", ""

    # 默认Agent定义
    all_agents = {
        "user": {"id": "user", "name": "Zames", "role": "项目主导方/最终决策者", "status": "online"},
        "planner": {"id": "planner", "name": "规划Agent", "role": "任务拆解/方案评审/进度协调/验收", "status": "online"},
        "builder": {"id": "builder", "name": "执行Agent", "role": "前端开发/后端开发/测试/文档", "status": "online"}
    }

    # 选择接入的Agent（兼容字符串列表 ["user","planner"] 与自定义 dict 列表
    # [{"id":"agent_1","name":"品牌设计师","role":"视觉/品牌"}, ...]）
    if agents is None:
        agents = ["user", "planner", "builder"]
    selected_agents = []
    seen_ids = set()
    for a in agents:
        if isinstance(a, dict):
            aid = str(a.get("id", "")).strip()
            if not aid or aid in seen_ids:
                continue
            seen_ids.add(aid)
            selected_agents.append({
                "id": aid,
                "name": str(a.get("name", aid)).strip() or aid,
                "role": str(a.get("role", "")).strip(),
                "status": "online",
            })
        else:
            aid = str(a).strip()
            if aid in all_agents and aid not in seen_ids:
                seen_ids.add(aid)
                selected_agents.append(all_agents[aid])
    # 用户必须在且置顶
    user_agent = None
    for a in selected_agents:
        if a["id"] == "user":
            user_agent = a
            break
    if user_agent is None:
        user_agent = dict(all_agents["user"])
        selected_agents.insert(0, user_agent)
    elif selected_agents[0]["id"] != "user":
        selected_agents.remove(user_agent)
        selected_agents.insert(0, user_agent)

    # 创建看板数据
    now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    board_data = {
        "project_info": {
            "name": project_title or project_name,
            "description": description or "",
            "created": now,
            "version": "1.0",
            "workspace": resolved_workspace,
            "workspace_status": ws_status
        },
        "agents": selected_agents,
        "tasks": [],
        "messages": [
            {
                "id": 1,
                "sender": "system",
                "content": f"项目 '{project_title or project_name}' 已创建。\n\n接入Agent: {', '.join([a['name'] for a in selected_agents])}\n工作区: {resolved_workspace or '未指定'}（{ws_status}）\n\n{handoff or '暂无交接文件'}",
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
        "workspace": resolved_workspace,
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

    return True, "项目创建成功", project_path, resolved_workspace, ws_status


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
            data = json.load(f)
    # v2.14：协作协议（工作/收工）兜底——老项目默认工作态
    try:
        if not isinstance(data.get("protocol"), dict):
            data["protocol"] = {"mode": "working", "updated_by": "system", "updated_at": "", "exemptions": []}
    except Exception:
        pass
    # v2.11：project_info 补工作区（旧项目 board 里没有，从 config 补；兼容 str/dict）
    try:
        pi = data.setdefault("project_info", {})
        if not pi.get("workspace"):
            cfg = load_config(project)
            ws = cfg.get("workspace", "")
            if isinstance(ws, dict):
                ws = ws.get("path", "")
            pi["workspace"] = ws
    except Exception:
        pass
    return data



_LAST_GIT_COMMIT = {}

def _auto_git_commit(board_file):
    """v2.14.2: 看板保存后自动 git 提交，每次写盘都留历史快照。
    若目录未纳入 git（无仓库/文件不在仓库内）则静默跳过。"""
    try:
        if not board_file:
            return
        abs_f = os.path.abspath(board_file)
        root = os.path.abspath(PROJECTS_ROOT)
        if not abs_f.startswith(root + os.sep):
            return
        rel = os.path.relpath(abs_f, root)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        import subprocess as _sp
        _sp.run(["git", "-C", root, "add", "--", rel], capture_output=True, timeout=15)
        _sp.run(["git", "-C", root, "commit", "-m", "board: %s %s" % (rel, now)],
                capture_output=True, timeout=15)
    except Exception:
        pass


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
    _auto_git_commit(board_file)   # v2.14.2：保存后自动留版本快照（防直接编辑覆盖丢数据）
    return board_file


def _normalize_config_agents(data):
    """兼容旧格式：project_config.json 的 agents 可能是字符串列表（v1.x 创建的项目），
    归一化为 dict 列表（内存级，不写回），避免下游 a.get() 崩溃。"""
    if not isinstance(data, dict):
        return
    agents = data.get("agents")
    if not isinstance(agents, list):
        return
    normalized = []
    for a in agents:
        if isinstance(a, str):
            normalized.append({"id": a, "name": a, "role": "", "connected": False})
        else:
            normalized.append(a)
    data["agents"] = normalized


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
    _normalize_config_agents(data)
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
def update_org_relations(project, relations):
    """v2.9: 批量设置组织汇报关系（reports_to）

    relations: [{"agent_id": "builder_1", "reports_to": "planner"}, ...]
    reports_to 为 None/"" 表示取消上级（回到扁平）。
    写 board_data.agents（组织图读 board），并同步 config。
    """
    if not relations:
        return False, "relations 不能为空"
    data = load_board(project)
    agents = data.get("agents", [])
    by_id = {a.get("id"): a for a in agents}
    changed = []
    for r in relations:
        aid = str(r.get("agent_id", "")).strip()
        rid = (r.get("reports_to") or "").strip() or None
        if not aid or aid not in by_id:
            continue
        if aid == rid:
            continue  # 不能向自己汇报
        by_id[aid]["reports_to"] = rid
        changed.append(aid)
    data["agents"] = agents
    save_board(data, project)
    # 同步 config
    try:
        config = load_config(project)
        c_agents = {a.get("id"): a for a in config.get("agents", [])}
        for aid in changed:
            if aid in c_agents:
                c_agents[aid]["reports_to"] = by_id[aid].get("reports_to")
        config["agents"] = list(c_agents.values())
        save_config(config, project)
    except Exception as e:
        print(f"[v2.9] 同步 reports_to 到 config 失败: {e}")
    return True, f"已更新 {len(changed)} 个 Agent 的汇报关系"


def add_org_position(project, agent_id, name, role):
    """v2.9: 组织图「加位子」——注册即创建（复用 register_agent）"""
    return register_agent(project, agent_id, {"type": "session", "target": "组织图手动添加"}, name=name, role=role)


def register_agent(project, agent_id, entry, name=None, role=None):
    """T8.1: 注册 Agent 入口，标记接通状态

    entry: {"type": "http"|"session", "target": "url或会话描述"}
    name/role: v2.9 可选，自定义名字与身份（"加位子"用）
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
            if name:
                agent["name"] = name
            if role is not None:
                agent["role"] = role
            found = True
            break
    if not found:
        # 注册即创建（Agent 首次接入自动加入列表）
        agents.append({
            "id": agent_id,
            "name": name or agent_id,
            "role": role or "",
            "color": "#a1a1a6",
            "connected": True,
            "entry": entry,
            "registered_at": now,
            "last_seen": now,
            "reports_to": None
        })
    config["agents"] = agents
    save_config(config, project)

    # 同步 agents 到看板：按 id 合并，保留看板里已有的成员（v2.9 修复覆盖丢失）
    try:
        data = load_board(project)
        merged = {a.get("id"): a for a in data.get("agents", []) if a.get("id")}
        for a in agents:
            if a.get("id"):
                merged[a["id"]] = a
        data["agents"] = list(merged.values())
        save_board(data, project)
    except Exception as e:
        print(f"[T8.1] 同步 agents 到看板失败: {e}")

    return True, f"Agent {agent_id} 注册成功"


def build_guide(project, agent_id, name, role):
    """v2.13: 生成引导词（供引导词接入的 Agent 粘贴，明确其身份认知）"""
    role_txt = role or "成员"
    return f"""你是「{project}」项目班子里的一名成员。

【你的身份】
- 名字：{name}
- 身份 ID：{agent_id}（这是你的唯一标识，全程不要改动）
- 职责：{role_txt}

【协作方式】
这个项目使用「协同看板」工作：任务、消息、决策都记录在项目目录下的 collab_board.json（或通过 /api/board?project={project} 读取）。你的所有工作都在看板上留痕。

【数据安全 · 看板 JSON 红线】
- collab_board.json 的 agents 数组是班子名单，**禁止直接编辑或覆盖**；增减成员一律走组织视图「拉人进班子」或 /api/agents/* 接口。
- 如确需直接编辑看板 JSON：必须先 GET /api/board 拉取最新内容，保存时确保 agents 数组与拉取时完全一致（只允许追加 messages / 修改 tasks 字段）；任何覆盖保存都可能把别人刚加进来的成员写丢。

【协作协议 · 工作态/收工态】
- 看板顶层 protocol.mode 是项目当前协作状态：
  · working（工作态）：认领任务后，每 30 分钟自动检查任务看板对齐一次——有任务就开工、有交付就标记待验收、有验收就收尾；被 @ 的消息及时响应。
  · off（收工态）：停止主动轮询和认领新任务，但保留被动响应——被 @ 或收到新指派时仍会回复处理。
- 如果你在 protocol.exemptions 名单里，即使项目处于收工态，也按工作态执行。

【工作协议】
1. 开始工作前，先读取看板，了解项目背景、任务清单和你被分配/可认领的任务。
2. 领取任务：找到分配给你（assignee 含 {agent_id}）或适合你职责的任务，用认领动作领取，并在回复里确认身份：「我是{name}（{agent_id}）」。
3. 执行中持续更新任务状态（进行中/进度百分比），遇到阻塞及时在消息里说明并 @ 相关成员。
4. 完成后把任务标记为待验收，留言交付说明；需要沉淀结论时写入决策记录。
5. 定期检查看板更新（建议 2~5 分钟轮询一次），响应 @ 你的消息和新的任务指派。

记住：你是 {name}，身份 ID {agent_id}，职责是 {role_txt}。"""


def invite_agent(project, agent_id, name, role="", entry_type="guide", entry_target=""):
    """v2.13: 拉人进班子 + 配置接入方式

    entry_type:
      - guide: 引导词接入（生成引导文案，用户复制到目标 Agent 粘贴激活）
      - api:   HTTP API 接入（entry.target 为接口地址/轮询地址）
      - cli:   CLI 接入（tools/agentnexus）
      - dsh:   终端托管（进程托管：工具自动在本机拉起终端 Agent 进程，直接在组织视图卡片对话）
      - manual: 仅添加身份（原「加位子」行为，不产生接入配置）
    返回 (success, message, guide)
    """
    entry_type = (entry_type or "manual").strip().lower()
    if entry_type not in ("guide", "api", "cli", "dsh", "manual"):
        return False, "entry_type 必须是 guide/api/cli/dsh/manual", ""
    if entry_type == "api":
        entry = {"type": "http", "target": (entry_target or "").strip() or "http://localhost:8766"}
        guide = f"""Agent 接入信息（API 模式）
项目：{project}    身份：{name}（{role or '成员'}）    id：{agent_id}

读取看板：  GET /api/board?project={project}
发消息：    POST /api/messages?project={project}   body: {{"sender": "{agent_id}", "content": "..."}}
领取任务：  POST /api/claim?project={project}     body: {{"task_id": "Txx", "assignee": "{agent_id}"}}
更新任务：  POST /api/tasks/{{task_id}}?project={project}  body: {{"status": "doing", "progress": 40}}
沉淀决策：  POST /api/decisions?project={project}  body: {{"title": "...", "decision": "..."}}

协议：建议每 2~5 分钟轮询一次看板，响应 @ 你的消息和任务指派。"""
    elif entry_type == "cli":
        entry = {"type": "cli", "target": "tools/agentnexus"}
        base = build_guide(project, agent_id, name, role)
        guide = base + f"""

【你的操作方式 · CLI（agentnexus 命令）】
你不是聊天界面 Agent，而是终端 Agent——通过 agentnexus 命令行读写看板（身份保存在 ~/.agentnexus.json）：
首次接入：  agentnexus onboard --project "{project}" --agent {agent_id} --name "{name}" --role "{role or '成员'}"
常用命令：
  agentnexus status --project "{project}"              # 项目概览（含 @ 你的消息）
  agentnexus my-tasks --project "{project}"            # 我的任务
  agentnexus claim --project "{project}" --task Txx    # 认领任务（in_progress）
  agentnexus update --project "{project}" --task Txx --status in_progress --progress 40
  agentnexus send --project "{project}" --to planner --msg "你好"   # 发消息（@planner）
  agentnexus conclude --project "{project}" --title "决策标题" --decision "内容"

轮询对齐：按上方【协作协议·工作态/收工态】执行——工作态下每 30 分钟用 agentnexus status 检查 @ 消息与任务指派；收工态停止主动检查。"""
    elif entry_type == "dsh":
        entry = {"type": "dsh", "target": "进程托管（本机 dsh）"}
        guide = f"""Agent 接入信息（终端托管 · 进程托管模式）
项目：{project}    身份：{name}（{role or '成员'}）    id：{agent_id}

你不用复制粘贴任何东西：本工具会在你本机直接拉起终端 Agent（dsh）进程，自动生成专属身份配置。
接入后到组织视图点开这个人的卡片，右下角对话框直接发指令，Agent 会实时回复并操作看板（认领任务 / 更新状态 / 发消息 / 沉淀决策）。

协作协议见【协作协议·工作态/收工态】——工作态下每 30 分钟检查 @ 消息与任务指派；收工态停止主动检查。"""
    elif entry_type == "manual":
        entry = {"type": "session", "target": "组织图手动添加"}
        guide = ""
    else:  # guide
        entry = {"type": "session", "target": "引导词接入"}
        guide = build_guide(project, agent_id, name, role)

    # 写入 agent（复用 register_agent 合并逻辑，并携带 guide）
    success, message = register_agent(project, agent_id, entry, name=name, role=role)
    if not success:
        return False, message, ""
    if entry_type == "dsh":
        # 进程托管：立即生成成员专属 dsh profile（身份/协作协议替换自模板）
        _dsh_ensure_profile(project, agent_id, name, role)
    # 把引导词存进 agent 记录（board + config）
    try:
        board = load_board(project)
        for a in board.get("agents", []):
            if a.get("id") == agent_id:
                a["guide"] = guide
                a["entry_type"] = entry_type
                break
        save_board(board, project)
        cfg = load_config(project)
        for a in cfg.get("agents", []):
            if isinstance(a, dict) and a.get("id") == agent_id:
                a["guide"] = guide
                a["entry_type"] = entry_type
                break
        save_config(cfg, project)
    except Exception:
        pass
    return True, f"已拉入 {name}（{entry_type} 接入）", guide


def update_agent_profile(project, agent_id, name=None, role=None):
    """v2.12+：成员改名 / 改身份——只更新 name/role，不动 entry/connected 接入状态

    v2.13：变更后发系统消息留痕（帮助 Planner 与协作成员同步认知）；
    返回 changed 明细与 guide（若该成员是引导词接入，改身份后需重新生成引导词）。"""
    board = load_board(project)
    found = None
    for a in board.get("agents", []):
        if a.get("id") == agent_id:
            found = a
            break
    if not found:
        return False, "成员不存在", {}
    changed = []
    if name and name != found.get("name"):
        old = found.get("name") or agent_id
        found["name"] = name
        changed.append(f"改名：{old} → {name}")
    if role is not None and role != found.get("role"):
        old = found.get("role") or "未设定身份"
        found["role"] = role
        changed.append(f"身份变更：{old} → {role}")
    if changed:
        now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
        board.setdefault("messages", []).append({
            "id": max([m.get("id", 0) for m in board.get("messages", [])], default=0) + 1,
            "sender": "system",
            "content": f"成员 {agent_id} {'；'.join(changed)}（身份 ID 不变，已派任务不受影响）",
            "timestamp": now,
            "type": "system"
        })
    save_board(board, project)
    # v2.16：终端托管成员改名/改身份后重建专属 profile（删除旧目录重新生成）
    entry_t = (found.get("entry") or {}).get("type", "")
    if changed and (entry_t == "dsh" or agent_id == "terminal"):
        import shutil as _sh2, os as _os2
        prof_dir = _dsh_member_profile_dir(project, agent_id)
        if _os2.path.isdir(prof_dir):
            try:
                _sh2.rmtree(prof_dir)
            except Exception:
                pass
        _dsh_ensure_profile(project, agent_id, found.get("name"), found.get("role"))
    # 同步 config
    try:
        cfg = load_config(project)
        for a in cfg.get("agents", []):
            if isinstance(a, dict) and a.get("id") == agent_id:
                if name:
                    a["name"] = name
                if role is not None:
                    a["role"] = role
                break
        save_config(cfg, project)
    except Exception:
        pass
    is_guide = (found.get("entry_type") == "guide") or (isinstance(found.get("entry"), dict) and found.get("entry", {}).get("target") == "引导词接入")
    guide = ""
    if is_guide and (changed or name or role is not None):
        guide = build_guide(project, agent_id, found.get("name") or agent_id, found.get("role") or "")
    return True, "已更新", {"changed": changed, "guide": guide, "is_guide": is_guide}


def remove_agent(project, agent_id):
    """v2.12: 移除成员——从看板/config 删除；名下未完成任务转为无人认领；引用它的汇报关系清空"""
    if not agent_id or agent_id == "user":
        return False, "项目主导方不能被移除"
    board = load_board(project)
    if not any(a.get("id") == agent_id for a in board.get("agents", [])):
        return False, "成员不存在"
    now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    # 名下未完成任务：清空 assignee + 备注
    orphan = 0
    for t in board.get("tasks", []):
        if t.get("assignee") == agent_id:
            t["assignee"] = ""
            t["note"] = (t.get("note") or "") + f" [原负责人 {agent_id} 已移除]"
            if t.get("status") not in ("done", "blocked"):
                orphan += 1
    # 引用它的汇报关系清空（其下属提升为顶层）
    for a in board.get("agents", []):
        if a.get("reports_to") == agent_id:
            a["reports_to"] = ""
    # 从成员列表移除
    board["agents"] = [a for a in board.get("agents", []) if a.get("id") != agent_id]
    # 系统消息
    board.setdefault("messages", []).append({
        "id": max([m.get("id", 0) for m in board.get("messages", [])], default=0) + 1,
        "sender": "system",
        "content": f"成员 {agent_id} 已从班子移除" + (f"，{orphan} 个未完成任务已转为无人认领" if orphan else ""),
        "timestamp": now,
        "type": "system"
    })
    save_board(board, project)
    # config 同步删除
    try:
        cfg = load_config(project)
        cfg_agents = cfg.get("agents", [])
        if isinstance(cfg_agents, list):
            cfg["agents"] = [a for a in cfg_agents if not (isinstance(a, dict) and a.get("id") == agent_id) and a != agent_id]
        save_config(cfg, project)
    except Exception:
        pass
    return True, f"已移除成员 {agent_id}" + (f"，{orphan} 个任务转为无人认领" if orphan else "")



def _looks_like_taskplan_table(line):
    """v2.13.7: 识别『| # | 任务 | 负责 | 优先级 | 说明 |』待办清单表格头"""
    ln = line.lstrip()
    return ln.startswith("|") and "任务" in ln and "负责" in ln and "优先级" in ln and "说明" in ln


_ROLE_HINTS = {
    "美工": ["美术", "贴图", "视觉"],
    "UI": ["UI", "交互", "界面"],
    "音频": ["音频", "音效", "音乐", "配音"],
    "玩法": ["玩法", "关卡"],
    "数值": ["数值", "平衡", "数据策划"],
    "试玩": ["试玩", "反馈"],
    "引擎": ["引擎", "逻辑", "实现", "规划"],
}


def _map_assignee_from_role(board, role_text):
    """中文角色 → agent_id（按 agent 名字关键词匹配；无命中返回空=无人认领）"""
    if not role_text:
        return ""
    for role_kw, hints in _ROLE_HINTS.items():
        if role_kw in role_text:
            for a in board.get("agents", []):
                name = a.get("name") or ""
                if any(h in name for h in hints):
                    return a.get("id")
    return ""


def sync_tasks_from_taskplan(data, sender, content):
    """v2.13.7: 检测 Agent 广播中的 TaskPlan 待办清单表格，把缺失任务自动写入看板 todo 列。
    就地修改 data；返回 (created, skipped)。"""
    if not content or sender == "user":
        return 0, 0
    lines = content.split("\n")
    table_idx = None
    for i, ln in enumerate(lines):
        if _looks_like_taskplan_table(ln):
            table_idx = i
            break
    if table_idx is None:
        return 0, 0
    existing = {str(t.get("id")): t for t in data.get("tasks", [])}
    created = skipped = 0
    now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    for ln in lines[table_idx + 1:]:
        stripped = ln.strip()
        if not stripped.startswith("|"):
            break
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 4:
            continue
        tid, title = cells[0], cells[1]
        if not tid or not any(ch.isalnum() for ch in tid):
            continue
        if str(tid) in existing:
            skipped += 1
            continue
        role_text = cells[2] if len(cells) > 2 else ""
        pri_text = cells[3] if len(cells) > 3 else ""
        data.setdefault("tasks", []).append({
            "id": tid,
            "title": title,
            "assignee": _map_assignee_from_role(data, role_text),
            "status": "todo",
            "progress": 0,
            "priority": {"P1": "high", "P2": "medium", "P3": "low"}.get(pri_text, "medium"),
            "note": f"自动同步自 TaskPlan 待办清单（{now[:10]}）",
            "updated": now,
        })
        existing[str(tid)] = True
        created += 1
    if created:
        data.setdefault("messages", []).append({
            "id": max([int(m.get("id", 0)) for m in data.get("messages", []) if str(m.get("id", 0)).isdigit()], default=0) + 1,
            "sender": "system",
            "content": f"已从 TaskPlan 待办清单自动同步 {created} 个新待办任务"
                       + (f"，{skipped} 个已存在未改动" if skipped else ""),
            "timestamp": now,
            "type": "system"
        })
    return created, skipped




def set_protocol(project, mode, by="user", exemptions=None):
    """v2.14: 项目级协作协议——工作态(working)/收工态(off)。
    收工态下 Agent 暂停主动轮询与认领，但保留被动响应；exemptions 中成员在收工态仍按工作态对待。
    更新后发系统消息广播给全员。"""
    mode = (mode or "working").strip().lower()
    if mode not in ("working", "off"):
        return False, "mode 必须是 working/off"
    board = load_board(project)
    proto = board.setdefault("protocol", {})
    if not isinstance(proto, dict):
        proto = {}
    prev = proto.get("mode", "working")
    exempt = [x for x in (exemptions or []) if isinstance(x, str) and x]
    now = time.strftime("%Y-%m-%dT%H:%M:%S+08:00")
    proto["mode"] = mode
    proto["updated_by"] = by
    proto["updated_at"] = now
    proto["exemptions"] = exempt
    board["protocol"] = proto
    # 豁免成员名单（用于消息）
    names = []
    for x in exempt:
        for a in board.get("agents", []):
            if a.get("id") == x:
                names.append(a.get("name") or x)
                break
        else:
            names.append(x)
    if mode == "working":
        content = "⚡ 已进入工作状态：任务板已同步，认领任务后每 30 分钟对齐看板（有活认领、有交付交付、有验收验收）。" + (f" 豁免名单已清空。" if not exempt else f" 豁免继续工作态：{'、'.join(names)}。")
    else:
        content = "🌙 已收工：全员停止主动轮询与认领，保留被动响应（被 @ 仍会回复）。" + (f" 豁免继续工作态：{'、'.join(names)}。" if exempt else "")
    board.setdefault("messages", []).append({
        "id": max([int(m.get("id", 0)) for m in board.get("messages", []) if str(m.get("id", 0)).isdigit()], default=0) + 1,
        "sender": "system",
        "content": content,
        "timestamp": now,
        "type": "system"
    })
    save_board(board, project)
    return True, content


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
        new_id = max([int(m["id"]) for m in data["messages"] if str(m.get("id", "")).isdigit()], default=0) + 1
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
        new_id = max([int(m["id"]) for m in data["messages"] if str(m.get("id", "")).isdigit()], default=0) + 1
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
                if str(task["id"]) == str(task_id):
                    for field in ["status", "progress", "assignee", "artifact", "note", "priority", "handoff"]:
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
                if str(task["id"]) == str(task_id):
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



# ================= dsh 进程托管（v2.15：终端 Agent 进程接入） =================
def _dsh_find_node():
    """探测可用的 node 可执行文件：优先显式指定，其次真实系统常见路径，回退沙箱 node"""
    import os as _os, shutil as _sh
    cands = [
        _os.environ.get("DSH_NODE", ""),
        "/usr/local/bin/node",
        "/opt/homebrew/bin/node",
        "/Users/zames/Library/Application Support/DoubaoWork/sandbox_runtime/bases/98670218a5f0d8bc9b9ebf3f70881304/bin/node",
        "/Users/zames/.local/bin/node",
        "/Users/zames/.bun/bin/node",
    ]
    for c in cands:
        if c and _os.path.isfile(c) and _os.access(c, _os.X_OK):
            return c
    return "node"  # 最后尝试 PATH

DSH_NODE = _dsh_find_node()
DSH_CLI = "/Users/zames/deepseek-harness/apps/cli/lib/bin.js"
DSH_PROFILE = "sanguo-dsh"          # 三国指挥官专属 profile（锁 DeepSeek Flash + 看板协同指令）
DSH_AGENT_ID = "terminal"           # 该会话在看板中的身份 ID
def _dsh_log(msg):
    try:
        with open("/tmp/dsh_server.log", "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
    except Exception:
        pass

DSH_MODEL_WHITELIST = {"space-bunny-free", "deepseek-flash", "deepseek-v4-flash", "deepseek-v4.1-flash",
                       "deepseek-v4-flash-vision", "deepseek-v4-flash-vision-experience"}

DSH_DEFAULT_MODEL = "space-bunny-free"
DSH_MODEL_LABELS = {
    "space-bunny-free": "Space Bunny（限时免费）",
    "deepseek-flash": "DeepSeek Flash（旧别名）",
    "deepseek-v4-flash": "DeepSeek V4 Flash",
    "deepseek-v4.1-flash": "DeepSeek V4.1 Flash",
    "deepseek-v4-flash-vision": "DeepSeek V4 Flash Vision",
    "deepseek-v4-flash-vision-experience": "DeepSeek V4 Flash Vision Experience",
}


DSH_PROFILE_TEMPLATE = "sanguo-dsh"   # profile 模板：providers/模型/协议段复制自此，身份段按成员替换

def _dsh_safe(s):
    """目录安全化：非字母数字转下划线"""
    return re.sub(r"[^\w\-]", "_", str(s or "default"))

def _dsh_member_profile_dir(project, agent_id):
    """成员专属 profile 目录：~/.dsh/profiles/dsh-<project>-<agent>/"""
    import os as _os
    name = "dsh-" + _dsh_safe(project) + "-" + _dsh_safe(agent_id)
    return _os.path.expanduser("~/.dsh/profiles/" + name)

def _dsh_ensure_profile(project, agent_id, agent_name=None, agent_role=None):
    """为托管成员生成专属 dsh profile（模板复制 + 身份段替换）。
    目录已存在则直接返回（成员改名时由 update_agent_profile 触发重建）。"""
    import os as _os, shutil as _sh
    tpl_dir = _os.path.expanduser("~/.dsh/profiles/" + DSH_PROFILE_TEMPLATE)
    tpl_patch = _os.path.join(tpl_dir, "cordis.patch.yml")
    if not _os.path.isfile(tpl_patch):
        _dsh_log("profile 模板缺失: %s" % tpl_patch)
        return None
    tgt_dir = _dsh_member_profile_dir(project, agent_id)
    tgt_patch = _os.path.join(tgt_dir, "cordis.patch.yml")
    if _os.path.isfile(tgt_patch):
        # v2.19：身份段升级检测——旧版身份句（模板占位符未注入，说明还是 v2.18 及以前生成的）→ 重建
        try:
            tgt_body0 = open(tgt_patch, encoding="utf-8").read()
            if "{PROJECT}" not in tgt_body0 and "身份 ID：terminal" in tgt_body0:
                _dsh_log("成员 profile 身份段升级，重建: %s" % tgt_dir)
                _sh.rmtree(tgt_dir)
                return _dsh_ensure_profile(project, agent_id, agent_name, agent_role)
        except Exception as e:
            _dsh_log("身份段检测异常 %s: %s" % (tgt_dir, e))
        # v2.18：模板若已含审批桥段而旧成员 profile 没有，则自动同步（否则权限请求无应答通道会秒退）
        try:
            tpl_body = open(tpl_patch, encoding="utf-8").read()
            tgt_body = open(tgt_patch, encoding="utf-8").read()
            if "agentnexus-approval-bridge" not in tgt_body:
                idx = tpl_body.find("# AgentNexus 审批桥")
                if idx >= 0:
                    seg = tpl_body[idx:].rstrip("\n")
                    open(tgt_patch, "a", encoding="utf-8").write("\n\n" + seg + "\n")
                    _dsh_log("成员 profile 同步审批桥段: %s" % tgt_dir)
        except Exception as e:
            _dsh_log("成员 profile 同步失败 %s: %s" % (tgt_dir, e))
        # v2.18：同步审批桥插件链接到成员 profile 的 node_modules（缺失则插件加载失败、进程秒退）
        try:
            pkgs_dir = _os.path.join(tgt_dir, "node_modules", "@deepseek-ai")
            _os.makedirs(pkgs_dir, exist_ok=True)
            link_dst = _os.path.join(pkgs_dir, "dsh-agentnexus-approval-bridge")
            if not _os.path.islink(link_dst):
                _os.symlink("/Users/zames/Desktop/Project/agent-collab-dev/tools/approval-bridge", link_dst)
                _dsh_log("成员 profile 同步审批桥插件链接: %s" % tgt_dir)
        except Exception as e:
            _dsh_log("成员 profile 插件链接同步失败 %s: %s" % (tgt_dir, e))
        return tgt_dir
    try:
        _os.makedirs(tgt_dir, exist_ok=True)
        # 复制模板全部文件（cordis.patch.yml / cordis.yml / package.json 等）
        for fn in _os.listdir(tpl_dir):
            src = _os.path.join(tpl_dir, fn)
            if _os.path.isfile(src):
                _sh.copy2(src, _os.path.join(tgt_dir, fn))
        body = open(tgt_patch, encoding="utf-8").read()
        name = agent_name or agent_id
        role = agent_role or "终端执行 Agent"
        # v2.19：占位符替换（模板身份段已改为 {PROJECT}/{NAME}/{ID}/{ROLE} 占位符，不再依赖精确中文句子匹配）
        body = body.replace("{PROJECT}", project)
        body = body.replace("{NAME}", name)
        body = body.replace("{ID}", agent_id)
        body = body.replace("{ROLE}", role)
        body = body.replace('--project "三国指挥官"', '--project "%s"' % project)
        body = body.replace("--name terminal", "--name %s" % agent_id)
        open(tgt_patch, "w", encoding="utf-8").write(body)
        # v2.18：审批桥插件链接（模板 node_modules 目录不会被文件复制带过来）
        try:
            pkgs_dir = _os.path.join(tgt_dir, "node_modules", "@deepseek-ai")
            _os.makedirs(pkgs_dir, exist_ok=True)
            _os.symlink("/Users/zames/Desktop/Project/agent-collab-dev/tools/approval-bridge",
                        _os.path.join(pkgs_dir, "dsh-agentnexus-approval-bridge"))
        except Exception as e:
            _dsh_log("成员 profile 插件链接失败 %s: %s" % (tgt_dir, e))
        _dsh_log("生成成员 profile: %s" % tgt_dir)
        return tgt_dir
    except Exception as e:
        _dsh_log("生成成员 profile 失败 %s: %s" % (tgt_dir, e))
        return None

def _dsh_profile_name(project, agent_id):
    """返回 --profile 使用的 profile 名（目录名）"""
    d = _dsh_member_profile_dir(project, agent_id)
    return "dsh-" + _dsh_safe(project) + "-" + _dsh_safe(agent_id) if d else DSH_PROFILE_TEMPLATE

# ---- v2.17：聊天历史持久化（按成员，项目目录 dsh_chat/，随项目纳入版本管理） ----
_HISTORY_MAX = 600
def _dsh_history_path(project, agent_id):
    """历史文件：<PROJECTS_ROOT>/<项目>/dsh_chat/<agent>.json"""
    import os as _os
    d = _os.path.join(PROJECTS_ROOT, project, "dsh_chat")
    return _os.path.join(d, _dsh_safe(agent_id) + ".json")

def _dsh_load_history(project, agent_id):
    try:
        with open(_dsh_history_path(project, agent_id), encoding="utf-8") as f:
            h = json.load(f)
        return h if isinstance(h, list) else []
    except Exception:
        return []

def _dsh_append_history(project, agent_id, entries):
    """原子追加历史（读-追加-写回，上限 _HISTORY_MAX 条）"""
    import os as _os
    p = _dsh_history_path(project, agent_id)
    try:
        h = _dsh_load_history(project, agent_id)
        h.extend(entries)
        if len(h) > _HISTORY_MAX:
            h = h[-_HISTORY_MAX:]
        _os.makedirs(_os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(h, f, ensure_ascii=False, indent=1)
        _os.replace(tmp, p)
    except Exception as e:
        _dsh_log("历史写入失败: %s" % e)

def _dsh_clear_history(project, agent_id):
    import os as _os
    try:
        p = _dsh_history_path(project, agent_id)
        if _os.path.exists(p):
            _os.remove(p)
        return True
    except Exception:
        return False

def _dsh_member_cfg(project, agent_id):
    """读成员会话配置（project_config.json dsh.members.<agent_id>；terminal 兼容回退 dsh 根键）"""
    cfg = _dsh_load_cfg(project)
    dsh = cfg.get("dsh") or {}
    members = dsh.get("members") or {}
    if agent_id in members and isinstance(members[agent_id], dict):
        return members[agent_id]
    if agent_id == "terminal":
        # 兼容 v2.15：三国 terminal 的会话曾存 dsh 根键
        legacy = {k: v for k, v in dsh.items() if k != "members" and k != "model"}
        if legacy:
            return legacy
    return {}

def _dsh_save_member_cfg(project, agent_id, member):
    """写成员会话配置；terminal 写 members.terminal（新机制）"""
    cfg = _dsh_load_cfg(project)
    dsh = cfg.get("dsh") or {}
    members = dsh.get("members")
    if not isinstance(members, dict):
        members = {}
    members[agent_id] = member
    dsh["members"] = members
    cfg["dsh"] = dsh
    _dsh_save_cfg(project, cfg)

def _dsh_lock_patch(project):
    """按项目动态生成模型锁 overlay（CLI --patch 最高优先级）。
    读 project_config.json 的 dsh.model；不在白名单则回退默认。"""
    cfg = _dsh_load_cfg(project)
    model = (cfg.get("dsh") or {}).get("model") or DSH_DEFAULT_MODEL
    if model not in DSH_MODEL_WHITELIST:
        model = DSH_DEFAULT_MODEL
    safe = re.sub(r"[^\w\-]", "_", project or "default")
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", safe + "-model-lock.yml")
    body = (
        "# AgentNexus v2.15+ 模型锁 overlay（按项目动态生成）：CLI 层最高优先级\n"
        f"# project={project} model={model}\n"
        "- id: agent-default-model\n"
        "  name: '@deepseek-ai/dsh-agent-default-model'\n"
        "  config:\n"
        "    provider: opencode-go-extra\n"
        f"    model: {model}\n"
    )
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
    except Exception as e:
        _dsh_log("写模型锁失败 %s: %s" % (path, e))
    return path

def _dsh_config_path(project):
    files = get_project_files(project)
    return files.get("config_file")

def _dsh_load_cfg(project):
    p = _dsh_config_path(project)
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _dsh_save_cfg(project, cfg):
    p = _dsh_config_path(project)
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception:
        pass

def _dsh_verify_model(profile_name=DSH_PROFILE):
    """启动前校验 profile 锁定的模型在白名单内（防误选昂贵模型）"""
    try:
        import subprocess as _sp
        out = _sp.run([DSH_NODE, DSH_CLI, "--profile", profile_name, "--dump-config"],
                      capture_output=True, text=True, timeout=30)
        cfg = out.stdout or ""
        model = None
        provider = None
        in_agent_model = False
        for line in cfg.splitlines():
            if "agent-default-model" in line:
                in_agent_model = True
            if in_agent_model and line.strip().startswith("model:"):
                model = line.split(":", 1)[1].strip()
            if in_agent_model and line.strip().startswith("provider:"):
                provider = line.split(":", 1)[1].strip()
            if in_agent_model and not line.startswith((" ", "-", "#")):
                in_agent_model = False
        if model not in DSH_MODEL_WHITELIST:
            return False, f"模型校验失败：当前 {provider}/{model} 不在白名单 {sorted(DSH_MODEL_WHITELIST)}，拒绝启动"
        return True, f"模型校验通过：{provider}/{model}"
    except Exception as e:
        return False, f"模型校验异常：{e}"

def _dsh_run(project, agent_id, dsh_input, session_id, emit):
    """拉起 dsh headless 进程（按成员 profile），流式转发 NDJSON 事件。返回 (ok, session_id, usage_agg)"""
    import subprocess as _sp
    _dsh_log("进入 _dsh_run: agent=%s sid=%s" % (agent_id, session_id))
    _lock_patch = _dsh_lock_patch(project)
    _dsh_log("lock_patch=%s" % _lock_patch)
    # 按成员定位 profile（模板复制 + 身份替换）；失败回退模板 profile
    board = load_board(project)
    member = None
    for a in (board or {}).get("agents", []) or []:
        if a.get("id") == agent_id:
            member = a
            break
    prof_dir = None
    if member:
        prof_dir = _dsh_ensure_profile(project, agent_id,
                                       member.get("name"), member.get("role"))
    prof_name = _dsh_profile_name(project, agent_id) if prof_dir else DSH_PROFILE_TEMPLATE
    # v2.19：身份锚定前缀（每次会话注入，防身份漂移/混淆——即使 profile 未重建也有强身份）
    m_name = (member.get("name") if member else None) or agent_id
    m_role = (member.get("role") if member else None) or "终端执行 Agent"
    dsh_input = ("【身份锚定】你是「%s」项目班子的「%s」（身份 ID：%s，%s）。"
                 "你不是团队中任何其他成员，不要以他人身份发言、认领或更新任务。\n\n%s"
                 % (project, m_name, agent_id, m_role, dsh_input))
    cmd = [DSH_NODE, DSH_CLI, "--profile", prof_name, "--patch", _lock_patch, "--json"]
    if session_id:
        cmd += ["--session-id", session_id]
    cmd.append(dsh_input)
    usage_agg = {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0, "totalTokens": 0}
    new_sid = session_id
    try:
        proc = _sp.Popen(cmd, stdout=_sp.PIPE, stderr=_sp.PIPE, text=True,
                         encoding="utf-8", errors="replace", stdin=_sp.DEVNULL)
        _dsh_log("Popen ok pid=%s cmd0=%s" % (proc.pid, proc.args[0] if isinstance(proc.args, list) else ""))
        _first_ev = True
        for raw in proc.stdout:
            raw = raw.strip()
            if not raw:
                continue
            if _first_ev:
                _first_ev = False
                _dsh_log("dsh 首事件: %s" % raw[:80])
            try:
                ev = json.loads(raw)
            except Exception:
                continue
            if ev.get("type") == "session" and ev.get("sessionId"):
                new_sid = ev["sessionId"]
            # v2.18：审批桥内部事件 → 前端审批事件（挂起等待用户批准，dsh 进程阻塞等待应答文件）
            if ev.get("type") == "agentnexus_approval":
                ev["type"] = "approval"
            elif ev.get("type") in ("agentnexus_bridge_loaded",):
                continue
            u = ev.get("usage")
            if isinstance(u, dict):
                for k in usage_agg:
                    if k in u and isinstance(u[k], (int, float)):
                        usage_agg[k] += u[k]
            if emit:
                emit(ev)
        proc.wait(timeout=180)
        err = proc.stderr.read()[:800] if proc.stderr else ""
        if proc.returncode != 0:
            _dsh_log("dsh 子进程失败: rc=%s stderr=%s" % (proc.returncode, err[:300]))
            return (False, new_sid, usage_agg)
        return (True, new_sid, usage_agg)
    except Exception as e:
        _dsh_log("dsh 子进程异常: %s" % e)
        return (False, new_sid, usage_agg)

def _handle_dsh_model(self, project=None):
        """GET/POST /api/dsh/model：读取当前接入模型 / 切换项目接入模型（白名单校验）"""
        if self.command == "GET":
            cfg = _dsh_load_cfg(project)
            model = (cfg.get("dsh") or {}).get("model") or DSH_DEFAULT_MODEL
            if model not in DSH_MODEL_WHITELIST:
                model = DSH_DEFAULT_MODEL
            self._send_json({"ok": True, "model": model,
                             "whitelist": sorted(DSH_MODEL_WHITELIST),
                             "labels": DSH_MODEL_LABELS})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except Exception:
            req = {}
        model = (req.get("model") or "").strip()
        if not model:
            self._send_json({"ok": False, "error": "model 不能为空"}, status=400)
            return
        if model not in DSH_MODEL_WHITELIST:
            self._send_json({"ok": False, "error": "模型不在白名单：" + str(sorted(DSH_MODEL_WHITELIST))}, status=400)
            return
        cfg = _dsh_load_cfg(project)
        dsh_cfg = cfg.get("dsh") or {}
        old = dsh_cfg.get("model")
        dsh_cfg["model"] = model
        if old != model:
            dsh_cfg["session_id"] = ""  # 模型变了：下次起新会话，确保新模型生效
            members = dsh_cfg.get("members") or {}
            for _mid in members:
                if isinstance(members[_mid], dict):
                    members[_mid]["session_id"] = ""
        cfg["dsh"] = dsh_cfg
        _dsh_save_cfg(project, cfg)
        _dsh_log("模型切换 project=%s %s -> %s" % (project, old or "(default)", model))
        self._send_json({"ok": True, "model": model,
                         "note": "已生效：下一次消息将使用新模型（会话已重置）"})

def _handle_dsh_history(self, project=None):
    """GET /api/dsh/history?project=X&agent=<id>：读取成员聊天历史 + 累计用量"""
    parsed = urlparse(self.path)
    params = parse_qs(parsed.query)
    agent_id = params.get("agent", [None])[0] or "terminal"
    board0 = load_board(project)
    member = None
    for a in (board0 or {}).get("agents", []) or []:
        if a.get("id") == agent_id:
            member = a
            break
    if not member:
        self._send_json({"ok": False, "error": "看板中不存在成员 %s。" % agent_id}, status=400)
        return
    history = _dsh_load_history(project, agent_id)
    member_cfg = _dsh_member_cfg(project, agent_id)
    self._send_json({"ok": True, "history": history, "total_usage": member_cfg.get("usage", {})})

def _handle_dsh_history_clear(self, project=None):
    """POST /api/dsh/history/clear?project=X&agent=<id>：清空该成员聊天历史 + 会话（session/用量归零）"""
    parsed = urlparse(self.path)
    params = parse_qs(parsed.query)
    agent_id = params.get("agent", [None])[0] or "terminal"
    board0 = load_board(project)
    member = None
    for a in (board0 or {}).get("agents", []) or []:
        if a.get("id") == agent_id:
            member = a
            break
    if not member:
        self._send_json({"ok": False, "error": "看板中不存在成员 %s。" % agent_id}, status=400)
        return
    _dsh_clear_history(project, agent_id)
    member_cfg = _dsh_member_cfg(project, agent_id) or {}
    member_cfg["session_id"] = ""
    member_cfg["usage"] = {"inputTokens": 0, "outputTokens": 0, "cacheReadTokens": 0, "totalTokens": 0}
    member_cfg["last_ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    _dsh_save_member_cfg(project, agent_id, member_cfg)
    self._send_json({"ok": True, "message": "会话已清空（历史与 Agent 记忆均归零）"})

DSH_ANSWER_FILE = "/tmp/agentnexus_answers.jsonl"

def _dsh_approve_answer(request_id, option):
    """把用户审批结果 append 到审批桥应答文件（行级原子，多成员共享；requestId 全局唯一）"""
    import os as _os
    try:
        with open(DSH_ANSWER_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "agentnexus_approval_answer",
                                "requestId": request_id, "option": option},
                               ensure_ascii=False) + "\n")
        return True
    except Exception as e:
        _dsh_log("审批应答写入失败: %s" % e)
        return False

def _handle_dsh_approve(self, project=None):
    """POST /api/dsh/approve：用户批准/拒绝一次进程托管权限请求（写入审批桥应答文件）"""
    try:
        length = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
    except Exception:
        req = {}
    request_id = (req.get("requestId") or "").strip()
    option = (req.get("option") or "").strip()
    if not request_id or option not in ("allow", "reject"):
        self._send_json({"ok": False,
                         "error": "参数错误：需要 requestId + option(allow/reject)"}, status=400)
        return
    if not _dsh_approve_answer(request_id, option):
        self._send_json({"ok": False, "error": "应答写入失败，请稍后重试"}, status=500)
        return
    _dsh_log("审批应答: request=%s option=%s" % (request_id, option))
    self._send_json({"ok": True, "requestId": request_id, "option": option})

def _handle_dsh_chat(self, project=None):
    """POST /api/dsh/chat  SSE 流式：留言给终端 Agent（按成员托管），回复实时回传"""
    import subprocess as _sp
    parsed = urlparse(self.path)
    params = parse_qs(parsed.query)
    agent_id = params.get("agent", [None])[0] or "terminal"
    _dsh_log("dsh/chat 进入: project=%s agent=%s path=%s" % (project, agent_id, self.path))
    _dsh_log("DSH_NODE=%s DSH_CLI=%s" % (DSH_NODE, DSH_CLI))
    # 0) 成员校验：看板存在该成员（dsh 托管 / terminal 兼容 / role 含「终端」）
    board0 = load_board(project)
    member = None
    for a in (board0 or {}).get("agents", []) or []:
        if a.get("id") == agent_id:
            member = a
            break
    if not member:
        self._send_json({"ok": False, "error": "看板中不存在成员 %s，请先在组织视图拉入「终端托管」成员。" % agent_id}, status=400)
        return
    entry_t = (member.get("entry") or {}).get("type", "")
    # v2.17.1：接入类型双字段兼容——entry.type 可能被 CLI onboard（8767）覆写为 session，
    # 但顶层 entry_type=dsh 或 dsh.members 有会话配置说明仍是终端托管成员
    has_dsh_cfg = bool((_dsh_member_cfg(project, agent_id) or {}).get("session_id") or
                       (_dsh_member_cfg(project, agent_id) or {}).get("usage"))
    is_terminal_member = (agent_id == "terminal") or (entry_t == "dsh") or (member.get("entry_type") == "dsh") or                          ("终端" in (member.get("role") or "")) or has_dsh_cfg
    if not is_terminal_member:
        self._send_json({"ok": False, "error": "成员 %s 不是终端托管成员（entry.type=%s），无法进行进程托管对话。" % (agent_id, entry_t or "无")}, status=400)
        return
    # 1) 成员 profile（模板复制+身份替换）→ 模型白名单校验
    prof_dir = _dsh_ensure_profile(project, agent_id, member.get("name"), member.get("role"))
    prof_name = _dsh_profile_name(project, agent_id) if prof_dir else DSH_PROFILE_TEMPLATE
    ok, msg = _dsh_verify_model(prof_name)
    _dsh_log("verify: ok=%s %s" % (ok, msg))
    if not ok:
        self._send_json({"ok": False, "error": msg}, status=403)
        return
    # 2) 收工态检查
    data = load_board(project)
    proto = (data or {}).get("protocol") or {}
    _dsh_log("protocol.mode=%s" % proto.get("mode"))
    if proto.get("mode") == "off":
        self._send_json({"ok": False, "error": "项目处于收工态（off），终端 Agent 已休眠。请先开工再对话。"}, status=403)
        return
    # 3) 读 body
    try:
        length = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
    except Exception:
        req = {}
    message = (req.get("message") or "").strip()
    context = (req.get("context") or "").strip()  # v2.17：独立上下文（不污染落盘内容）
    session_id = req.get("session_id") or ""
    _dsh_log("message=%s session=%s" % (message[:30], session_id))
    if not message:
        self._send_json({"ok": False, "error": "message 不能为空"}, status=400)
        return
    # v2.17：给 dsh 的完整输入 = 上下文 + 新消息；历史落盘只用纯 message
    dsh_input = (context + "\n\n【现在的新消息】" + message) if context else message
    # 4) 复用/新会话（按成员）
    member_cfg = _dsh_member_cfg(project, agent_id)
    if not session_id:
        session_id = member_cfg.get("session_id") or ""
    # 4b) v2.17：用户消息立即持久化（即使本轮失败/中断，提问也不丢）
    _dsh_append_history(project, agent_id, [{
        "role": "user", "content": message,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")
    }])
    # 5) SSE 流式响应
    _dsh_log("开始 SSE 响应")
    self.send_response(200)
    self.send_header("Content-Type", "text/event-stream")
    self.send_header("Cache-Control", "no-cache")
    self.send_header("X-Accel-Buffering", "no")
    self.end_headers()
    _reply_box = {"text": ""}  # server 端累积回复全文（用于历史持久化）
    def emit(ev):
        t = ev.get("type")
        if t in ("text", "final") and ev.get("text"):
            _reply_box["text"] = ev["text"]
        try:
            self.wfile.write(("data: " + json.dumps(ev, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()
        except Exception:
            pass
    _dsh_log("调用 _dsh_run")
    import threading as _th
    _dsh_locks = globals().get("_DSH_LOCKS")
    if _dsh_locks is None:
        _DSH_LOCKS = {}
        _dsh_locks = _DSH_LOCKS
    lock = _dsh_locks.setdefault(project + ":" + agent_id, _th.Lock())
    with lock:
        ok, new_sid, usage = _dsh_run(project, agent_id, dsh_input, session_id, emit)
        _dsh_log("_dsh_run 返回: ok=%s sid=%s usage=%s" % (ok, new_sid, usage))
        if not ok and session_id:
            # session 写句柄占用等瞬态冲突：等 3 秒重试一次；仍失败则换新会话
            import time as _time
            _time.sleep(3)
            ok2, new_sid2, usage2 = _dsh_run(project, agent_id, dsh_input, session_id, emit)
            _dsh_log("占用重试: ok=%s sid=%s" % (ok2, new_sid2))
            if not ok2:
                ok3, new_sid3, usage3 = _dsh_run(project, agent_id, message, "", emit)
                _dsh_log("换新会话: ok=%s sid=%s" % (ok3, new_sid3))
                if ok3:
                    ok, new_sid, usage = True, new_sid3, usage3
            elif ok2:
                ok, new_sid, usage = True, new_sid2, usage2
    if not new_sid and ok:
        # 兜底：无 session 事件
        new_sid = "session-" + project + "-" + agent_id
    # 6) 用量落盘（按成员）
    if ok:
        member_cfg["session_id"] = new_sid
        member_cfg.setdefault("usage", {}).setdefault("inputTokens", 0)
        member_cfg["usage"]["inputTokens"] += usage.get("inputTokens", 0)
        member_cfg["usage"]["outputTokens"] = member_cfg["usage"].get("outputTokens", 0) + usage.get("outputTokens", 0)
        member_cfg["usage"]["cacheReadTokens"] = member_cfg["usage"].get("cacheReadTokens", 0) + usage.get("cacheReadTokens", 0)
        member_cfg["usage"]["totalTokens"] = (member_cfg["usage"].get("inputTokens", 0)
                                             + member_cfg["usage"].get("outputTokens", 0))
        member_cfg["last_ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _dsh_save_member_cfg(project, agent_id, member_cfg)
    # v2.17：成功回复落盘（assistant + usage）
    if ok and _reply_box["text"]:
        _dsh_append_history(project, agent_id, [{
            "role": "assistant", "content": _reply_box["text"],
            "usage": {k: usage.get(k, 0) for k in ("inputTokens", "outputTokens", "cacheReadTokens", "totalTokens")},
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")
        }])
    emit({"type": "dsh_done", "ok": ok, "session_id": new_sid,
          "usage": usage, "total_usage": member_cfg.get("usage", {})})

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

        # API: Dashboard 聚合统计（v2.6）
        if parsed.path == "/api/dashboard":
            self._send_json(dashboard_stats())
            return

        # API: 项目级决策列表（v2.10）
        if parsed.path == "/api/decisions":
            data = load_board(project)
            self._send_json({"decisions": data.get("decisions", [])})
            return

        # API: 终端 Agent 模型配置（v2.15.2）
        if parsed.path == "/api/dsh/model":
            _handle_dsh_model(self, project)
            return

        # API: 终端 Agent 聊天历史（v2.17）
        if parsed.path == "/api/dsh/history":
            _handle_dsh_history(self, project)
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

        # API: 终端 Agent 会话清空（v2.17）
        if parsed.path == "/api/dsh/history/clear":
            _handle_dsh_history_clear(self, project)
            return

        # API: 终端 Agent 审批（v2.18 进程托管权限批准闭环）
        if parsed.path == "/api/dsh/approve":
            _handle_dsh_approve(self, project)
            return

        # API: 终端 Agent 会话（v2.15 进程托管）
        if parsed.path == "/api/dsh/chat":
            _handle_dsh_chat(self, project)
            return

        # API: 终端 Agent 模型配置（v2.15.2）
        if parsed.path == "/api/dsh/model":
            _handle_dsh_model(self, project)
            return

        # API: 创建新项目（多项目管理）
        if parsed.path == "/api/projects":
            self._handle_create_project()
            return

        # API: 组织关系（v2.9）
        if parsed.path == "/api/org/relation":
            self._handle_org_relation(project)
            return

        # API: 组织加位子（v2.9，复用注册）
        if parsed.path == "/api/org/position":
            self._handle_org_position(project)
            return

        # API: 项目级决策沉淀（v2.10 CLI 结论）
        if parsed.path == "/api/decisions":
            self._handle_add_decision(project)
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

        # API: 协作协议 开工/收工（v2.14）
        if parsed.path == "/api/protocol":
            self._handle_protocol(project)
            return

        # API: 保存项目配置
        if parsed.path == "/api/config":
            self._handle_save_config(project)
            return

        # API: Agent 入口注册（T8.1）
        if parsed.path == "/api/agents/register":
            self._handle_register_agent(project)
            return

        # API: 成员改名/改身份（v2.12）
        if parsed.path == "/api/agents/update":
            self._handle_update_agent_profile(project)
            return

        # API: 拉人进班子 + 接入配置（v2.13）
        if parsed.path == "/api/agents/invite":
            self._handle_invite_agent(project)
            return

        # API: 移除成员（v2.12）
        if parsed.path == "/api/agents/remove":
            self._handle_remove_agent(project)
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
        workspace_mode = (req.get("workspace_mode") or "").strip()
        handoff = req.get("handoff", "").strip()

        success, message, project_path, resolved_workspace, ws_status = create_project(
            project_name=project_name,
            agents=agents,
            workspace=workspace,
            handoff=handoff,
            project_title=project_title,
            description=description,
            workspace_mode=workspace_mode
        )

        if success:
            self._send_json({
                "ok": True,
                "message": message,
                "project_name": project_name,
                "project_path": project_path,
                "workspace": resolved_workspace or "",
                "workspace_status": ws_status or "",
                "board_url": f"/collab_board_nothing.html?project={project_name}"
            })
        else:
            self._send_json({"ok": False, "error": message})

    def _handle_add_decision(self, project=None):
        """沉淀一条项目级决策（CLI conclude / 界面均可调用）"""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            msg = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_error(400, "Invalid JSON")
            return

        title = (msg.get("title") or "").strip()
        decision = (msg.get("decision") or "").strip()
        if not title or not decision:
            self._send_error(400, "Missing title or decision")
            return

        with board_lock:
            data = load_board(project)
            decisions = data.setdefault("decisions", [])
            new_id = max([d.get("id", 0) for d in decisions], default=0) + 1
            decisions.append({
                "id": new_id,
                "title": title,
                "decision": decision,
                "status": msg.get("status", "agreed"),
                "by": msg.get("by", "user"),
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            })
            save_board(data, project)

        self._append_system_message(f"决策已沉淀：{title}", project)
        self._send_json({"ok": True, "decision_id": new_id})

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
                if str(task["id"]) == str(task_id):
                    # 允许更新的字段
                    for field in ["status", "progress", "assignee", "artifact", "note", "priority", "handoff"]:
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
            new_id = max([int(m["id"]) for m in data["messages"] if str(m.get("id", "")).isdigit()], default=0) + 1
            new_msg = {
                "id": new_id,
                "sender": msg["sender"],
                "content": msg["content"],
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                "type": msg.get("type", "text")
            }
            data["messages"].append(new_msg)
            # v2.13.7: TaskPlan 待办清单自动同步（就地写任务+系统消息）
            try:
                sync_tasks_from_taskplan(data, msg.get("sender"), msg.get("content", ""))
            except Exception:
                pass
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
                if str(task["id"]) == str(task_id):
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
        name = (req.get("name") or "").strip() or None
        role = (req.get("role") or "").strip() or None
        if not agent_id:
            self._send_json({"ok": False, "error": "agent_id 不能为空"})
            return
        if entry is not None and not isinstance(entry, dict):
            self._send_json({"ok": False, "error": "entry 必须是 {type, target} 对象"})
            return
        if isinstance(entry, dict) and entry.get("type") not in ("http", "session"):
            self._send_json({"ok": False, "error": "entry.type 必须是 http 或 session"})
            return

        success, message = register_agent(project, agent_id, entry, name=name, role=role)
        self._send_json({"ok": success, "message": message, "agent_id": agent_id, "name": name or agent_id})


    def _handle_update_agent_profile(self, project=None):
        """v2.12: 改名 / 改身份"""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            req = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_error(400, "Invalid JSON")
            return
        agent_id = (req.get("agent_id") or "").strip()
        name = (req.get("name") or "").strip()
        role = req.get("role")
        if not agent_id:
            self._send_json({"ok": False, "error": "agent_id 不能为空"})
            return
        success, message, meta = update_agent_profile(project, agent_id, name=name or None, role=role)
        self._send_json({"ok": success, "message": message, "agent_id": agent_id,
                         "changed": meta.get("changed", []), "guide": meta.get("guide", ""),
                         "is_guide": meta.get("is_guide", False)})

    def _handle_remove_agent(self, project=None):
        """v2.12: 移除成员"""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            req = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_error(400, "Invalid JSON")
            return
        agent_id = (req.get("agent_id") or "").strip()
        success, message = remove_agent(project, agent_id)
        self._send_json({"ok": success, "message": message, "agent_id": agent_id})

    def _handle_invite_agent(self, project=None):
        """v2.13: 拉人进班子，支持 guide/api/cli/manual 四种接入配置"""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            req = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_error(400, "Invalid JSON")
            return
        agent_id = (req.get("agent_id") or "").strip()
        name = (req.get("name") or "").strip() or agent_id
        role = (req.get("role") or "").strip()
        entry_type = (req.get("entry_type") or "guide").strip()
        entry_target = (req.get("entry_target") or "").strip()
        if not agent_id:
            self._send_json({"ok": False, "error": "agent_id 不能为空"})
            return
        success, message, guide = invite_agent(project, agent_id, name, role, entry_type, entry_target)
        self._send_json({"ok": success, "message": message, "agent_id": agent_id,
                         "name": name, "guide": guide, "entry_type": entry_type})

    def _handle_protocol(self, project=None):
        """v2.14: 开工/收工切换 + 豁免名单"""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            req = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_error(400, "Invalid JSON")
            return
        mode = req.get("mode", "working")
        by = (req.get("by") or "user").strip()
        exemptions = req.get("exemptions")
        ok, message = set_protocol(project, mode, by, exemptions)
        self._send_json({"ok": ok, "message": message})

    def _handle_org_relation(self, project=None):
        """v2.9: 批量设置组织汇报关系"""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            req = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_error(400, "Invalid JSON")
            return
        relations = req.get("relations")
        if not isinstance(relations, list) or not relations:
            self._send_json({"ok": False, "error": "relations 必须是数组"})
            return
        success, message = update_org_relations(project, relations)
        self._send_json({"ok": success, "message": message})

    def _handle_org_position(self, project=None):
        """v2.9: 组织图加位子（注册即创建）"""
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            req = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_error(400, "Invalid JSON")
            return
        agent_id = (req.get("agent_id") or "").strip()
        if not agent_id:
            self._send_json({"ok": False, "error": "agent_id 不能为空"})
            return
        name = (req.get("name") or "").strip() or agent_id
        role = (req.get("role") or "").strip()
        success, message = add_org_position(project, agent_id, name, role)
        self._send_json({"ok": success, "message": message, "agent_id": agent_id, "name": name})

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
            new_id = max([int(m["id"]) for m in data["messages"] if str(m.get("id", "")).isdigit()], default=0) + 1
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

    server = ThreadingHTTPServer(("0.0.0.0", port), CollabHandler)
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
