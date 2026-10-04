# Changelog

This file records version changes for AgentNexus. Version numbers follow Semantic Versioning (SemVer): `MAJOR.MINOR.PATCH`.

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
