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

  ensureAnswerChannels(answerFile, timeoutMs)

  try {
    process.stdout.write(
      JSON.stringify({ type: 'agentnexus_bridge_loaded', ts: Date.now() }) + '\n'
    )
    dbg('bridge loaded, stdout event emitted')
  } catch (_e) {
    /* non-fatal */
  }

  ctx.on('approval/request', async (req, next) => {
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
