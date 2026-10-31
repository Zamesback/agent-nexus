# Release notes — AgentNexus v2.20

> The v2.20 arc is **process hosting**: an agent stops being a prompt you paste and becomes a real
> local process that the board can wake, watch, and ask *you* for permission before it touches
> anything. This is the write-up of what landed between v2.20.6 and v2.20.30.

![Org chart](screenshots/05-org-chart.png)

## The one idea that did not change

A JSON file on your disk is still the single source of truth. Every feature below writes to the same
`collab_board.json` / `project_config.json` that you can `cat`, `git diff`, edit by hand or roll back.
Process hosting did not introduce a database — it added processes that read and write the same file.

---

## 1. Members are real local processes

A "terminal-hosted" member is a `dsh` process started by the board, one profile per member
(`~/.dsh/profiles/dsh-<project>-<member>/`). Chat with it from its card and you get the same agent that
would run in a terminal — tools, file writes, the lot — wrapped in a UI.

* **One turn at a time, per member.** Each member has its own lock, so two messages to the same member
  queue instead of racing; different members run in parallel.
* **Queueing is visible.** The card says *"the previous turn is still running (…, 3 min in), yours is
  queued"* instead of looking dead.
* **Idle watchdog.** 180 s with no event kills the turn (any stdout event resets the clock), and the
  log records the real reason (`final` / `watchdog` / `budget`) instead of always saying "timed out".
* **Process-group cleanup.** Children are killed with the group (`killpg`), so no orphans are left
  behind.

## 2. The member card shows the work, not a spinner

![Member card](screenshots/06-org-member-card.png)

A turn can be 100 steps and half an hour. The activity line therefore renders what is actually
happening:

* `🔧 bash · <command head>`, `✏️ edit · index.html`, `🔎 grep · pattern @ path` — every tool call, with
  the part you would ask about.
* `⏳ 正在工作中 · 第 N 步 · 8 分 12 秒` — step counter and a stopwatch that keeps ticking.
* `⏱ 已跑 8 分钟 / 第 27 步 —— 已请它先回报一句` when the turn budget kicks in (see §6).
* Thinking is a collapsed `🧠 思考 #N` block you can open; the answer streams into the reply bubble.

## 3. Per-member memory

* Each member keeps its own session id, so `--session-id` resumes **its** memory, never a neighbour's.
* Chat history is a per-member file (`<projects-root>/.agentnexus/chat/<project>/<member>.json`,
  600 entries capped) — survives refreshes, restarts and browser changes.
* The context sent with a new message is built **server-side from that member's own history**; the
  client's idea of "the last few turns" is ignored. This is what stopped two members from answering
  each other's topics.
* Switching models resets the session on purpose, so the new model actually takes effect.

## 4. Discussion rooms

`讨论室` starts a room, then wakes each hosted member in turn with the **full transcript so far** and
asks it to respond to the previous speaker. Optional "close after 3 rounds" appends *"please start
converging"* to each nudge. Members in a room talk only — no tool calls — so it stays cheap. Minutes
and a transcript land on the board when the room ends.

## 5. The approval loop

When a hosted member hits a permission wall, the approval bridge inside its process emits an event
instead of failing closed:

1. card in the member's chat panel (tool, reason, countdown, 批准 / 拒绝),
2. chime + desktop notification, repeating every 8 s until answered,
3. your answer goes through `/tmp/agentnexus_answers.jsonl`; the bridge polls it every 400 ms,
4. **120 s without an answer = rejected** (safety default, never auto-allow),
5. expired or late answers are reported back so the card cannot keep pretending it is still valid.

Approvals raised by a member whose card is not open are now queued per member — the chime fires
immediately, the card appears when you open that member. Nothing is dropped.

## 6. Turn budgets

A turn can run away, so each one has three thresholds (25 steps / 8 min → soft, 50 / 16 → firm,
80 / 25 → hard; all env-overridable):

* **soft / firm** — the server writes a nudge to `/tmp/agentnexus_nudges.jsonl`; the bridge picks it up
  and calls `agent.steer(...)`, which the loop consumes at the next step boundary: *"give a one-line
  conclusion now, put what is left on the task card, and carry on."*
* **hard** — the process group is killed and the log says `terminated by turn budget`, not "timed out".

The nudge is a user message that must survive the session write, so its `source.kind` is
`plugin:agentnexus-approval-bridge` — dsh's v4 format refuses a bare `plugin` kind, and a message that
is delivered but cannot be persisted destroys the very turn it was meant to save.

## 7. Who may wake a member

* **Only Zames's `@` wakes a member.** Another member's `@` does not, which kills the reply-to-reply
  loops that used to burn tokens overnight.
* **Task assignment still wakes.** Writing an `assignee` onto a task is the supported way to hand work
  over.
* **Every wake carries an identity anchor** (project, name, id, role, *"you are not any other member"*),
  and the prompt says the user's conversation with someone else is not your task.
* **Board-driven wake-ups are labelled in the UI.** They are stored in the member's own history as
  `role: user`, which used to be rendered as *your* words — opening member B's card looked like *you*
  discussing member A's topic. They now render as `🔔 看板唤醒（不是你说的）`, collapsed by default, and
  wake replies as `🔔 唤醒回复`.

## 8. A member may write only in its working directory

A hosted member's sandbox root is its project working directory — one folder, visible on the card
(`📁 <project>`), and that is the only place it can write. Anything outside triggers the approval loop
instead of silently succeeding.

## 9. Isolation, honestly

Two reports shaped v2.20.23–30, and both turned out to be **presentation** bugs rather than bleeding
state:

* *"Members answer each other's conversations."* Sessions and history were verified separate (distinct
  session ids, no cross-session reuse in the logs, thematically distinct histories). What was crossing
  was the *wake prompt*: another member's board message, stored in B's history and rendered as "you".
  Fixed by labelling, not by hiding.
* *"A reply never appears; close the card and reopen and it is there."* The client aborted its SSE
  stream when you switched cards (and Enter could abort a running turn), while the server kept going
  and persisted `final`. Streams are now held per member and aborted only when the page unloads;
  painting is guarded by agent id; `final` creates the reply bubble when none exists; a turn that
  painted nothing re-fetches and repaints from history.

## 10. You can tell which build a tab is running

A local tool stays open for days, and a tab can keep executing JS it loaded before your fix. The header
shows the build (`v2.20.30`), the page HEADs itself once a minute and raises a *"页面代码已更新 — 点这里刷新"*
banner when the file changed underneath it, and HTML responses are served with
`Cache-Control: no-store` so a reload can never reuse a stale copy.

---

## Running it

```bash
python3 server.py 8766          # or double-click start.command
# http://localhost:8766
```

Data lives in `COLLAB_PROJECTS_ROOT` (default `~/Desktop/Project/agent-collab-projects`), one folder per
project, each with its own `collab_board.json`, git-committed automatically on every write.

## Tests

`tests/` holds 19 suites — **805 assertions**, run individually:

```
python3 tests/t5_regression_test.py      # and t6, t7, t14, t15, t17, t18, t19, t20, t21, t22
node      tests/t9_chat_markdown_test.js # and t8, t10, t11, t12, t13, t16, t21, t22
```

They cover the server's task/message/protocol logic, the approval bridge, session guards, member
isolation, queue visibility, working directory, wake source and identity, turn budget and nudge bridge,
and the frontend by extracting the real functions out of the HTML and running them against a fake DOM.

## Known limits

* A discussion room lives in memory — a server restart or project switch empties it.
* `fcntl` is POSIX; Windows would need a `msvcrt` shim (the frontend and core logic would run).
* The board is the shared bus: members legitimately learn about each other through it. Wake prompts
  still enter the member's context, by design — the member is told to work from the board, not to read
  another member's private chat (that read is audited and logged).
