# AgentNexus · 多 Agent 协同平台 — 项目交接文档

> 版本：v2.20.5（本地 main）｜ 更新日期：2026-10-08 ｜ 作者：Zames 与协同 Agent 团队
> 本文件是接手者（人 / Agent）的**第一入口**。读完本文件即可掌握项目全貌、运行方式、核心机制与未决事项。

---

## 0. 一句话定位

**AgentNexus 是一个"Agent 管理学"工具**：让人类作为项目主导方，管理 5~10 个 AI Agent 组成的团队，通过一个本地看板完成「规划 → 分工 → 执行 → 验收」的完整闭环。核心哲学：**JSON 文件是唯一事实来源，Agent 通过读写同一份文件协作，人类通过组织架构图指挥全局**。

## 1. 背景与动机

- 起点：作者在真实工作中发现，多 Agent 协作靠"交接文档 + 行动表"转手效率低、不实时。想做前端界面让多个 Agent 直接沟通，人类也参与其中。
- 演化路线（本工具一路走来的三阶段）：
  1. **任务看板**（v1~v2.10）：JSON 看板 + 聊天 + 三种触发模式（手动复制 / API / 定时轮询）
  2. **组织架构视图**（v2.13）：主界面从"任务驱动"升级为"**组织驱动**"——按汇报关系看团队，点人卡片直接对话
  3. **进程托管**（v2.15~v2.20）：真正的 Agent 进程跑在本机，工具内实时对话、实时看到工作进展，形成"多 Agent 讨论室"
- 定位：本地优先、零依赖、文件即数据库。面向"严肃项目可能涉及 5/8/10 个 Agent"的个体开发者 / 小型团队。

## 2. 当前能力清单（v2.20.5）

| 能力 | 说明 | 入口 |
|---|---|---|
| 项目 Dashboard | 多项目管理、创建向导、项目卡片、关键指标 | `index.html` |
| 组织架构图 | 汇报层级、拖拽排布、折线连接、缩放（⌘+滚轮）、编辑层级、加位子 | `org_chart.html` |
| 成员卡片 | 左信息（当前工作/最近交付/汇报关系）+ 右对话（留言或终端实时聊天），支持全屏 | 组织图点节点 |
| 任务看板 | Todo / In Progress / Review / Done 四列，任务认领、进度、验收 | `collab_board_nothing.html` |
| 五种接入模式 | 引导词(Hybrid) / API / 定时轮询 / CLI(agentnexus) / 终端托管(dsh) | 加位子弹窗 |
| 开工/收工协议 | 工作状态 30 分钟对齐看板，收工状态休眠（server 端保留，UI 开关已撤） | `protocol` 字段 |
| 讨论房间 | 多终端托管 Agent 真对话（轮转唤醒、三轮后结束、用户随时插话） | 顶栏"讨论室" |
| 审批闭环 | 托管 Agent 撞权限墙 → 前端审批卡 → 批准/拒绝（120s 未操作自动拒绝） | 聊天面板 |
| 聊天历史持久化 | 每成员 `dsh_chat/<agent>.json`，跨刷新/跨浏览器可追溯，600 条封顶 | `/api/dsh/history` |
| 数据版本管理 | 数据仓库自动 git 提交，每次写入可回滚 | `agent-collab-projects/` |

## 3. 系统架构

### 3.1 整体拓扑

```
┌────────────── 浏览器（本机） ──────────────┐
│ index.html (Dashboard)                     │
│ org_chart.html (组织架构·主界面)            │
│ collab_board_nothing.html (任务看板·二级)   │
└───────────────┬────────────────────────────┘
                │ HTTP / SSE
┌───────────────▼────────────────────────────┐
│ server.py  (Python 标准库，零第三方依赖)     │
│ · REST API + SSE 流式事件                   │
│ · fcntl 文件锁 + 原子写                     │
│ · 进程托管调度（dsh）· 讨论房间轮转 · 审批桥  │
└───────┬───────────────────────┬────────────┘
        │                       │
┌───────▼──────────┐   ┌────────▼───────────┐
│ 数据仓库           │   │ dsh 运行环境        │
│ agent-collab-     │   │ ~/.dsh/profiles/    │
│ projects/<项目>/   │   │ (sanguo-dsh 模板 +  │
│ collab_board.json │   │  各成员 profile)    │
│ project_config    │   │ dsh CLI (bin.js)    │
│ dsh_chat/         │   └─────────────────────┘
└───────────────────┘
```

### 3.2 协同模型（谁做什么）

- **人类（Zames）**：项目主导方、最终决策者。发需求、拍板、验收。
- **规划 Agent（Planner）**：task plan、任务拆解、派工、校验验收。
- **执行 Agent（Builder×N / 各类角色）**：认领任务、执行、交付、改状态。
- **数据（collab_board.json）**：唯一事实来源。任务状态、消息、成员名册、决策、协议全部落在这一份文件；任何 Agent 写入后，其他 Agent 通过读文件 / 轮询感知。
- **分工原则**：Planner 不独占执行，执行侧可并行认领；各自认领的部分自行协调（阶段二起双方并行）。

### 3.3 接入模式详解（五种）

| 模式 | 机制 | 适用 | 备注 |
|---|---|---|---|
| 引导词 Hybrid | 前端生成身份引导词，复制到 Agent 聊天界面粘贴激活；Agent 主动读看板 | 纯本地、小白、无 Key | 默认模式 |
| API | 配置 LLM API，前端 `@` 消息自动触发对应 Agent 回复/执行 | 有 Key、要自动化 | 需联网 |
| 定时轮询 | 前端定时器触发 Agent 去读 JSON（5/15/30min/1h/1d） | 无人值守异步协作 | 消耗 token 可控 |
| CLI（agentnexus） | 终端 Agent 跑 `agentnexus onboard/claim/update/handoff/conclude` 命令，写入同一 JSON | 真能碰文件的 Agent | `tools/agentnexus` |
| **终端托管（dsh）** | 工具直接拉起 DeepSeek Harness Agent 进程，卡片内实时对话、实时看工作 | 最深度协作 | v2.15+ 核心 |

> 唤醒权差异：引导词 Agent 自己唤醒自己（读板）；API Agent 可以被板唤醒但只能说话；**CLI/托管 Agent 可以被板唤醒且真的执行**。

### 3.4 两个主界面的关系

- **组织架构（主）**：看"团队"——谁是谁的下属、谁手上在干啥、身份/汇报/协作关系，点人直接对话。
- **任务看板（二级）**：看"事"——任务流状态（Todo/In Progress/Review/Done）。
- 组织图顶栏可一键切看板；讨论室从组织图顶栏进入。

## 4. 运行方式

```bash
# 1) 启动服务器（端口 8766）
cd /Users/zames/Desktop/Project/agent-collab-dev
python3 server.py 8766
# 或用 start.command 双击（macOS）

# 2) 打开浏览器
# http://localhost:8766  → Dashboard → 点项目卡进入组织架构
# http://localhost:8766/org_chart.html?project=<项目名>
# http://localhost:8766/collab_board_nothing.html?project=<项目名>
```

- **服务器托管**：本机 launchd 托管，进程被杀后约 6 秒自动拉起（正常无需手动重启）。
- **端口检查**：`lsof -tiTCP:8766 -sTCP:LISTEN`
- **日志**：`/tmp/dsh_server.log`（dsh 相关）；手动启动可用 `/tmp/manual_server.log`。
- 环境变量：`COLLAB_PROJECTS_ROOT`（数据仓库根目录，默认 `/Users/zames/Desktop/Project/agent-collab-projects`）。

## 5. 目录结构

### 5.1 源码仓库 `/Users/zames/Desktop/Project/agent-collab-dev/`（git，本地 main）

```
server.py                  # 后端（零依赖，含 dsh 托管/讨论房间/审批桥调度）
index.html                 # Dashboard 首页（多项目）
org_chart.html             # 组织架构主界面（人卡/终端聊天/讨论室入口）
collab_board_nothing.html  # 任务看板（Nothing 风格，推荐）
CHANGELOG.md               # 版本记录（英文，含 v2.10→v2.20.5 全演进）
README.md                  # GitHub 外宣文档（停留在 v2.10 视角，需择机更新）
docs/
├── PROJECT_HANDOVER.md    # ★ 本交接文档
├── PROJECT_SUMMARY.md     # 早期总结（阶段一/二，已过时，参考用）
├── architecture.html      # 架构图
├── PLAN-讨论房间-v1.md     # 讨论房间设计
└── ROADMAP-通用进程托管.md # 进程托管路线
tools/
├── agentnexus             # CLI 接入命令（onboard/claim/update/handoff/conclude…）
└── approval-bridge/       # dsh 审批桥插件（index.js，超时 120s 自动拒绝）
examples/  tests/          # 示例与回归测试
start.command / stop.command
```

### 5.2 数据仓库 `/Users/zames/Desktop/Project/agent-collab-projects/`（git，自动提交）

```
<项目名>/
├── collab_board.json      # ★ 核心数据（agents / messages / tasks / decisions / protocol）
├── project_config.json    # 项目配置（dsh.members / dsh.model / trigger_templates…）
├── dsh_chat/<agent>.json  # 每托管成员聊天历史（600 条封顶）
├── processed_msg_ids.json # 定时模式去重
└── api_calls.log
```

### 5.3 dsh 配置 `~/.dsh/profiles/`（不在 git 内）

```
sanguo-dsh/                          # ★ 模板（providers/模型/协议段；审批桥 timeoutMs=120000）
dsh-<项目>-<成员>/cordis.patch.yml    # 每托管成员一份（身份段按成员替换）
```

## 6. 数据模型要点

**collab_board.json**：
- `agents[]`：**名册，唯一的成员权威来源**。含 `id/name/role/color/reports_to/entry_type`。
  ⚠️ 数据安全红线：**不要直接覆盖 agents 数组**（曾因 Agent 直接编辑 JSON 导致成员丢失，v2.14.1 修复）。加/删成员走组织图或 `/api/agents/*`。必须直接改 JSON 时，先 `GET /api/board` 再原样保留 agents 数组。
- `messages[]`：全局聊天（sender/to/content/timestamp）
- `tasks[]`：任务（id/status: todo|in_progress|review|done/assignee/progress/note）
- `decisions[]`：项目决策沉淀
- `protocol`：开工/收工状态（mode/updated_by/updated_at/exemptions）

**project_config.json**：
- `dsh.model`：项目级模型（白名单：Space Bunny free / DeepSeek V4 Flash / V4.1 Flash / V4 Flash Vision / Vision Experience）
- `dsh.members.<agent_id>`：{session_id, usage, last_ts}——会话与用量
- `trigger_templates`：引导词模板（per_agent / global）

## 7. 关键机制详解（接手必读）

### 7.1 进程托管生命周期（v2.15~v2.20 打磨的核心）

1. **成员级串行锁**：`globals()["_DSH_LOCKS"]`（全局，勿改回局部——v2.20.2 曾因局部赋值导致并发消息互杀）。同一成员的消息真正排队，前一条跑完落盘后下一条才启动，并继承完整会话记忆。
2. **会话续用**：传 `--session-id` 保持记忆（上下文继承靠它）；模型切换会重置会话。
3. **空闲 watchdog**：180s 无任何事件才杀（活跃长任务不误杀）；任何 stdout 事件重置计时。
4. **审批桥**：托管 Agent 撞权限墙 → `approval/request` → stdout 事件 → 前端审批卡 → 应答文件（/tmp/agentnexus_answers.jsonl）→ 桥轮询 400ms。**120s 无人批准自动拒绝**（安全默认，不自动放行）。
5. **进程组清理**：Popen 用 `start_new_session=True`，杀进程用 `os.killpg`（进程组级）+ `proc.kill()` 兜底——工具子进程不留孤儿。
6. **身份锚定**：每次唤醒输入前加【身份锚定】块（项目/姓名/ID/角色 + "你不是任何其他成员"），profile 身份段用占位符生成——防身份混淆。
7. **聊天历史**：用户消息到达即落盘；final 事件实时落盘（防丢失）；清空会话同时重置 session/usage（记忆归零）。
8. **模型三层锁**：profile 锁 + CLI `--patch` overlay（`tools/<项目>-model-lock.yml`）+ server 白名单。

### 7.2 讨论房间（v2.20）

- 轮转调度：`POST /api/discuss/start` 建房间，server 轮流唤醒每个托管成员，把**完整对话记录**传给下一位，要求回应上一位观点。
- 「三轮后结束」按钮：勾选后锁定 3 轮，唤醒词追加"请尽量收敛观点"；不勾则无限讨论（纯说话、无工具调用，token 可控）。
- 隔离规则：唤醒词带【讨论模式】硬约束（只说话不执行）；`kill_after_final` 让一轮收尾即杀进程。
- **已知限制**：房间状态是内存级——server 重启或切项目即清空。转写落板是后续迭代项。

### 7.3 数据安全

- 文件锁 fcntl + 原子写（tmp+rename）
- `save_board()` 后自动 git 提交（数据仓库）——任何误写可回滚
- 消息 id 类型容忍（防 Agent 直接编辑写字符串 id）

### 7.4 开工/收工协议

- `protocol.mode = working | off`；working 时 Agent 每 30 分钟对齐看板（认领/交付/验收），off 时停止主动轮询但保留被动回复（被 @ 仍回）。v2.16.1 撤掉了顶部广播 UI（实操不好用），server 端与引导词协议段**保留**。

## 8. API 清单（server.py）

| 分组 | 端点 |
|---|---|
| 项目 | GET/POST `/api/projects`、DELETE `/api/project`、POST `/api/projects` |
| 看板 | GET `/api/board`、GET/POST `/api/config` |
| 任务 | POST `/api/tasks/{id}`、POST `/api/claim` |
| 消息/决策 | POST `/api/messages`、POST `/api/add-decision` |
| Agent 管理 | POST `/api/invite-agent`、`/api/register-agent`、`/api/update-agent-profile`、`/api/remove-agent` |
| 组织 | POST `/api/org-relation`、`/api/org-position` |
| 协议 | POST `/api/protocol` |
| 连接/调度 | POST `/api/test-connection`、`/api/schedule-config` |
| dsh 托管 | POST `/api/dsh/chat`(SSE)、GET `/api/dsh/history`、POST `/api/dsh/history/clear`、POST `/api/dsh/approve`、GET/POST `/api/dsh/model` |
| 讨论室 | POST `/api/discuss/start` `resume` `stop` `round-limit` `chat`、GET `/api/discuss/state` |

全部支持 `?project=<项目名>` 参数。

## 9. 版本演进速览（v2.10 → v2.20.5）

| 版本 | 里程碑 |
|---|---|
| v2.10 | CLI 模式（agentnexus onboard/claim/update/handoff/conclude） |
| v2.12 | 皮肤系统（Nothing + Apple 双主题）、Dashboard 首页 |
| v2.13 | 组织架构视图（reports_to 层级、拖拽、折线连接、人卡留言） |
| v2.14 | 开工/收工协议；**数据仓库 git 自动提交**（误写可回滚）；数据安全红线 |
| v2.15 | **终端托管（dsh）**：模型硬锁、SSE 实时聊天、会话/用量持久化 |
| v2.16 | 托管作为一等接入模式（第 5 选项）；每成员专属 profile；改名重建 |
| v2.17 | 聊天历史持久化（dsh_chat/）；全屏成员卡；消息即到即落盘 |
| v2.18 | **审批闭环**（权限墙 → 前端批准/拒绝 → 应答文件） |
| v2.19 | 身份锚定强化；工作进展可见（⏳ 状态点、思考块折叠）；上游错误可见 |
| v2.20 | **讨论房间**（真多 Agent 对话、三轮后结束） |
| v2.20.1 | 修复连续留言无回复（旧进程持锁） |
| v2.20.2 | 修复成员级锁（全局化）、空闲 watchdog、模型校验 |
| v2.20.3 | 审批超时 120s、进程组清理（killpg） |
| v2.20.4/5 | 全屏布局修复、聊天自动滚到底 |

## 10. 协作约定（用户偏好，接手必须遵守）

1. **节奏**：开发 → 用户验收 → 再发 GitHub 发布版。**不并行、不贪快**。
2. **语言**：CHANGELOG / 发布说明 / README 对外用**英文**；与用户交流用中文。
3. **发布**：用户说"发布吧"才推 GitHub（仓库：github.com/Zamesback/agent-nexus，本地 main）。
4. **数据安全**：agents 名册不直接覆盖；改 JSON 先 GET /api/board。
5. **版本管理**：数据仓库已纳入 git，源码仓库提交用清晰英文 message。

## 11. 已知问题与未决事项

| 事项 | 状态 |
|---|---|
| **ACP 方案**（dsh 自带 ACP server：session/close/cancel/request_permission） | **远期目标**。用户点名要花时间探讨 ACP 与当前"pkill+watchdog+审批桥"的优劣势再决定 |
| 讨论室状态内存级 | 已知限制，转写落板待迭代 |
| 上游模型 400 波动（deepseek-v4.1-flash 曾"Model is unavailable"） | 上游波动非配置错；出现时提示用户或切模型 |
| 简化进程管理（无 session + 全文 context 传递，像讨论室那样） | 权衡未决，当前 v2.20.x 已稳定，不急 |
| Windows 兼容 | fcntl 需 msvcrt 适配（前端/核心逻辑可跑） |
| 全公司"员工"概念（长期 Agent 跨任务复用） | 3.0 待办，非当前 |
| README 落后于 v2.20.x | 待大版本发布时一并更新（英文） |
| 新项目"自带工作区 vs 工具工作区"二合一 | 已有部分交互处理，后续项目实操验证 |

## 12. 接手后第一步

1. `curl http://localhost:8766/` 确认 server 在跑（不在则 `python3 server.py 8766`）
2. 打开 `http://localhost:8766` → 点「三国指挥官」进组织架构，看人卡/看板/讨论室全貌
3. 读 `/tmp/dsh_server.log` 尾部看 dsh 最近行为
4. 需要动代码：`cd /Users/zames/Desktop/Project/agent-collab-dev && git log --oneline -10` 对齐版本
5. 有任何新功能/新迭代：先跟用户确认方向，再动手（节奏见第 10 节）

---

*本文档由执行 Agent 于 2026-10-08 整理，基于源码、CHANGELOG 与多轮协作实录。如与代码不符，以代码为准。*
