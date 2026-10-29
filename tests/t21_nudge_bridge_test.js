#!/usr/bin/env node
// v2.20.27：桥接插件「单轮预算提醒」单测（假 ctx / 假 agent，不碰真 dsh）
'use strict'
const path = require('path')
const fs = require('fs')
const os = require('os')

const BRIDGE = path.join(__dirname, '..', 'tools', 'approval-bridge', 'index.js')
const plugin = require(BRIDGE)
const stdoutWrite = process.stdout.write.bind(process.stdout)
const events = []
process.stdout.write = (chunk, ...rest) => {
  try {
    const s = String(chunk)
    for (const line of s.split('\n')) {
      const t = line.trim()
      if (!t) continue
      try { events.push(JSON.parse(t)) } catch (_e) {}
    }
  } catch (_e) {}
  return true
}
process.stderr.write = () => true

let pass = 0, fail = 0
function ok(cond, label, extra) {
  if (cond) { pass++; stdoutWrite('  ✓ ' + label + '\n') }
  else { fail++; stdoutWrite('  ✗ ' + label + (extra ? '  → ' + extra : '') + '\n') }
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const ofType = (t) => events.filter((e) => e && e.type === t)

function makeAgent(id, sid) {
  return {
    id, session: { id: sid }, steered: [], followed: [],
    steer(m) { this.steered.push(m) },
    followup(m) { this.followed.push(m) },
  }
}

async function main() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'nudgebridge-'))
  const nudgeFile = path.join(dir, 'nudges.jsonl')
  fs.writeFileSync(nudgeFile, '')   // 存在但空 → 从 EOF 起读

  const a1 = makeAgent('agent-1', 'sid-1')
  const a3 = makeAgent('agent-3', 'sid-3')
  const handlers = {}
  const ctx = {
    agents: {
      list: () => [a1],
      get: (id) => (id === a1.id ? a1 : id === a3.id ? a3 : undefined),
    },
    on: (ev, fn) => { handlers[ev] = fn },
  }

  stdoutWrite('== A. 出口契约 ==\n')
  ok(Array.isArray(plugin.inject) && plugin.inject.length === 1 && plugin.inject[0] === 'agents',
    "module.exports.inject === ['agents']", JSON.stringify(plugin.inject))

  plugin(ctx, { nudgeFile, timeoutMs: 120000 })
  await sleep(200)
  ok(ofType('agentnexus_bridge_loaded').length === 1, '装载事件照旧发出')

  const push = (obj) => fs.appendFileSync(nudgeFile, JSON.stringify(obj) + '\n')

  stdoutWrite('== B. 正常投递（按 sessionId 认领）==\n')
  push({ type: 'agentnexus_nudge', sessionId: 'sid-1', level: 1, text: '【预算提醒】先回报一句' })
  await sleep(800)
  ok(a1.steered.length === 1, 'steer 被调用一次', 'n=' + a1.steered.length)
  const msg = a1.steered[0] || {}
  ok(msg.role === 'user', '消息 role=user', String(msg.role))
  ok(Array.isArray(msg.content) && msg.content[0] && msg.content[0].type === 'text'
    && /先回报一句/.test(msg.content[0].text), '消息 content 是 text block 且带原文', JSON.stringify(msg.content))
  ok(typeof msg.id === 'string' && msg.id.length > 0, '消息带 id', String(msg.id))
  // v2.20.28：v4 会话格式只认「生产者自有」的 source.kind（裸 'plugin' 会被 dsh 在会话
  // 落盘时拒收：packages/session/session-format-v3-to-v4/src/message-sources.ts:9-10）。
  // 之前这里只查 role/content/id，于是一个「投递成功但整轮被写崩」的形状照样 15/15 全绿。
  const src = msg.source || {}
  ok(typeof src.kind === 'string' && src.kind.length > 0 && src.kind !== 'plugin',
    "source.kind 是非空字符串且不是裸 'plugin'", JSON.stringify(src))
  ok(src.kind === 'plugin:agentnexus-approval-bridge',
    'source.kind 为 plugin:agentnexus-approval-bridge（约定 plugin:<插件名>）', String(src.kind))
  const del = ofType('agentnexus_nudge_delivered')
  ok(del.length === 1 && del[0].level === 1 && del[0].agentId === 'agent-1',
    '回报 agentnexus_nudge_delivered{level:1,agentId:agent-1}', JSON.stringify(del[0]))
  ok(ofType('agentnexus_nudge_failed').length === 0, '没有多余的失败回报')

  stdoutWrite('== C. 别人的 sessionId 不投递 ==\n')
  push({ type: 'agentnexus_nudge', sessionId: 'sid-nobody', level: 1, text: '不该投' })
  await sleep(800)
  ok(a1.steered.length === 1, '仍未调用 steer')
  const failed = ofType('agentnexus_nudge_failed')
  ok(failed.length === 1 && failed[0].error === 'no live agent',
    '回报 agentnexus_nudge_failed{no live agent}', JSON.stringify(failed[0]))

  stdoutWrite('== D. agent/created 记下的句柄可用 ==\n')
  if (handlers['agent/created']) handlers['agent/created']({ agent: a3 })
  ok(typeof handlers['agent/created'] === 'function', '注册了 agent/created 监听')
  push({ type: 'agentnexus_nudge', sessionId: 'sid-3', level: 2, text: 'L2 收口' })
  await sleep(800)
  ok(a3.steered.length === 1, '新 agent 收到 steer', 'n=' + a3.steered.length)
  ok(a1.steered.length === 1, '旧 agent 没被误投')

  stdoutWrite('== E. steer 抛错不能崩 ==\n')
  a1.steer = () => { throw new Error('boom-steer') }
  push({ type: 'agentnexus_nudge', sessionId: 'sid-1', level: 2, text: '再投一次' })
  await sleep(800)
  const failed2 = ofType('agentnexus_nudge_failed')
  ok(failed2.length === 2 && /boom-steer/.test(String(failed2[1].error)),
    '失败被回报且不抛出去', JSON.stringify(failed2[1]))

  stdoutWrite('== F. 无效行 / 非 NDJSON 行被忽略 ==\n')
  fs.appendFileSync(nudgeFile, 'not json\n' + JSON.stringify({ type: 'agentnexus_approval_answer', requestId: 'x' }) + '\n')
  await sleep(600)
  ok(a3.steered.length === 1 && ofType('agentnexus_nudge_delivered').length === 2,
    '噪声行不产生任何投递')

  fs.rmSync(dir, { recursive: true, force: true })
  stdoutWrite('\n' + pass + ' passed, ' + fail + ' failed\n')
  process.exit(fail ? 1 : 0)
}

main()
