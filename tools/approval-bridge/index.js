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
 */
'use strict'

const readline = require('readline')
const fs = require('fs')

function dbg(msg) {
  try {
    process.stderr.write(`[approval-bridge] ${msg}\n`)
  } catch (_e) {}
}

let channelStarted = false
const pending = new Map()

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
          if (!entry) continue
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
    if (!entry) return
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
        dbg('approval timed out (rejected): ' + requestId)
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
