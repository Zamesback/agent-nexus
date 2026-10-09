## v2.20.13 — Hosted replies are displayed as the markdown they already are

- **Fixed**: a hosted member's reply is markdown — tables, ordered lists, headings, fenced code — but the chat bubble printed it through `esc2()` as literal text, so users read raw `| column | column |` pipes and `**bold**` asterisks. This was the common case, not an edge case: 13 of the 78 replies in the real 设计总负责DSH-1 history contain a table.
- Assistant replies now render through `renderMiniMarkdown()`, the same zero-dependency renderer the deliverable preview has used since v2.20.6. Your own messages are deliberately *not* rendered — what you type is what you see. Thinking blocks, error lines and `thinking` entries stay plain too, so process output is not dressed up as prose.
- The renderer gained what chat needs and the artifact preview could live without: ordered lists now emit `<ol>` (previously every list item became `<ul>`, losing 1/2/3 numbering), a non-1 start becomes `<ol start="n">`, an ordered↔unordered switch closes the previous tag, and `- [x]` / `- [ ]` render as real (disabled) checkboxes.
- A bubble containing a table drops the 88 % width cap (`.md-wide`) so the table is not squeezed into a column, and tables scroll horizontally on their own when they are still too wide.
- Streaming is covered: every `text` event re-renders the bubble and re-syncs the `md-wide` state, so the reply is formatted while it arrives, not only after it finishes. The same path serves history reload, the manual chat, and the discussion room (`drAppend`). Since this is entirely client-side, a page reload picks it up — no server restart needed.
- Escaping is unchanged and still first: the renderer escapes before it parses, so `<>` and quotes in agent output cannot become markup. Covered explicitly.
- **Tests**: new `tests/t9_chat_markdown_test.js` — 36 cases loading the real functions out of `org_chart.html` rather than a copy: table structure, separator-row filtering, per-cell inline markup, ordered lists and `start`, ordered↔unordered switching, task checkboxes (checked / unchecked / capital X), XSS escaping, user messages staying verbatim, fenced code blocks not being read as tables, `thinking` staying plain, custom speaker names, and `dshSetBody` adding *and* removing `md-wide` as the streamed text gains and loses a table. T5 27/27, T6 12/12, T7 22/22, T8 13/13.

## v2.20.12 — Approval countdowns are measured in the right unit

- **Fixed**: the approval bridge sends `timeoutMs` in milliseconds (120000 by default) while everything on the server compares against `time.time()`, which is in seconds. Two places used the two together without converting, so both were wrong by a factor of 1000:
  - `_dsh_approval_pending` returned `remainingMs` as `timeout - elapsed_seconds`, then multiplied by 1000 — giving ~120000000 instead of ~120000. This one was visible: the cross-page reminder in the lower-right corner of any page announced "about 1999 min 25 s until auto-reject", while the approval card inside the member card (which computes its own countdown from the SSE `timeoutMs`) simultaneously showed the correct "1 min 41 s left". Both were describing the same request.
  - `_dsh_approval_state` compared elapsed **seconds** against `timeoutMs + grace`, so the server-side fallback expiry needed 120003 seconds — about 33 hours. It was meant to be the belt-and-braces path for when the bridge never reports its own timeout (for example if the bridge crashed), which is exactly the case where it never fired.
- Both now convert at the boundary: `_dsh_approval_pending` computes `elapsed_ms = (now - ts) * 1000` and clamps at zero, and `_dsh_approval_state` compares against `timeoutMs / 1000 + grace`. Requests without a `timeoutMs` (an older bridge) are still never expired locally, since only the bridge knows their deadline.
- **Tests**: new `tests/t7_approval_units_test.py` — 22 cases pinning the magnitude of `remainingMs` (a fresh 120000 ms request reports ~120000, explicitly not 120000000), the elapsed-time decay, the clamp-at-zero window between the deadline and the grace period, the local fallback firing just after `timeoutMs/1000 + grace` and *not* before it, and the older-bridge and already-resolved/expired paths. T5 27/27, T6 12/12.
- The reminder bubble now also enforces the invariant locally: remaining time can never exceed the configured timeout. That is not a workaround for the server bug so much as a bound that must hold anyway, and it means the corrected wording appears as soon as the page is reloaded rather than waiting for the server process to be restarted. Covered by `tests/t8_approval_toast_test.js` (13 cases), which loads the real script in a sandbox — including the exact value that was on screen during the incident (119965000 ms with a 120000 ms timeout, previously "1999 min 25 s", now "2 min 0 s").

## v2.20.11 — A hosted member no longer goes silent after its working directory moves

- **Fixed (regression from v2.20.10)**: dsh binds a session to the directory it was recorded in and refuses to resume it anywhere else — `dsh: session "…" was recorded in "A", not "B"`. Changing the hosted agent's `cwd` from the source checkout to the project data directory therefore made **every session recorded before that change unresumable**. The child process printed the error, produced nothing, and sat there until the 180-second idle watchdog killed it; the old code then classified that as a delivered reply (`ok=True`, zero tokens), so the UI showed neither an answer nor an error. Two live members (设计总负责DSH-1, DSH2) were affected: they simply stopped answering.
- Added the pre-flight `_dsh_session_usable(session_id, cwd)`: before spawning, it checks where the session actually lives (`~/.dsh/sessions/<slug>/<session_id>`) against where it would have to live for this `cwd`, and drops the session when they disagree, so the member starts a fresh one instead of failing. `_dsh_cwd_slug` replicates dsh's directory naming (separators → `-`, non-ASCII → `~XXXX` as UTF-16 code units, wrapped in `--…--`) and is verified against every ASCII directory in a real session store. The mismatch is logged (`会话与当前工作目录不匹配，改用新会话`).
- Added a safety net in the stderr monitor: if that error still reaches us, kill the process immediately and return failure instead of waiting out the watchdog, so callers retry with a fresh session rather than treating the run as delivered.
- The **manual chat path** now uses the same rule v2.20.9 introduced for auto-wake: a process that exits cleanly having said nothing is not a success. It retries once on the same session, then once on a fresh one — and the fresh attempt now carries the same context, since a brand-new session has no memory of its own. If every attempt still comes back empty, the card receives an explicit error instead of silence.
- Cost of the rotation: a rotated session loses dsh's internal conversation memory for that member. The board, the chat log (`dsh_chat/<member>.json`) and the per-turn identity anchor are untouched, and the member card still sends the last few turns as context, so the member keeps working — it just no longer recalls older turns.
- **Tests**: new `tests/t6_session_guard_test.py` — 12 cases covering directory-name replication (ASCII and non-ASCII), the exact recording-directory mismatch that caused this, and the boundary cases that must stay permissive (unknown session, empty id, no `cwd`). T5 regression 27/27 and the auto-wake end-to-end suite 39/39 still pass. Verified live: the guard logs the rotation and both members answered immediately, inside their own project directory (`"cwd": ".../agent-collab-projects/三国指挥官"`).

## v2.20.10 — Hosted agents work inside their own project, not inside AgentNexus

- **Fixed**: the hosted (dsh) subprocess was launched with no `cwd`, so it inherited the server's working directory — which launchd pins to the AgentNexus source checkout. Every hosted agent, in every project, therefore worked in the same directory, and their relative-path output piled up in one shared `dsh_work/` inside the tool's own repository. Cross-project contamination was real, not theoretical: a throwaway test project's agent dropped `dsh_work/wakeup_check_smoke.md` into the AgentNexus repo, next to 59 files from another project's wall/tower work.
- `_dsh_run` now passes `cwd=_dsh_run_cwd(project)` — the project's own data directory (`COLLAB_PROJECTS_ROOT/<project>`, the folder that already holds `collab_board.json`), resolved through the existing `get_project_path` traversal guard. An unresolvable project still returns `None` and inherits the old behaviour, so nothing breaks for callers without a project. The resolved directory is now logged (`dsh 工作目录: …`) so this is diagnosable from `/tmp/dsh_server.log`.
- The deliverable registry follows automatically: relative paths are resolved against the `cwd` reported in dsh's `session` event, which is now the project directory, and that directory was already on the artifact-preview allow-list. Deliverables written with `write` / `edit` stay previewable in the member card.
- Verified: the child process really lands in the project directory (a probe inside the fake CLI, run through the production `Popen` path, reports the project dir on every invocation); on the live server a wake logs `dsh 工作目录: /Users/zames/Desktop/Project/agent-collab-projects/awake-smoke`; 39/39 auto-wake suite and 27/27 T5 regression still pass.
- Note: `tools/agentnexus` is HTTP-based and cwd-independent, and the profile's system prompt already tells agents to fall back to its absolute path when it is not on `PATH`, so the change does not strand any agent. The old shared `dsh_work/` directory is left in place (it is gitignored); its contents are one project's temporary build scripts, not tracked deliverables.

## v2.20.9 — An auto-wake that produces nothing now retries instead of passing for success

- **Fixed**: when the upstream model is briefly unavailable (`Upstream request failed: Model is unavailable.` — the flaky 400 the handover doc already lists as a known issue), the dsh subprocess still exits cleanly and emits an **empty** `final`. `_dsh_run` therefore reported `ok=True`, the auto-wake logged a cheerful `自动唤醒结束: ok=True` with zero tokens, and the member silently did nothing: no board reply, no chat-history entry, nothing for the human to notice. A round that delivered no text is now treated like a failed round and retried once on a fresh session (`自动唤醒本轮无输出（上游模型可能不可用），换新会话重试`).
- The old retry was gated on `session_id` being non-empty, which skipped the retry in precisely the transient case: switching a member's model clears its stored session, so the very next wake was a cold start and the guard suppressed the second attempt. The retry passes a fresh session anyway, so the gate is gone and a cold-start failure retries too.
- Verified live on the real stack: a hosted member was `@`-mentioned via `POST /api/messages`, woke up on its own, ran a full 132,295-token round, and wrote its conclusion back to the board as an `auto_reply` carrying `auto_wake: true` — the first end-to-end confirmation of auto-wake against a real dsh process rather than a fake CLI. Re-run against an unavailable model, the same path now logs the no-output retry and correctly posts **no** empty reply.
- Also verified: 39/39 hosted-member auto-wake suite, 27/27 T5 regression suite.

## v2.20.8 — API-mode replies land on the right board, and the regression suite runs again

- **Fixed (data correctness)**: the API-mode worker was project-blind. `_trigger_agent_worker` called `load_board()` / `load_config()` with no project, so a `@`-triggered API agent's reply — and any `[UPDATE_TASK]` / `[CLAIM_TASK]` action it emitted — was written through the single-project fallback path, i.e. to a board next to `server.py`. In a multi-project deployment that file doesn't exist, so the worker raised `FileNotFoundError`, the reply was lost, and the whole run was silently a no-op. `project` is now threaded through the entire chain (`_trigger_agent_worker` → `load_board` / `load_config` / `call_llm` / `parse_and_execute_actions` / `_execute_update_task` / `_execute_claim_task` / `_append_agent_message`), every new parameter defaults to `None`, and the legacy single-project path is unchanged. Verified: in a multi-project layout the reply and the `UPDATE_TASK` action both land on the correct project board, and no stray board file appears next to the server.
- **Fixed (lost notification)**: the API-degradation notice (auto-trigger switched off after 3 consecutive failures) was dropped in *every* path, because `call_llm` never passed a project down and `_append_system_message` bailed out whenever it had none. The guard is now "skip only if the target board file does not exist", which keeps the single-project deployment working, keeps the multi-project case from inventing a phantom board, and no longer swallows the notice. `call_llm` and `_record_api_error` also read and write the project's own config now, so the degradation state (`auto_trigger=False`, `consecutive_failures`) is recorded against the right project.
- **Fixed**: `tests/t5_regression_test.py` could not even start — it put `tests/` on `sys.path` and then did `import server`, while `server.py` lives in the repository root (`ModuleNotFoundError`). The repository root is now on the path too, so the suite runs: **27/27 pass**. The one assertion that had been failing (`T5.4-3` degradation notice on the board) was the real bug above, not a stale expectation.
- Verified: 27/27 T5 regression, 39/39 hosted-member auto-wake, plus dedicated multi-project routing checks for the reply path and the degradation notice (correct board, correct config, no phantom files).

## v2.20.7 — Hosted members wake up on their own

- **The missing wake path**: a terminal-hosted (dsh) member could only be woken by a human typing into its member card or by the discussion-room rotation. Being `@`-mentioned or handed a task on the board did nothing — the mention trigger only understood `http` webhooks and the API mode (`api_config.enabled && auto_trigger && api_key`), and no `entry_type == "dsh"` branch existed anywhere. Now `trigger_dsh_wake` runs a headless round of `_dsh_run` and the member really acts, with no human in the loop.
- **What wakes a member**: a board message that mentions it (via `@id` / `@name` in the body, or via the `to` field) or a task being assigned to it while that task is `todo` / `in_progress`. Both routes share one predicate, `_dsh_is_terminal_member` — the same one `_handle_dsh_chat` now uses, so the chat guard and the wake guard can no longer disagree.
- **Runaway protection, in five layers**: (1) a wake prompt that tells the agent to read the board first, act or explain in one line, and **not** to `@` other members for hand-off (hand-off goes into a task's `assignee`); (2) replies are written back with `type: "auto_reply"`, and `system` + `auto_reply` messages are excluded from waking anyone — the feedback loop is cut at the source; (3) rolling-window throttles of **3 wakes per member / 8 per project per 300 s**; (4) a member that is already running simply declines the new wake instead of queueing, so work can't pile up; (5) only a message inside the board's **last 20** can wake anyone — the legacy single-project scheduler scans back 100 messages, so without this, flipping the switch on would have let a backlog of old `@`s start every hosted member at once (and a rejected stale message doesn't consume throttle budget either).
- **Fully visible and switchable**: `GET/POST /api/dsh/auto-wake` reports and flips the per-project switch (stored as `dsh.auto_wake`), and the member card shows a `⚡ 自动唤醒 / ⛔ 唤醒关` toggle next to the approval-sound button. `protocol.mode == "off"` (收工) suppresses all wakes, as does the switch itself.
- **Same accounting as a manual message**: the user-side prompt and the agent's `final` are persisted into `dsh_chat/<member>.json` (the user entry carries `auto_wake: true`), the session id is reused so context carries over, and usage is accumulated into `dsh.members.<id>.usage` exactly like an interactive round.
- Verified with 39/39 end-to-end tests plus 7/7 stale-guard cases against an isolated server and a fake CLI (zero model cost): the terminal-member predicate, switch round-trip, both throttle ceilings, every refusal path, a real `@` wake that lands an `auto_reply` plus chat history plus usage, the feedback-loop cut for `system` / `auto_reply` types, 收工 suppression, task-assignment wake (and its `done` / non-hosted negatives), and concurrent wakes collapsing to one run. Also verified in the browser: the toggle flips the server state both ways and renders its on/off label.

## v2.20.6 — Approval loop closed, deliverables preview, drag-resizable layout, message-schema compatibility

- **Approval loop, closed end to end**: the bridge used to fail closed silently. On timeout it now emits `agentnexus_approval_expired` (with the real `timeoutMs`), and a late answer arriving after expiry emits `agentnexus_approval_late` exactly once — but only for requests *this* process expired, so a shared answer file used by several members can't cross-talk. The server tracks every live approval (`_DSH_APPROVALS` + `_dsh_approval_register/_expire/_state`, with a 3 s grace window so an older bridge without `timeoutMs` is not falsely rejected), converts both events into `approval_expired` / `approval_late` SSE frames, and `POST /api/dsh/approve` now answers **409 `{"ok":false,"stale":true}`** instead of a cheerful `ok` when the request is already dead — the UI no longer tells you「已批准」for an approval that no longer exists. The card is marked「⌛ 已超时」, its buttons are disabled, and the tip says the action must be re-triggered.
- **Honest timeout copy**: the card's hard-coded「5 分钟内未操作将自动拒绝」is gone; the countdown is driven by the `timeoutMs` the bridge actually reports (120 s), with a live per-second countdown and a local expiry fallback.
- **Approval alerts that follow you across pages**: new `approval_alert.js`, loaded by all three pages, polls `GET /api/dsh/approvals` every 3 s and — for any page — plays a three-note chime (WebAudio, no audio asset), re-chimes every 8 s until handled, fires a desktop notification, prefixes the tab title with「⚠️ 待批准 N」, and shows a bottom-right overlay with working **批准 / 拒绝** buttons and a remaining-time countdown. On the org chart it defers to the in-card approval card when that card actually rendered, and falls back to the overlay when it didn't (the card container only exists while the member modal is open). A `🔔 提醒开 / 🔕 提醒关` toggle persists in `localStorage`; browsers require one user gesture before audio is allowed, so the first click/keypress unlocks it and requests notification permission.
- **Deliverable preview for hosted agents**: `_dsh_run` now watches `tool_call` events and records every file an agent writes (`write` / `edit` / `present` / `apply_patch` / …) into `<project>/dsh_artifacts.json` — absolute path, tool, timestamp and a write count, deduped and capped per member. `GET /api/dsh/artifacts` lists them (kind, size, mtime, relative path) and `GET /api/dsh/artifact?path=…` serves the bytes. Access is double-gated: the path must be in that member's own registry **and** inside one of three allowed roots (the project workspace, the project data directory, the AgentNexus checkout), so an agent cannot be tricked into serving `/etc/passwd`. The member card gains a「交付物」section for terminal-hosted members; clicking an entry opens an in-page preview — images (with automatic enlargement for tiny sprites), PDF, sandboxed HTML, a zero-dependency mini markdown renderer, pretty-printed JSON, line-numbered text/code, and native audio/video players, plus download and open-in-new-tab.
- **Drag-resizable layout**: two vertical splitters let you resize the project list / org canvas / data panel on the org chart, and the person card gains a vertical splitter (basic info ↔ chat) and a horizontal one (chat log ↔ terminal chat). Ratios persist per browser in `localStorage`, double-click resets, and both panes are clamped to sane ranges.
- **Message-schema compatibility (read *and* write)**: `messages[]` has drifted into two field families — the server's `sender/content/timestamp/to` and agents' hand-written `from/body|text/ts/to` — and the UI only understood the first, so 42 messages rendered with a blank body and no sender, and `m.to === agentId` was always false for array targets (30 messages addressed to several people were invisible to every one of them). Both pages now normalize on load: field aliases, numeric-second timestamps, and `to` as a string, a space/comma-separated list, or an array. Recipients are resolved through the roster (id, display name, or historical aliases like `builder4` →「Builder 4试玩反馈」), and broadcasts (`all` / `@all`) reach everyone. `POST /api/messages` normalizes the same way and **no longer discards `to`**, and `detect_mentions` now also honours `to`, so a dispatch message wakes its target without needing an `@` in the body. Verified against the live 三国指挥官 board: 549 messages, 0 blank bodies, `DSH2` 0 → 28, `user` 4 → 55, all timestamps parseable.
- **Isolated test runs no longer pollute the real log**: the dsh log path is overridable via `AGENTNEXUS_DSH_LOG`.
- Verified: 7/7 bridge integration tests (timeout, expired/late emission, once-only late reporting, other members' answers ignored), 9/9 server approval-state tests, 22/22 `_dsh_run` pipeline tests against a fake CLI, 57/57 artifact-registry tests, 18/18 artifact `_dsh_run` integration tests, 20/20 message-normalization tests, plus browser runs of the approval card, the drag splitters, the deliverable list and the image preview.
- Note: `README.md` still describes the v2.10 feature set; per the project's release convention it is refreshed in one pass at the next major release.

## v2.20.5 — Fullscreen modal fills the viewport + chat auto-scroll to latest

- **Fullscreen now really fills the screen**: `.modal-mask.fullscreen` also overrides `#personCard` — the ID selector had higher specificity than the class rule, so on wide screens the modal stayed at 920px/94vw and the chat column looked tiny.
- **Chat opens at the latest message**: `openPerson` / `loadDshHistory` scroll both the chat list and the terminal feed to the bottom, with a `document.fonts.ready` pass plus a 300 ms fallback so late font/layout growth can't leave the view stuck at the top.

## v2.20.4 — Person-card fullscreen layout fix

- `.modal-mask.fullscreen` uses a flex column so `.person-body` fills the remaining viewport height; the left/right panes drop their fixed 56/58vh caps and stretch with internal scrolling, and the chat input sticks to the bottom.
- The left pane widens to 380px in fullscreen.

## v2.20.3 — Approval timeout + process-group cleanup

- **Approval timeout**: the approval bridge (agentnexus-approval-bridge) already fails closed on its own after its `timeoutMs`, but 5 min was longer than the 180 s idle watchdog — so an unanswered approval got the process killed by the watchdog while it was legitimately waiting for a human decision. The bridge timeout is now 120 s (profile template + existing member profiles updated), so an unanswered approval auto-rejects quickly and the process resumes/ends without conflicting with the watchdog. Deny-by-default stays safe — no auto-approve.
- **Process-group cleanup**: `_dsh_run` now spawns dsh with `start_new_session=True` and kills via `os.killpg` (process-group signal) before the per-pid kill fallback, so tool child processes (bash etc.) can't survive as orphans holding the session write handle. Verified: after final, 0 lingering processes.
- Verified smoke: model back online (deepseek-v4.1-flash), simple chat returned "ok" with final persisted, kill_after_final terminated cleanly, 0 residuals.

## v2.20.2 — True per-member queuing + long-task watchdog fix

- **Fixed (critical)**: the per-member lock was never actually shared — `_DSH_LOCKS = {}` was being assigned as a local variable inside each request thread, so every concurrent message got its own lock and the "queue" never existed. A follow-up message could kill the in-flight process mid-work (its reply lost). The lock is now stored on module globals, so consecutive messages to the same hosted member are truly serialized: the next one waits until the previous run finishes and persists, then starts with the full conversation history — old work is never interrupted mid-run, and the new process naturally inherits context (verified: a follow-up message repeated the previous round's conclusions from session memory).
- **Fixed**: model verify showed the wrong model — `--dump-config` was run without the model-lock overlay, so it reported a stale profile model (space-bunny-free) while the run actually used deepseek-v4.1-flash. verify now mounts the same overlay as the run.
- **Fixed**: watchdog was a fixed 180 s blind kill — long deep-dive tasks (dozens of tool calls, 3–5 min) were terminated mid-work with no final reply. It is now an idle watchdog: it only kills after 180 s with no events at all, so active long tasks run to completion. Partially-streamed text already persists via the v2.17 fallback.
- Verified end-to-end on 三国指挥官 / 设计总负责DSH-1: a long analysis task ran ~2 min to a full structured report (final persisted); a follow-up queued behind it, started right after, inherited context, and its in-progress work triggered the approval bridge (sandbox-write request) as designed — no lingering processes.

## v2.20.1 — Fix: consecutive messages to a hosted agent going unanswered

- **Root cause**: dsh is an agent loop — after streaming its final reply the process stays alive (it won't exit on its own), still holding the session write lock. A second message arriving while the first process is alive spawned a new process with the same `--session-id`, hit `session "…" is already owned by an active write handle`, and hung with no reply — exactly the "断断续续 / 你还在吗" no-response behavior the user hit with 设计总负责DSH-1.
- **Fix**: `_dsh_run` now kills any lingering process of the same member profile before spawning the new one, so every new message starts a clean session and always receives a reply. The displaced run's user message is already persisted on arrival (v2.17), and the most recent message always gets a live answer.
- Verified: fired a long task then, 6s later, a "you still there?" follow-up — the follow-up replied with a fresh board snapshot (任务 57 个，进行中 3 / 待做 5 / 完成 46) and the reply was persisted to chat history.

## v2.20 — Discussion Room: real multi-agent conversation

- **Discussion Room v1**: on the org chart page, a new「讨论室」top-bar button opens a discussion room where multiple terminal-hosted (dsh) members actually converse with each other — not just each reply to the same prompt. A moderator loop wakes one member at a time, hands each the full transcript so far, and instructs each speaker to respond to the previous speaker's point before adding their own.
- **Moderator round-robin**: `POST /api/discuss/start` creates a room; the server rotates through participants one turn each, waking each dsh session with the complete history, and streams every event over SSE (`discuss_msg / discuss_status / discuss_activity / discuss_thinking / discuss_error / discuss_round_limit / discuss_end`). Start / resume / stop / round-limit / chat endpoints plus a state snapshot API let the frontend recover after closing and reopening the room.
- **「三轮后结束」button**: when checked, the discussion locks to 3 rounds and each wake prompt appends「请尽量收敛观点，3 轮后讨论将结束」; unchecked, the discussion continues indefinitely (pure talk, no tool calls, so token cost stays modest).
- **Only terminal-hosted members participate**: guide/API members are greyed out in the participant list (there is no process to wake); a manual speak-for-them flow is planned for v1.5.
- **Discussion-mode isolation**: every wake prompt carries a hard【讨论模式】rule — speak only, no commands/tools/files — and `_dsh_run` gained a `kill_after_final` flag so a round ends the instant the final reply streams out (dsh is an agent loop that won't exit on its own; previously a finished round could idle for the full 180s watchdog).
- **Live discussion view**: full-screen room with participant status dots (speaking ● / waiting ○ / done ✓), the streaming chat feed (reusing the dsh message styles plus activity/thinking/error events), and a bottom input so the user can jump in at any time (interjected messages land in the transcript).
- Verified end to end: 2 hosted members × 3 rounds — each speaker explicitly acknowledged the previous one (「我先回应 DSH-1 的观点…」「你这条口径纠正，我接受，承认我表述偷懒」), the discussion terminated on `round_limit`, and the browser UI covered start → live view → end banner → reopen recovery.
- Known limitation: room state is in-memory (per server process); restarting the server or switching projects clears it. Persisting transcripts to the board is planned for a later iteration.

## v2.19.3 — Terminal-hosted agents: work-in-progress visibility

- **Live progress indicator**: the chat panel now renders dsh `status` events — a pulsing「⏳ 正在工作中 · 第 N 步…」appears while the agent is executing a step and disappears on `turn_end` (previously the panel just sat silent while the agent worked).
- **Collapsible thinking blocks**: `thinking` events render as foldable「🧠 思考 #N」blocks (default collapsed; click to read the full reasoning).
- **Watchdog instead of deadlock-prone wait**: `proc.wait(timeout=180)` sat *after* the stdout read loop, so a child that stopped writing but stayed alive blocked the thread forever and the timeout never fired. A watchdog thread now kills the child at 180s, unblocking the read loop; anything already streamed is treated as delivered.
- **Upstream errors are now visible**: a stderr monitor detects model failures (`Model is unavailable` / upstream 400), kills the hung child, and emits a `dsh_error` event — the panel shows「⚠️ 模型服务暂不可用…」instead of hanging on「回复中…」forever. A `dsh_done` with no reply also surfaces a failure notice instead of staying silent.
- Verified end to end: activity indicator shows on `step_start`; an upstream model outage was caught, surfaced to the panel, and the hung process reaped within ~2s.

## v2.19.2 — Fix: hosted-agent replies disappearing from chat history

- **Replies persist in real time**: the assistant reply is now written to the member's chat history the moment the `final` event streams out — no longer only after the dsh session fully exits. Long-running tasks used to blow past `proc.wait(timeout=180)`, the run was marked failed, and the already-delivered reply was never saved; refreshing the page then showed only your own messages.
- **Timeout no longer swallows delivered replies**: if a dsh session exceeds 180s, the server now kills the child process and returns success for anything already streamed, instead of marking the whole turn failed.
- **Dedupe guard**: the legacy end-of-run append is skipped when the reply was already persisted via the `final` event, so a reply is never written twice.
- Verified: sent a message, killed the client connection at 20s (simulating a refresh mid-run) — the assistant reply was already in `dsh_chat/<agent>.json` when the history was read.

## v2.19 — Hardened identity anchoring for terminal-hosted agents

- **Stronger identity block in every hosted profile**: the profile template's persona suffix is rewritten from a single casual sentence to a placeholder-anchored identity block — `{PROJECT}/{NAME}/{ID}/{ROLE}` — that states who you are (name, ID, role, reporting line) **and** who you are not ("you are not any other member; their messages, tasks, claims and identity tags are not yours; never speak or claim on their behalf"). Self-reference convention is explicit ("always self-identify as 我（{NAME}）") and the `agentnexus --name` flag must be the member ID.
- **Placeholder-based profile generation**: `_dsh_ensure_profile` now substitutes `{PROJECT}/{NAME}/{ID}/{ROLE}` instead of matching an exact Chinese sentence — the replacement can no longer silently fail if the template wording changes.
- **Legacy profile upgrade**: `_dsh_ensure_profile` detects an old-style identity sentence (un-injected template text) and rebuilds the member profile from the new template automatically.
- **Per-run identity anchor**: `_dsh_run` now prefixes every dsh session input with a 【身份锚定】 identity anchor (project / name / ID / role + "you are not any other member") — so identity holds even before a profile rebuild.
- Verified end to end: member「设计总负责DSH-1」answered "who are you" with its own ID/name, listed planner as a peer (not itself), and committed to acting only as DSH-1.

## v2.18 — Human-in-the-loop approval for terminal-hosted agents

- **Real approval bridge for dsh**: a terminal-hosted (dsh) agent that hits a sandbox permission wall (e.g. writing outside the workspace) used to fail silently — headless mode has no approval channel, so escalation requests were auto-rejected. Now a plugin (`tools/approval-bridge`, mounted into the dsh CLI via profile patch) intercepts the engine's `approval/request` event and emits it as NDJSON to stdout.
- **Server relays approvals to the UI**: `server.py` forwards the event to the member chat panel as a typed `approval` SSE event; a new `POST /api/dsh/approve` writes the user's decision to a shared answer file that the bridge polls.
- **In-tool approval card**: the member card renders「⚠️ 需要你的批准」with the tool name and the escalation reason, plus **批准 / 拒绝** buttons and a clear「5 分钟内未操作将自动拒绝（安全默认）」notice. Approving answers `allow-once` (the exact command re-runs with full access); rejecting fails closed. This is a one-shot allow — no blanket `allow-always` in v1.
- **Profile self-healing**: `_dsh_ensure_profile` now syncs the approval-bridge patch and plugin symlink onto legacy member profiles, and new hosted members get both at creation — so existing members keep working after upgrade.
- Verified end to end: sandbox-denied bash → escalation request → approval card in browser → 批准 → file written by the agent → card marked「✓ 已批准」.

## v2.17.1 — Terminal-hosted member detection fix (dual-field compatibility)

- **Access-type mismatch fix**: a terminal-hosted (dsh) member could end up with `entry.type=session` when the agentnexus CLI onboard (8767 inbox) re-registered the same identity, breaking chat. Detection now accepts the top-level `entry_type === 'dsh'` and the member's `dsh.members` config as authoritative too, so a hosted member stays chat-enabled no matter what `entry.type` says.
- **Member badge**: the invite-mode badge now shows「终端托管」for dsh members (was falling back to「仅身份」).
- Fixed the 三国指挥官「设计总负责DSH-1」board/config entry back to `dsh` (both collab_board.json and project_config.json).

## v2.17 — Chat history persistence + fullscreen member card Chat history persistence + fullscreen member card

- **Chat history is now real**: per-member terminal chats persist to `<PROJECTS_ROOT>/<project>/dsh_chat/<agent>.json` — the user message is written immediately when the request arrives (so a question is never lost, even if the run fails or the page refreshes), and the assistant reply plus token usage is appended on success. 600-message cap, atomic writes (tmp + rename). History follows the project folder, so it survives browser refreshes, cache clears, and works across browsers/devices.
- **New APIs**: `GET /api/dsh/history?project=X&agent=<id>` returns the persisted history plus the member's cumulative usage; `POST /api/dsh/history/clear` wipes the history file, the member's session_id and usage — so clearing the chat also resets the agent's memory (fixes the old split where the frontend cleared localStorage but the process memory stayed).
- **Frontend loads from the server**: the member chat panel renders history from the backend on open; the「清空会话」button calls the backend; the usage readout uses server-side totals instead of localStorage.
- **Message vs context separated**: the chatbox sends the raw message and the recent-history context as separate fields, so the persisted history stays clean and never snowballs.
- **Fullscreen member card**: the person modal gains a fullscreen toggle (⛶) so long terminal chats get the whole screen.

## v2.16.1 — Remove the work/rest broadcast switcher — Remove the work/rest broadcast switcher

- Dropped the「开工/收工 + 豁免」broadcast UI (top-bar button, modal, related JS) from both the org view and the kanban view — it didn't hold up in daily use.
- Server-side `/api/protocol` state and the agent-prompt protocol paragraphs are intentionally kept: agents still follow the working/rest protocol (30-min polling while working, dormant when off), so nothing about agent behavior changes.

## v2.16 — Terminal hosting as a first-class invite mode

- **New invite mode「终端托管」(dsh)**: the person-invite panel now has a 5th option — terminal hosting. Picking it registers a member with `entry.type=dsh` and **immediately generates a member-specific dsh profile** (`~/.dsh/profiles/dsh-<project>-<agent>/cordis.patch.yml`, cloned from the template with identity/reporting/protocol replaced per member — project name, agent ID, name, role, and `agentnexus` flags).
- **Per-member process hosting**: `/api/dsh/chat?project=X&agent=<id>` now resolves the member on the board (must be terminal-hosted / legacy `terminal` / role containing「终端」), runs dsh with that member's profile, and validates the model against the member profile's whitelist. Unknown or non-terminal members are rejected with clear errors.
- **Per-member sessions & usage**: `project_config.json` now keeps `dsh.members.<agent_id> = {session_id, usage, last_ts}`; legacy `terminal` data in the old `dsh` root key is read as a fallback (v2.15 migration-compatible). Model switching resets every member's session so the next message picks up the new model.
- **Profile rebuild on rename**: renaming or re-roling a terminal-hosted member deletes and regenerates its profile (identity text stays in sync).
- **Org UI**: invite modal shows 5 modes; terminal-hosted cards expose the chat panel via `entry.type === 'dsh'` (not just the legacy `terminal` id); chat history is isolated per member (`currentDshAgent`), and the chatbox posts `&agent=<id>`.
- Verified end to end: invite → profile generation → member validation → real dsh reply (network recovered) → usage persisted.

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
