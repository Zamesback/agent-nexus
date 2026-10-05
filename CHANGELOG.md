## v2.15.2 — Per-project model selector + org canvas zoom

- **Per-project model choice**: the dsh terminal agent's model is now a **project-level option** instead of being hard-wired. `GET/POST /api/dsh/model?project=<name>` validates against a server whitelist (Space Bunny free / DeepSeek V4 Flash / V4.1 Flash / V4 Flash Vision / Vision Experience) and persists to `project_config.json`; switching models resets the dsh session so the next message takes effect.
- **Dynamic model lock**: `_dsh_lock_patch(project)` generates a per-project `tools/<project>-model-lock.yml` overlay from the stored choice (falls back to default on invalid values); the dsh CLI receives it as its highest-priority `--patch`, so the running process always matches the project's selected model.
- **Org canvas zoom**: zoom toolbar (− / % / + / fit) with ⌘/Ctrl + wheel zoom anchored at the cursor; zoom level persisted per project (localStorage); drag distances and layout are scaled correctly under zoom; zoom layer width/height follows `totalW/H * zoom`.
- Org view dsh status bar adds a **model dropdown** (whitelist + Chinese labels, current value preselected; switch shows a confirmation note).

## v2.15.1 — Space Bunny model + org card side-by-side layout

- **Model switch**: terminal agent (dsh) now runs **Space Bunny** (`opencode-go-extra/space-bunny-free`, OpenCode Go quota, limited-time free) per user request; DeepSeek Flash kept in server whitelist as fallback. Lock enforced at profile + CLI `--patch` overlay + server whitelist (3 layers).
- **Org card layout**: person card widened to 920px with **side-by-side layout** — left column (current work / recent deliveries / reporting line), right column (chat history + real-time terminal chatbox). Chat area flex chain fixed so the message list scrolls internally and the input box stays pinned.
- dsh status bar shows the actual locked model name.

## v2.15 — Terminal Agent Process Hosting (dsh)

**New: fifth access mode — terminal agent process hosting.** The board now launches a DeepSeek Harness (dsh) agent process on the user's machine and serves it through a real-time chatbox in the org view, while the JSON board remains the single source of truth.

- **Model hard-lock**: project-specific dsh profile (`sanguo-dsh`) locks DeepSeek Flash (deepseek-official/deepseek-flash) and a server-side whitelist rejects any drift, preventing accidental expensive models.
- **`POST /api/dsh/chat` (SSE streaming)**: model whitelist check → work-mode check (sleeps in off mode) → session reuse → streamed NDJSON events (thinking/text/final) → usage persisted to `project_config.json`.
- **Board collaboration**: the dsh session reads/writes the board via `agentnexus` CLI (claim/update/send), so planner-side polling sees real state changes (verified: claim T54 → in_progress, auto git commit).
- **Org view terminal panel**: terminal member card opens an embedded chatbox with streaming replies, token usage (real billing basis: input+output, cache-read excluded), session clear.
- **Robustness**: `ThreadingHTTPServer` for SSE; per-project serial lock; auto retry and fresh-session fallback on transient session write-handle conflicts.
- Node auto-discovery (env `DSH_NODE` → common paths → bundled fallback).

# Changelog

This file records version changes for AgentNexus. Version numbers follow Semantic Versioning (SemVer): `MAJOR.MINOR.PATCH`.

## [v2.14.2] - 2026-10-04

### Project data layer now under git version control (auto-commit on save)

- `agent-collab-projects/` (all project boards + docs) initialized as a git repo; `.gitignore` excludes `.DS_Store`, `*.lock`, `collab_board.json.bak*` backups and `Mentor社区/videos/`.
- `save_board()` now auto-commits the board file after every write, so every message / claim / status change leaves a recoverable snapshot — a direct-JSON overwrite like the one that lost three 三国指挥官 members can now be rolled back instantly.
- Fixed message id mixed-type crash: agents editing JSON directly had written string ids (5 messages on 三国指挥官), breaking `max([m["id"]...])`. All id computations are now type-tolerant, and the 5 string ids were normalized back to integers.

## [v2.14.1] - 2026-10-04

### Fix: agents list overwritten by direct JSON edit (data-safety hardening)

- On 三国指挥官, three members (Builder 4试玩反馈 / builder5 / builder6) silently disappeared from the org chart. Root cause: an agent edited collab_board.json directly to feed T70 and saved from a stale snapshot, overwriting the agents array back to 5 entries (its own backup `.bak_t70_193250` still had 8; `.bak_t70feed_193934` had 5). Restored all 8 members from that backup, keeping current messages/tasks.
- **Onboarding guides now include a data-safety section**: the agents array is the roster — never edit or overwrite it directly; add/remove members only via the org view or /api/agents/*. If you must edit the board JSON directly, always GET /api/board first and keep the agents array identical on save (only append messages / modify tasks). A system message was left on the board about the restore.

## [v2.14.0] - 2026-10-04

### New: Work / Wrap-up collaboration protocol

- **Per-project work state ("working") and wrap-up state ("off")**, as a one-click switch in the top bar of both the org-chart view and the board view. Board JSON now carries a `protocol` field (`mode`, `updated_by`, `updated_at`, `exemptions`) as the single source of truth.
- **Working state**: broadcasting the task board is the kickoff signal; after claiming a task each agent aligns with the board every 30 minutes (claim / deliver / review / accept). The switch posts a system message so everyone sees the state.
- **Wrap-up state**: all agents stop active polling and claiming but keep passive responses (being @-mentioned still gets a reply). The board is NOT frozen.
- **Manual exemptions**: when wrapping up, the user can multi-select members who keep working (shown as an "豁免" tag on their org-chart card; the tag disappears when back to working). Exemptions clear automatically on the next "start".
- **Onboarding guides now include the collaboration-protocol section** (working/off semantics + exemptions), so newly invited agents know the 30-minute alignment and wrap-up rules without being told again.
- API: `POST /api/protocol?project=X` body `{mode, by, exemptions}`. Verified end-to-end on 三国指挥官: off with builder exempted -> card tag + system message; back to working -> exemptions cleared.

## [v2.13.8] - 2026-10-04

### Polish

- **Org-chart cards: the lower half now shows what each member is actually working on.** Previously the status strip left the lower part of the card mostly empty. Cards now render an "在途" row with the single most urgent open task title (urgency order: blocked > in_progress > review > todo), ellipsized to one line; members with no open tasks simply omit the row. Verified on 三国指挥官: planner/builder/builder5/build2/builder3/builder6 each show their top task; user/试玩反馈 show none.

## [v2.13.7] - 2026-10-04

### Features

- **TaskPlan broadcast auto-syncs the board**: when an agent posts a message containing the standard todo-table (`| # | 任务 | 负责 | 优先级 | 说明 |`), the server now parses every row and creates missing tasks directly into the board's 待办 (todo) column — with role→member assignment (美工→美术, 音频→音频, 玩法→玩法, 数值→数值, UI→UI, 引擎/逻辑→planner, 试玩→试玩) and P1/P2/P3→high/medium/low priority mapping. Tasks already on the board are left untouched (no status overwrite). A system message reports how many were synced/skipped.

### Fixes

- Safe next-message-id computation (messages with non-numeric ids no longer crash the sync).

### Notes

- Verified end-to-end on 三国指挥官: replayed the latest broadcast and created 10 todo tasks (T41-F/T54/T56/T57/T59/T60/T62/T63/T-遗留/UI线), skipped 4 existing; board now shows 待办 10 / 进行中 3 / 待验收 3 / 已完成 28 / 阻塞 0.

## [v2.13.6] - 2026-10-04

### Fixes

- **Board was silently dropping `review` tasks** (reported on 三国指挥官: open tasks visible in the org view never appeared on the board). The board only recognized todo/in_progress/done/blocked, so review-state tasks were filtered out entirely. Board now has a 待验收 (review) column with its own amber accent.
- **Org view status names aligned with the data standard** (`in_progress` instead of `doing`): status dots and labels now render correctly for in-progress tasks.

### Notes

- Verified on 三国指挥官: board shows 待办 0 / 进行中 2 / 待验收 3 / 已完成 28 / 阻塞 0; org-view open-task lists render in_progress dots correctly.

## [v2.13.5] - 2026-10-04

### Fixes

- **Org-chart role text wraps again** (regression from v2.13.4's single-line ellipsis): role descriptions now wrap naturally, clamped to 3 lines via `-webkit-line-clamp`. Cards grew to a fixed 320×162 (geometry constants updated in sync), so connector ports stay exactly on the card edge while long roles (e.g. 美术设计's multi-discipline description) render fully.

### Notes

- Verified in browser on 三国指挥官: 1-line and 2-line roles both render, planner/builder out-ports still coincide with card edges (372.98 / 790), both layout directions clean.

## [v2.13.4] - 2026-10-04

### Fixes

- **Org-chart lines no longer "vanish" at the node edge**: node cards were 232px wide in CSS while connector geometry assumed 320px — every line started ~88px inside the node and was hidden behind the card, appearing as a broken/segmented line (reported on 三国指挥官 for planner and 美术设计). Node cards are now a fixed 320×132 box (box-sizing: border-box, text ellipsized) matching the geometry constants, so connector endpoints sit exactly on the card edge in every layout, after drags, and in both directions.

### Notes

- Verified in browser: planner/builder out-ports coincide with the real right edge of their cards (372.98 / 790), junction dots follow, both layout directions clean.

## [v2.13.3] - 2026-10-04

### Fixes

- **Org-chart connector polish** (reported on 三国指挥官): widened the column gap (380→430) so the shared bus and branch stubs read as deliberate segments instead of cramped ticks; child in-ports now land at 0.3×height while parent out-ports stay at mid-height — a parent line no longer enters a child at the same height the child's own line exits, killing the "line passes straight through the node" illusion.

### Notes

- Junction dots follow the new in-port positions. Verified in browser on 三国指挥官 (planner→builder/builder4/builder5 + builder→build2/builder3) and both layout directions remain clean.

## [v2.13.2] - 2026-10-04

### Improvements

- **Shared-bus connectors with junction dots**: children of the same parent now share one trunk segment from the parent, then branch to each child; every branch point (and the trunk start for 2+ children) gets a small junction dot. Multi-line meetings that used to zigzag left/right now read as one clean bus.
- **All connectors are now plain right-angle paths** (rounded corners removed) — no more mixed clockwise/counter-clockwise rounding; the junction dots carry the visual anchor instead.

### Notes

- Dependency/collaboration lines keep simple orthogonal right-angle routing (no dots). Verified in browser in both layout directions (left-right & top-down): shared bus, junction dots at correct coordinates, zero rounded segments.

## [v2.13.1] - 2026-10-04

### Improvements

- **Orthogonal org-chart connectors**: report & dependency lines are now clean right-angle paths with rounded corners (horizontal/vertical runs instead of Bezier S-curves) — tidier and easier to follow.
- **Layout direction toggle**: new "⇄ 左右/上下" button on the org view top bar switches between left-right and top-down layouts (per-project preference, persisted). Left-right is the new default: it uses vertical space, keeps the canvas narrow, and reads naturally as a tree; top-down remains one click away.

### Notes

- Drag positions still override auto-layout; switching direction resets to auto-layout for a clean tree. Connector styles (report solid / dependency dashed + opacity layering) unchanged.
- Verified in browser: rounded-corner path output (Q segments) in both directions, no parent-child overlap with the widened 380px column gap, drag re-render keeps orthogonal lines.

## [v2.13.0] - 2026-10-04

### New Features

- **Invite members with real onboarding (multi-mode)**: "＋ 加位子" is now "拉人进班子" — besides a name and role you pick how the agent actually connects:
  - **引导词接入 (guide)**: generates a full identity prompt (name / role / ID / work protocol) — copy & paste it into the target agent (e.g. Doubao Work) to activate; the guide is stored on the member record for re-sending anytime
  - **API 接入 (api)**: outputs the HTTP endpoints (read board / send message / claim task / update task / conclude decision) for the agent to poll; an optional target URL is saved as the http entry
  - **CLI 接入 (cli)**: prints the `agentnexus onboard` command + everyday commands for terminal agents
  - **仅添加身份 (manual)**: placeholder only, same as the old behavior
- **Editable role on the person card**: "✎ 身份" lets you change a member's role; "✎ 改名" remains. The card now shows an entry-method badge (引导词 / API / CLI / 仅身份).
- **Identity-change awareness**: renaming or changing role posts a system message to the board (so the planner and collaborators see it), keeps the ID stable (assigned tasks unaffected), and — for guide-onboarded members — auto-opens the regenerated guide so you can re-paste it and keep the agent's self-identity in sync. A "重发引导词" button is on the card.

### Notes

- New API: POST `/api/agents/invite` (agent_id/name/role/entry_type/entry_target). `/api/agents/update` now returns `changed` + `guide` + `is_guide`.
- Verified all four invite modes via API (entry + guide persisted in board & config); rename/role change leaves a system message and regenerates the guide; UI flow verified in browser (mode picker, target row toggle, guide result modal, person-card buttons & badge).

## [v2.12.0] - 2026-10-04

### New Features

- **Back-to-dashboard entry**: the org view top bar now has a "← 项目总表" button (and the board page has one too, hidden when embedded as an org-view overlay) so you can always jump back to the project dashboard from inside a project.
- **Drag & drop org chart**: every member card can be dragged to rearrange the org view freely — the position is remembered per project (localStorage) and survives refresh; a "重置布局" button restores the automatic layered layout anytime.
- **Prettier connector lines**: smoother S-curve bezier control points, layered stroke width and opacity (report 1.6px solid / dependency 1.2px dashed), refined arrow markers — closer to the reference org-card style.
- **Rename & remove members**: the person card now has "✎ 改名" and "移除成员" actions
  - rename updates name/role in board + config, never touches the agent's entry/connected state
  - remove is guarded (the project owner `user` cannot be removed), orphans its open tasks back to unassigned with a note, clears any reports_to references (subordinates become top-level), keeps message history, and posts a system message

### Notes

- New APIs: POST `/api/agents/update` (rename / change role), POST `/api/agents/remove` (remove member).
- Verified end-to-end: rename syncs board+config; remove cleans agents/config/tasks/relations; drag persists position across refresh and reset restores layout; board page shows the back button only outside embed mode.

## [v2.11.0] - 2026-10-04

### New Features

- **Workspace create-or-link choice in the project wizard**: when creating a project you now pick one of two workspace modes
  - **新建工作区 (create, default)**: a `workspace/` sub-directory is created inside the project folder — for brand-new projects with no workspace yet
  - **关联已有工作区 (link existing)**: fill in the path of a directory the project already has — the board links to it directly, and **no second workspace is created** (no more duplicate workspaces for projects that carry their own folder)
  - Linking a non-existent path fails with a clear error and the project folder is rolled back (no empty leftovers)
- Board data now records the resolved workspace (`project_info.workspace` + `workspace_status`: created / existing) so the org view can show where project files live; older projects get their workspace back-filled from `project_config.json` on load (string or `{path,name}` object shapes both supported)

### Notes

- Three scenarios verified via API: create (workspace/ created), link existing (linked, no duplicate folder), link missing path (error + rollback). Wizard radio switch verified in browser.
- No schema migration needed: legacy projects keep their workspace field as-is.

## [v2.10.0] - 2026-10-04

### New Features

- **CLI Mode — terminal-agent access (4th collaboration mode)**: `tools/agentnexus` command joins terminal agents (Claude Code / Codex / OpenHands) into any project
  - `onboard` registers the agent's identity into the project board; `status` / `my-tasks` read the project; `claim` / `update` / `accept` / `reject` drive tasks; `handoff` writes a structured handoff note on the task plus a board message; `send` mentions another agent; `conclude` persists a project-level decision
  - All CLI writes land in the same `collab_board.json` shared with prompt/API agents — one JSON, multiple entry points
  - Wake-up rights note added to README: prompt agents wake themselves, API agents can be woken but only talk, CLI agents can be woken AND actually execute
- **Project-level decisions** (`decisions[]` in the board JSON): new API `POST/GET /api/decisions`; the org view renders a "决策沉淀" panel in the right reading column with title / body / author / time / status
- **Task handoff field**: tasks accept a structured `handoff` object (note / by), written via CLI `handoff` and visible in CLI `my-tasks`
- **Project switch sidebar** (org view): left rail lists all projects with task counts, current project highlighted, one click to switch
- **Board as an overlay card**: the "看板" button now opens the task board as a full-screen iframe overlay on top of the org view with a "← 返回组织" close button (no more lost navigation); `embed=1` hides the board's own org-view button

### Fixes

- `depends_on` could be a string ("T25") instead of an array — task cards and org dependency lines now normalize both shapes (board crash on such tasks fixed)
- Task-id matching now compares `str(id)` so integer task ids work from the CLI

### Notes

- Desktop launcher (AgentNexus.app, Nothing-style icon) rebuilt on the Desktop: double-click detects port 8766, starts the server if needed, opens the browser, shows a notification.
- Org-view "加位子" / relation editing verified in browser on 三国指挥官 (10 nodes, zero console errors); test data cleaned afterwards.

## [v2.9.0] - 2026-10-04

### New Features

- **Organization view (new primary interface)**: each project now opens on an org-chart page (`org_chart.html`) instead of the task board
  - Nodes = project Agents (badge, name, role, current load); status (手上没活 / in_progress / blocked) is derived from tasks automatically
  - Hierarchical layout with Bezier connector lines: reporting lines (solid, from `reports_to`), dependency lines (dotted, from task `depends_on`), collaboration lines (dashed, from shared task chains)
  - Click any node to open a person card: current work, recent deliveries, reporting-relation editor, chat history + direct message box (messages are written to the same board JSON with an `@` mention)
  - Right-hand reading panel: waiting-on-you count, open items, recent activity feed (report / delivery / error tags derived from messages)
  - "+ 加位子" adds a new Agent inline (custom name + role); "编辑层级" mode lets you reassign anyone's supervisor
  - The task board remains as a second-level view (topbar link both ways); Dashboard home is unchanged
  - Fully compatible with the three skins (Nothing / Apple / Premium)
- **Manual reporting relations**: agents array now supports `reports_to`; new API `POST /api/org/relation` (batch) and `POST /api/org/position` (add position, reuse of register)
- **Backend fix**: `register_agent` now merges agents into the board by id instead of overwriting (previously an agent registration could drop board-only members)

### Notes

- Org layout is manually configured (supervisor selection) and connectors are auto-derived from existing task data (assignee + depends_on); no event-sourcing or scheduler introduced in this release.

## [v2.8.0] - 2026-09-19

### New Features

- **Unlimited custom Agents per project**: the project creation dialog no longer limits you to the fixed Planner/Builder pair
  - Dynamic agent rows: type any agent name (e.g. 品牌设计师, 前端开发, 测试工程师) and a role/identity line; add as many rows as needed, remove any row
  - Zames (the user/owner) stays fixed and always first
  - Backend `create_project` accepts custom dict lists `[{"id","name","role"}, ...]` alongside the legacy string list; the user agent is always inserted at the top
- **Custom-identity onboarding**: the per-agent onboarding guide is now generated from each agent's own name + role (a "品牌设计师" gets a 品牌设计师 identity brief, not a generic Planner/Builder script); built-in keyword detection still applies to planner/builder/user
- **Auto agent colors on the board**: custom agents get a stable color from an 8-color palette via id-hash (identity selector, task assignee, message sender), instead of falling back to one gray

### Notes

- Existing projects keep their current agents untouched (backward compatible).
## [v2.7.0] - 2026-09-18

### New Features

- **Modern Premium skin** (design-advisor baseline): a third theme built on the Modern Premium + Bento visual foundation
  - Near-black base `#0F0F10`, paper `#F6F3EC` (light mode, future), single coral accent `#E8493F`
  - Headings in **Georgia** serif, body in system font stack (Inter/Roboto/Arial/Space Grotesk excluded per spec)
  - Hairline borders (1px), 4/8/16/24 spacing rhythm, small card radius (6px), quiet gradient-free background
  - Nothing skin preserved untouched as the "retro pixel" skin; Apple Light unchanged
- **Three-state theme cycle**: theme toggle now loops `Nothing → Apple → Premium → Nothing` on both Dashboard and Board pages (localStorage key stays `agentnexus_theme`)

### Bug Fixes

- **Board stuck on LOADING after theme init**: `init()` ran before the new `const THEME_ORDER/THEME_NEXT_LABEL` declarations were evaluated (TDZ), throwing `Cannot access 'THEME_NEXT_LABEL' before initialization` and halting page bootstrap. `init()` is now invoked at the end of the script, after all const declarations.

## [v2.6.0] - 2026-09-18

### New Features

- **Theme System (Skin System)**: Two switchable skins — the original Nothing dark theme and a new Apple Light skin (inspired by Apple / Shimo Docs: clean, simple, high-clarity light UI)
  - Skin applied via CSS custom properties (`data-theme` on `<html>`); hard-coded colors refactored to theme-aware RGB variables (`--red-rgb`, `--panel-rgb`)
  - Apple skin uses system fonts (SF/Inter), Apple Blue `#007aff` accent, rounded corners, light gradient background
  - Theme choice persisted in `localStorage` (`agentnexus_theme`), shared across Dashboard and Board pages
  - Toggle button in both pages' top bars; architecture ready for more skins (just add another `[data-theme]` block)

- **Home Page upgraded to Dashboard**: `index.html` is now a command center overview instead of a plain project list
  - Metric cards: total projects, active projects, total tasks, completion rate, total agents, total messages (with today's count)
  - Enhanced project cards: progress bar + %, task breakdown (done/total, active, blocked), agent chips, last activity time, handoff-document tag, enter button
  - Auto-refresh every 15 seconds

### Backend

- `list_projects()`: added `active_count`, `blocked_count`, `todo_count`, `agent_count`, `progress`, `last_active`, `has_handoff` fields per project
- New `/api/dashboard` endpoint: aggregates cross-project stats (project count, active projects, task totals, completion, message totals incl. today, agent totals) plus the detailed project list

## [v2.5.0] - 2026-09-16

### UI/UX Improvements

- **Decluttered top bar**: removed glyph indicator bar and redundant top agent pills (identity lives in the bottom bar); subtitle now dynamically shows the active project name
- **Command bar compacted**: five controls collapsed into a MANUAL toggle + "CMD ▾" dropdown (copy check instructions / polling rules / onboarding guide)
- **Long message collapse**: messages longer than 260 chars collapse by default with an expand/collapse button, preventing chat flooding
- **Empty state guidance**: todo column shows "message @Agent in chat to create the first task" hint
- **Visual noise reduction**: removed logo red-dot blink and status-dot pulse; slowed AI thinking animation

## [v2.3.1] - 2026-08-30

### Bug Fixes

- **Agent selection not responding in onboarding step 1**: Clicking on agent options had no response
  - **Root cause**: `fetch('/api/agents/status')` API call failed with `TypeError: Failed to fetch`, resulting in empty agent list. Step 1 displayed "No agents" with no clickable options, making users think clicks were not responding.
  - **Fix**: Prioritize getting agent list from already-loaded `boardData.agents` instead of making a separate API call. Fallback to `/api/agents/status` only when `boardData` is not available. More reliable, reduces one API call, avoids fetch failure issues.
  - **Improvement**: Replaced inline `onclick` attributes with direct `addEventListener` click listeners on each agent option for more reliable event binding on dynamically generated HTML.
  - **Debugging**: Added `[Onboarding]` prefixed console logs at key points (agent list fetch, step render, agent click, selection) for easier troubleshooting.

### Technical Changes

- `openOnboardingGuide()`: Now reads from `boardData.agents` first, API call as fallback
- `renderOnboardingStep()`: Direct click event listeners on `.onboard-agent-option` elements after rendering
- Removed event delegation approach in favor of direct listeners for simplicity and reliability

## [v2.3.0] - 2026-08-30

### New Features

- **Foolproof Agent Onboarding**: Three-step guide panel makes onboarding new agents simple and intuitive
  - Step 1: Select the agent to onboard (from project agent list)
  - Step 2: Copy personalized onboarding guide, paste to AI agent
  - Step 3: Wait for connection, auto-detect agent status (refreshes every 3 seconds)

- **Personalized Onboarding Guide**: Generate identity-aware guide after selecting an agent
  - Document header clearly states "Your Identity": who you are, your ID, your role
  - Includes ID usage instructions for registration and messaging
  - Agent knows its role immediately upon opening the document, no manual补充 needed

- **@Message Webhook Push**: Real-time @message push after agent registers HTTP webhook
  - Independent of API mode: pushes as long as agent registered http webhook, no LLM API config needed
  - Push content: event type, project name, agent info, message details, board path, reply API
  - Successful push updates last_seen (connection confirmation), failed push marks offline

- **Agent Onboarding Script Example**: `examples/agent_example.py` (zero dependencies, Python standard library only)
  - `AgentClient` class: register, heartbeat, send message, claim task
  - `WebhookHandler`: receive @message push and auto-reply
  - CLI arguments: `--agent-id`, `--agent-name`, `--board-url`, `--project`, `--port`
  - Auto-register on startup, send online message, heartbeat every 30 seconds

- **Agent Registration API**: `POST /api/agents/register` (completed by Planner Agent, T8.1)
  - Register agent entry, supports `http` (webhook) and `session` (manual) types
  - Marks `connected=True` after registration, records `registered_at` and `last_seen`
  - Syncs agents list to board for consistency

- **Agent Real-time Status Display**: `GET /api/agents/status` (completed by Planner Agent, T8.3)
  - Returns for each agent: ID, name, role, online status, entry type, registration time, last active time
  - Agent status list in API settings modal (ONLINE/OFFLINE badges)

### Technical Implementation

- **Backend**:
  - `push_message_to_agent()`: push @message to agents with registered HTTP webhook
  - `_update_agent_last_seen()`: update last_seen on successful push (connection confirmation)
  - `_mark_agent_offline()`: mark agent offline on push failure
  - `check_agent_online()`: check online status based on last_seen (default 5-minute timeout)
  - `_build_onboarding_md()`: supports `agent_id` parameter, generates personalized onboarding guide
  - `/api/onboarding`: supports `agent_id` query parameter, dynamically generates personalized guide when parameter present

- **Frontend**:
  - Three-step guide panel (Nothing style: dark background, red accent, pixel font)
  - Agent selector: list shows name + role, click to select (highlight + checkmark)
  - Step indicator: active/done states, progress visualization
  - Auto status detection: step 3 calls `/api/agents/status` every 3 seconds to refresh

- **Refactoring**:
  - `trigger_agent_if_mentioned()`: webhook push moved before API mode check, executes independently
  - Server startup: multi-project mode no longer requires default project data file to exist, only warns without exiting

### Testing & Verification

- **End-to-end test passed**: register → push → reply full flow normal
  - Agent registration successful (connected=True, entry correct, last_seen has value)
  - @message real-time pushed to agent webhook
  - Agent auto-replies after receiving, reply appears in board message list

- **Personalized guide test passed**:
  - planner personalized guide: header clearly states "You are Planner Agent, ID is planner, role is task breakdown/solution review..."
  - builder personalized guide: header clearly states "You are Executor Agent, ID is builder, role is frontend/backend development..."
  - Generic guide (without agent_id): no identity header, compatible with existing functionality

### Compatibility

- Legacy project upgrade: no migration needed, auto-compatible
  - When legacy project has no AGENT_ONBOARDING.md, /api/onboarding dynamically generates
  - Legacy project agents config compatible (both string list and object list supported)
- Generic guide functionality preserved: behavior unchanged without agent_id parameter
- API mode auto-trigger functionality preserved: webhook push and API mode trigger don't interfere, can be used simultaneously

## [v2.2.0] - 2026-08-30

### New Features

- **New Message Notification Sound**: Auto-plays "ding" sound when other agents send new messages
- **Web Audio Synthesis**: Pure frontend synthesized notification sound, no audio file needed
  - 880Hz (A5) + 1320Hz (E6) sine wave叠加
  - Exponential decay 0.3 seconds, crisp "ding" sound
- **Smart Trigger**:
  - Only triggers for messages not sent by self
  - System messages don't trigger
  - Batch new messages only ring once, won't continuously annoy
- **Notification Sound Toggle**: New "Notification // NOTIFICATION" section in API settings modal
  - Enabled by default
  - localStorage persistence, doesn't lose on page refresh
  - Play a preview when enabling, so you know the sound effect

### Technical Implementation

- Web Audio API (OscillatorNode + GainNode) synthesizes notification sound
- Polling detects message count changes, compares lastMessageCount
- AudioContext lazy loading, only initializes after user interaction (complies with browser autoplay policy)

### Testing

- Page loads normally (HTTP 200) ✅
- Key functions and variables complete ✅
- API endpoints normal ✅
- Notification sound wav file generation verification passed ✅

## [v2.1.1] - 2026-08-30

### Fixes

- **Delete Project Button Visibility**: Original "delete button only shows on project card hover" (`opacity:0`) made entry undiscoverable
  - Top-right ✕ changed to always visible (semi-transparent, highlight on hover)
  - Added prominent red "Delete Project" text button at bottom of project card
  - Deletion flow unchanged (two-level confirmation: scope selection + enter project identifier)

## [v2.1.0] - 2026-08-30

### New Features

- **Project Deletion Feature**: Support deleting projects in project management panel (index.html)
  - Delete button shows on project card hover
  - Two-level confirmation flow to prevent accidental deletion
  - Two deletion scopes:
    - **Data only**: Delete data files like board/config/logs, keep empty project folder, can be reconfigured
    - **Delete everything**: Completely delete entire project folder, requires entering project identifier for second confirmation
  - Backend API: `DELETE /api/project`

### Backend

- Added `delete_project()` function, supports data_only and all deletion modes
- Added `do_DELETE()` method to handle DELETE requests
- Added `_handle_delete_project()` to handle deletion logic, including security validation
- Security mechanism: deleting entire project requires entering project name confirmation, prevents misoperation

### Frontend

- Added delete button at top-right of project card (shows on hover)
- Delete confirmation modal: warning prompt + scope selection + input confirmation
- Nothing style UI, red danger styling
- Auto-refresh project list after successful deletion

### Testing

- data_only mode: correctly deletes data files, keeps empty folder ✅
- all mode without confirmation: correctly rejects, prompts to enter project name ✅
- all mode with wrong confirmation name: correctly rejects ✅
- all mode with correct confirmation name: successfully deletes entire folder ✅

## [v2.0.0] - 2026-08-29

### Initial Official Release

Three-layer progressive multi-agent collaboration board full-feature baseline.

### Features

- **Hybrid Mode**: Message `@` detection + one-click copy trigger command (manually drive agents)
- **API Mode**: `@` auto-triggers corresponding agent reply after configuring LLM API
  - Agent role definition + system prompt template
  - AI reply structured action parsing (`UPDATE_TASK` / `CLAIM_TASK`)
  - Anti-loop mechanism (chain depth limit + message deduplication)
  - API failure handling and auto-degradation (3 consecutive failures auto-close auto-trigger and notify)
  - api_key encrypted storage (base64 / environment variable XOR, no plaintext on disk)
- **Schedule Mode**: Scheduled polling scans unprocessed `@` messages (5/15/30/60/1440 minutes five levels)
  - Processed message deduplication (`processed_msg_ids.json` persistence)
- **Engineering Foundation**: Cross-process file lock (fcntl) + atomic write to prevent corruption; call log `api_calls.log`; frontend API settings modal, schedule settings, thinking animation, auto-trigger toggle

### Documentation

- `README.md`: usage instructions for three modes
- `PROJECT_SUMMARY.md`: project summary and improvement suggestions

### Known Limitations

- T2.5 real API key connectivity test pending verification (requires real key)
- End-to-end AI reply verification under real key pending verification
