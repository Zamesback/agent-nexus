# 工作计划：进程托管通用化（v2.17+）

> 目标：把「终端托管（写死 dsh）」升级为「本地 Agent 进程托管」通用机制——
> 自己继续用 dsh；别人拿到工具也能上手，配置自己的 CLI（Claude Code / Codex / 任意 CLI）完成进程托管。

---

## 一、现状与问题

- v2.16 已实现「终端托管」：拉人面板第 5 选项，拉入即生成成员专属 dsh profile，卡片右下角实时对话（SSE），会话/用量按成员隔离。
- 但**写死 dsh**：spawn 的是 dsh 的 node 入口，profile 模板是 `~/.dsh/profiles/sanguo-dsh/`（本机手工配），模型锁是 dsh 的 `--patch`。
- 别人拿到工具：①没装 dsh 用不了；②装了 dsh 也没有「模板 profile」（含 API key / 模型白名单 / 协作协议）——**缺首次配置向导**；③想用 Claude Code 等其他终端 Agent → 不支持。

## 二、目标形态（v2.17+）

```
拉人面板「终端托管」
  └─ 选 runtime：dsh · Claude Code · Codex CLI · 通用 CLI（可扩展）
        │
        ├─ 首次使用（无模板时）：进「进程托管设置」向导
        │    检测本机 CLI → 选 runtime → 配 API key / 登录 → 选模型白名单 → 生成模板 profile
        │
        └─ 拉人 → 按 runtime 生成成员专属配置（身份/协作协议替换）
              └─ 对话 → server 按 runtime spawn 进程 → SSE 实时回复
```

核心抽象：**runtime 注册表**。每个 runtime 只声明四样东西：

| 配置项 | 含义 | dsh 示例 | Claude Code 示例 | 通用 CLI 示例 |
|---|---|---|---|---|
| `spawn` | 启动命令与参数模板 | `node .../cli/lib/bin.js --profile <p>` | `claude -p --profile <p>` | 用户自定义 |
| `profile_dir` | 成员配置目录 | `~/.dsh/profiles/dsh-<项目>-<id>/` | `~/.agentnexus/profiles/claude-<项目>-<id>/` | `~/.agentnexus/profiles/generic-<项目>-<id>/` |
| `verify` | 启动前校验（CLI 存在 / key 有效） | dsh 子进程 `--version` | `claude --version` | 自定义命令 |
| `model_lock` | 模型选择与白名单策略 | dsh `--patch` + `tools/<项目>-model-lock.yml` | env `ANTHROPIC_MODEL` | env 或注入 prompt |

## 三、任务拆解

### P0 · 不动现有 dsh 行为，先做抽象（自己用零风险）

- **T1 runtime 注册表**：server.py 新增 `RUNTIMES` 注册表（id/标签/spawn/profile_dir/verify/model_lock），把现有 dsh 逻辑迁入 `terminal_run(project, agent_id, runtime, ...)` 抽象函数，**dsh 行为与配置路径完全不变**。
  - 验收：三国 DSH顾问 对话全链路照常；`_dsh_*` 改名后无回归。
- **T2 entry 记录 runtime**：拉人时 `entry.target` 记录 runtime id（dsh 兼容旧值）；对话路由按 runtime 分发。
  - 验收：旧 dsh 成员不迁移也能对话；新拉 dsh 成员记录 runtime=dsh。

### P1 · 别人能上手（核心交付）

- **T3 首次配置向导**（设置页「进程托管设置」）：
  1. 自动检测本机 CLI（`which dsh / claude / codex`，未装给出安装指引）；
  2. 选 runtime → 填 CLI 路径（检测到则免填）；
  3. 配 API key / 登录（dsh：`dsh auth login` 或 opencode key；claude：`ANTHROPIC_API_KEY`；通用：自定义 env）；
  4. 选模型白名单（默认给性价比推荐项）；
  5. **生成模板 profile**（写到工具自己的目录，如 `~/.agentnexus/templates/<runtime>/`，供 `ensure_profile` 复制）。
  - 验收：全新机器上按向导 5 步走完，即可拉终端托管成员并对话。
- **T4 通用 CLI runtime**：用户自定义 spawn 命令 + env + 注入协作协议 prompt；不依赖任何特定产品。
  - 验收：用一个任意 CLI（如本地脚本模拟）完成拉人→对话→看板协同。
- **T7 配置/用量按 runtime 迁移**：`dsh.members` → `terminal.<runtime>.members.<id>`（旧 dsh 键回退兼容）；模型切换按 runtime 重置会话。

### P2 · 常用 runtime 具体适配

- **T5 Claude Code runtime**：spawn `claude`，profile 用 `~/.agentnexus/profiles/claude-*`（工具自己管理），模型白名单走 `ANTHROPIC_MODEL` 环境变量 + 白名单校验。
  - 验收：真实 `claude` CLI 拉人→对话→工具内实时回复。
- **T6 Codex CLI runtime**（`openai codex`）：同理适配。
- **T8 拉人面板 UI**：「终端托管」展开 runtime 选择（已配置的 runtime 才可选；未配置的引导去设置向导）。

### P3 · 分发与文档

- **T9 使用文档（英文，外向）**：README 新增「Terminal Hosting Setup」章节（前置条件 / 5 步向导 / 支持的 runtime / 常见问题）；docs/ 内补充中文版操作说明。
- **T10 完整验证 + git 提交 + CHANGELOG（英文条目）**：多 runtime 实测、旧数据迁移、干净机器模拟。

## 四、风险与决策点

- **模板从哪来**：向导生成（推荐）；或随仓库带一份 dsh 模板骨架（**不含 key**）降低上手成本——建议两者都要：仓库带骨架模板，向导负责填 key/白名单。
- **key 安全**：key 只存本机 profile，不进 git；README 明确「工具不收集 key」。
- **generic runtime 边界**：任何能「命令行起、stdin/stdout 说话、有上下文记忆」的 CLI 都能接入；无法满足的（如纯 GUI）明确拒绝并提示。
- **模型锁的一致性**：多 runtime 的白名单校验逻辑统一收敛到 `verify` 阶段，避免各 runtime 行为漂移。
- **是否沿用 dsh 的「收工态/休眠」协议**：通用 runtime 默认沿用（协作协议随 profile 注入）；不支持的 runtime 至少保证「对话即用」。

## 五、执行顺序建议

先做 **T1+T2**（纯重构，保住自己用）→ 再 **T3+T4**（别人能上手，核心价值）→ T7 数据迁移 → T5/T6/T8 扩展 → T9/T10 收尾发布。
