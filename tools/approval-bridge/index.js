/**
 * AgentNexus Approval Bridge — dsh host plugin.
 *
 * Hooks the engine's `approval/request` waterfall event (which otherwise fails
 * closed with "no approval channel is available" in headless mode) and routes
 * the decision through the AgentNexus board:
 *
 *   1. emit `{"type":"agentnexus_approval", ...}` as a single NDJSON line to stdout
 *   2. wait for one `{"type":"agentnexus_approval_answer", requestId, option:"allow"|"reject"}`
 *      line on the answer file (config.answerFile, default /tmp/agentnexus_answers.jsonl)
 *      — stdin is kept as a secondary channel
 *   3. resolve the waterfall with 'allowed-once' / 'rejected'
 *
 * Fails closed: timeout (default 5 min) or answer channel EOF -> rejected.
 *
 * v2.20.6 — timeout / late-answer visibility:
 *   - on timeout, emit `agentnexus_approval_expired` so the board UI can retire
 *     the stale card instead of leaving a live-looking button that lies.
 *   - a late answer for a request this process already expired emits
 *     `agentnexus_approval_late` (once) instead of being dropped silently.
 *     Answers for *other* members' requests are ignored quietly — the answer
 *     file is shared, and requestIds are globally unique.
 *
 * v2.20.27 — turn-budget nudges:
 *   - polls a second shared file (config.nudgeFile, env AGENTNEXUS_NUDGE_FILE,
 *     default /tmp/agentnexus_nudges.jsonl) for `agentnexus_nudge` lines and
 *     hands the text to the running turn with `agent.steer(...)`, which the
 *     loop consumes at its next step boundary. Emits
 *     `agentnexus_nudge_delivered` / `agentnexus_nudge_failed` back on stdout.
 *   - declares `inject = ['agents']` so ctx.agents is reachable.
 */
'use strict'

const readline = require('readline')
const fs = require('fs')

function dbg(msg) {
  try {
    process.stderr.write(`[approval-bridge] ${msg}\n`)
  } catch (_e) {}
}

// v2.20.6: one NDJSON line to stdout — the host forwards these to the board UI.
function emitEvent(obj) {
  try {
    process.stdout.write(JSON.stringify(obj) + '\n')
    return true
  } catch (_e) {
    return false
  }
}

let channelStarted = false
const pending = new Map()
// v2.20.6: requests this process timed out on — used to tell "late answer to my
// expired request" apart from "answer to some other member's request".
const expiredIds = new Set()

function markExpired(requestId) {
  expiredIds.add(requestId)
  if (expiredIds.size > 50) {
    const oldest = expiredIds.values().next().value
    expiredIds.delete(oldest)
  }
}

// ---------------------------------------------------------------------------
// v2.20.27：单轮预算提醒（到量先收口回报一句，再接着干）。
// 服务端把提醒写成一行 NDJSON 追加到共享文件；这里按字节偏移轮询（与应答文件同款），
// 用 agent.steer() 送进正在跑的那一轮——steer 会在下一个 step 边界被消费。
// 这里任何异常都不能冒出去：提醒失败最多是没提醒，绝不能弄死一轮。
// ---------------------------------------------------------------------------
const NUDGE_DEFAULT_FILE = '/tmp/agentnexus_nudges.jsonl'
const knownAgents = new Map()   // sessionId（或 agent.id）-> Agent 句柄
let nudgeChannelStarted = false

function rememberAgent(agent) {
  try {
    if (!agent) return
    const sid = (agent.session && agent.session.id) || agent.id
    if (sid) knownAgents.set(String(sid), agent)
  } catch (_e) {}
}

function makeUserMessage(text) {
  const source = { kind: 'plugin', plugin: 'agentnexus-approval-bridge' }
  try {
    // 插件不在 dsh 安装目录里，require 不可能按祖先目录找到 dsh-llm；
    // 以启动器（process.argv[1]）为基准解析才有机会成功。
    const { createRequire } = require('module')
    const base = process.argv[1] || __filename
    const mod = createRequire(base)('@deepseek-ai/dsh-llm')
    if (mod && typeof mod.createUserMessage === 'function') {
      return mod.createUserMessage({ content: [{ type: 'text', text }], source })
    }
  } catch (_e) {}
  // 回退：品牌串在运行期是恒等函数，自己搓一个 user 消息即可。
  let id = ''
  try { id = require('crypto').randomUUID() } catch (_e) {}
  if (!id) id = 'msg-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10)
  return { id, role: 'user', content: [{ type: 'text', text }], source }
}

function pickAgent(ctx, sessionId) {
  const want = sessionId ? String(sessionId) : ''
  if (want && knownAgents.has(want)) return knownAgents.get(want)
  let list = []
  try {
    const reg = ctx.agents
    if (reg && typeof reg.list === 'function') list = reg.list() || []
    else if (reg && typeof reg.roots === 'function') list = reg.roots() || []
  } catch (_e) { list = [] }
  for (const a of list) {
    if (!a) continue
    const sid = (a.session && a.session.id) || a.id
    if (want ? String(sid) === want || String(a.id) === want : list.length === 1) return a
  }
  return null
}

function isAlive(ctx, agent) {
  try {
    const reg = ctx.agents
    if (reg && typeof reg.get === 'function') return reg.get(agent.id) === agent
  } catch (_e) { return false }
  return true
}

function deliverNudge(ctx, msg) {
  const sessionId = msg && msg.sessionId ? String(msg.sessionId) : ''
  const text = (msg && typeof msg.text === 'string' && msg.text) || ''
  if (!text) return
  try {
    const agent = pickAgent(ctx, sessionId)
    if (!agent || !isAlive(ctx, agent)) {
      dbg('nudge dropped (no live agent for session ' + (sessionId || '?') + ')')
      emitEvent({ type: 'agentnexus_nudge_failed', sessionId, level: msg.level,
                  error: 'no live agent', ts: Date.now() })
      return
    }
    const message = makeUserMessage(text)
    if (typeof agent.steer === 'function') agent.steer(message)
    else if (typeof agent.followup === 'function') agent.followup(message)
    else {
      emitEvent({ type: 'agentnexus_nudge_failed', sessionId, level: msg.level,
                  error: 'agent has no steer/followup', ts: Date.now() })
      return
    }
    dbg('nudge delivered (level ' + msg.level + ') to ' + agent.id)
    emitEvent({ type: 'agentnexus_nudge_delivered', sessionId, level: msg.level,
                agentId: String(agent.id), ts: Date.now() })
  } catch (e) {
    dbg('nudge failed: ' + String(e))
    emitEvent({ type: 'agentnexus_nudge_failed', sessionId, level: msg.level,
                error: String(e), ts: Date.now() })
  }
}

function ensureNudgeChannel(ctx, nudgeFile) {
  if (nudgeChannelStarted) return
  nudgeChannelStarted = true
  const filePath = nudgeFile || process.env.AGENTNEXUS_NUDGE_FILE || NUDGE_DEFAULT_FILE
  dbg('nudge file: ' + filePath)
  let offset = 0
  try {
    if (fs.existsSync(filePath)) offset = fs.statSync(filePath).size
  } catch (_e) {}
  const poll = setInterval(() => {
    try {
      if (!fs.existsSync(filePath)) return
      const size = fs.statSync(filePath).size
      if (size <= offset) return
      const fd = fs.openSync(filePath, 'r')
      let text = ''
      try {
        const buf = Buffer.alloc(size - offset)
        fs.readSync(fd, buf, 0, buf.length, offset)
        offset = size
        text = buf.toString('utf8')
      } finally {
        fs.closeSync(fd)
      }
      for (const line of text.split('\n')) {
        const trimmed = line.trim()
        if (!trimmed) continue
        let msg = null
        try { msg = JSON.parse(trimmed) } catch (_e) { continue }
        if (!msg || msg.type !== 'agentnexus_nudge') continue
        deliverNudge(ctx, msg)
      }
    } catch (_e) {
      /* transient fs errors are non-fatal */
    }
  }, 400)
  process.on('exit', () => { clearInterval(poll) })
}

function ensureAnswerChannels(answerFile, timeoutMs) {
  if (channelStarted) return
  channelStarted = true

  // --- Channel 1: append-only answer file, polled by byte offset ---
  const filePath = answerFile || '/tmp/agentnexus_answers.jsonl'
  dbg('answer file: ' + filePath)
  let offset = 0
  try {
    if (fs.existsSync(filePath)) offset = fs.statSync(filePath).size
  } catch (_e) {}
  const poll = setInterval(() => {
    try {
      if (!fs.existsSync(filePath)) return
      const size = fs.statSync(filePath).size
      if (size <= offset) return
      const fd = fs.openSync(filePath, 'r')
      try {
        const buf = Buffer.alloc(size - offset)
        fs.readSync(fd, buf, 0, buf.length, offset)
        offset = size
        const text = buf.toString('utf8')
        for (const line of text.split('\n')) {
          const trimmed = line.trim()
          if (!trimmed) continue
          let msg = null
          try {
            msg = JSON.parse(trimmed)
          } catch (_e) {
            continue
          }
          if (!msg || msg.type !== 'agentnexus_approval_answer') continue
          const entry = pending.get(msg.requestId)
          if (!entry) {
            // v2.20.6：迟到应答不能静默丢弃。只对「本进程超时过的请求」回报，
            // 其余是共享应答文件里其他成员的请求，安静忽略。
            if (expiredIds.has(msg.requestId)) {
              expiredIds.delete(msg.requestId)
              dbg('file answer IGNORED (request already expired): ' + msg.requestId)
              emitEvent({
                type: 'agentnexus_approval_late',
                requestId: msg.requestId,
                option: msg.option === 'allow' ? 'allow' : 'reject',
                ts: Date.now()
              })
            }
            continue
          }
          pending.delete(msg.requestId)
          const outcome = msg.option === 'allow' ? 'allowed-once' : 'rejected'
          dbg('file answer accepted: ' + msg.requestId + ' -> ' + outcome)
          entry.resolve(outcome)
        }
      } finally {
        fs.closeSync(fd)
      }
    } catch (_e) {
      /* transient fs errors are non-fatal */
    }
  }, 400)

  // --- Channel 2: stdin lines (kept for future interactive hosts) ---
  const rl = readline.createInterface({ input: process.stdin, terminal: false })
  rl.on('line', (line) => {
    let msg = null
    try {
      msg = JSON.parse(line)
    } catch (_e) {
      return
    }
    if (!msg || msg.type !== 'agentnexus_approval_answer') return
    const entry = pending.get(msg.requestId)
    if (!entry) {
      if (expiredIds.has(msg.requestId)) {
        expiredIds.delete(msg.requestId)
        dbg('stdin answer IGNORED (request already expired): ' + msg.requestId)
        emitEvent({
          type: 'agentnexus_approval_late',
          requestId: msg.requestId,
          option: msg.option === 'allow' ? 'allow' : 'reject',
          ts: Date.now()
        })
      }
      return
    }
    pending.delete(msg.requestId)
    const outcome = msg.option === 'allow' ? 'allowed-once' : 'rejected'
    dbg('stdin answer accepted: ' + msg.requestId + ' -> ' + outcome)
    entry.resolve(outcome)
  })
  rl.on('close', () => {
    /* stdin is not authoritative; keep waiting on the file channel */
  })

  // Stop polling when the process is about to exit; fail any stragglers closed.
  process.on('exit', () => {
    clearInterval(poll)
    for (const [, entry] of pending) {
      pending.delete(entry.requestId)
      entry.resolve('rejected')
    }
  })
}

module.exports = function agentnexusApprovalBridge(ctx, config = {}) {
  const timeoutMs = Number(config.timeoutMs) > 0 ? Number(config.timeoutMs) : 300000
  const answerFile = typeof config.answerFile === 'string' ? config.answerFile : ''
  const nudgeFile = typeof config.nudgeFile === 'string' ? config.nudgeFile : ''

  ensureAnswerChannels(answerFile, timeoutMs)
  // v2.20.27：单轮预算提醒通道（send 端在 server.py 的 _dsh_nudge_send）
  ensureNudgeChannel(ctx, nudgeFile)

  // v2.20.27：记住本进程的 agent，提醒要按 sessionId 找到它。
  // 拿 agent 的两个来源：注册表里已有的，以及之后 agent/created 事件里的。
  try {
    if (ctx.agents && typeof ctx.agents.list === 'function') {
      for (const a of ctx.agents.list() || []) rememberAgent(a)
    }
  } catch (_e) { /* 服务不可用时安静跳过 */ }
  try {
    if (typeof ctx.on === 'function') {
      ctx.on('agent/created', (payload) => { rememberAgent(payload && payload.agent) })
    }
  } catch (_e) { /* 非致命 */ }

  try {
    process.stdout.write(
      JSON.stringify({ type: 'agentnexus_bridge_loaded', ts: Date.now() }) + '\n'
    )
    dbg('bridge loaded, stdout event emitted')
  } catch (_e) {
    /* non-fatal */
  }

  ctx.on('approval/request', async (req, next) => {
    rememberAgent(req && req.agent)   // v2.20.27：顺手把这轮的 agent 记下来
    const callId = req && req.callId ? String(req.callId) : ''
    const requestId =
      callId ||
      `${req && req.toolName ? req.toolName : 'tool'}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const agentName =
      req && req.agent && typeof req.agent.name === 'string' ? req.agent.name : ''

    const payload = {
      type: 'agentnexus_approval',
      requestId,
      toolName: (req && req.toolName) || '',
      reason:
        (req && req.reason) ||
        (req && req.displayReason && req.displayReason.en) ||
        '',
      agentName,
      timeoutMs,  // v2.20.6: UI 用它显示真实倒计时（不要再硬编码「5 分钟」）
      ts: Date.now(),
    }
    try {
      process.stdout.write(JSON.stringify(payload) + '\n')
      dbg('approval event emitted: ' + requestId + ' (' + (req && req.toolName) + ')')
    } catch (_e) {
      dbg('stdout write failed: ' + String(_e))
    }

    return await new Promise((resolve) => {
      const timer = setTimeout(() => {
        pending.delete(requestId)
        markExpired(requestId)
        dbg('approval timed out (rejected): ' + requestId)
        // v2.20.6：超时也要回报——否则前端卡片永远不知道自己已经失效
        emitEvent({
          type: 'agentnexus_approval_expired',
          requestId,
          timeoutMs,
          ts: Date.now()
        })
        resolve('rejected')
      }, timeoutMs)
      pending.set(requestId, {
        requestId,
        resolve: (outcome) => {
          clearTimeout(timer)
          resolve(outcome)
        },
      })
    })
  })
}

// v2.20.27：cordis 只注入 inject 里点名的服务，否则访问 ctx.agents 会直接抛
// `cannot get property "agents" without inject`。提醒通道需要 agents。
module.exports.inject = ['agents']
