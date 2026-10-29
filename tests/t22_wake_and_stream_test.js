#!/usr/bin/env node
/**
 * T22 — v2.20.29：自动唤醒不再冒充「你」，回复不再因为切卡片而丢
 *
 * 两个真实报障：
 * ① 「我跟 DSH2 说的话，DSH1 好像在接」——不是记忆串了（会话/历史都按成员隔离，已逐条核对），
 *    而是**看板自动唤醒的提示词**被写进了成员历史，前端把 role=user 一律标成「你」。
 * ② 「留在聊天框里一直工作中，完成了也不跳出来；关掉重开就看见结论」——
 *    切卡片时前端 abort 了 SSE，但服务端照常跑完、final 照常落盘；界面这条流已经断了。
 *
 * 本测试从真实文件里抽源码/函数跑，不复制实现。
 * 跑法：node tests/t22_wake_and_stream_test.js
 */
'use strict'
const fs = require('fs')
const path = require('path')

const ROOT = path.join(__dirname, '..')
const html = fs.readFileSync(path.join(ROOT, 'org_chart.html'), 'utf8')
const server = fs.readFileSync(path.join(ROOT, 'server.py'), 'utf8')

let pass = 0, fail = 0
const out = []
function ok(cond, label, extra) {
  if (cond) { pass++; out.push('  ✓ ' + label) }
  else { fail++; out.push('  ✗ ' + label + (extra ? '  → ' + extra : '')) }
}
function include(src, needle, label) { ok(src.indexOf(needle) >= 0, label, needle) }
function exclude(src, needle, label) { ok(src.indexOf(needle) < 0, label, '仍存在: ' + needle) }
// 只在「代码」里判断有没有某个调用——注释里提到不算
function stripComments(s) {
  return String(s || '').replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/[^\n]*/g, '$1')
}
function section(title) { out.push(title) }

// 按大括号配平，从源码里抽一个具名函数（本文件的函数体里没有嵌套花括号字符串陷阱）
function extractFn(src, name) {
  const re = new RegExp('function\\s+' + name + '\\s*\\(')
  const m = re.exec(src)
  if (!m) return null
  const start = m.index
  let i = src.indexOf('{', start), depth = 0
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') depth++
    else if (src[j] === '}') { depth--; if (depth === 0) return src.slice(start, j + 1) }
  }
  return null
}

section('== A. 抽真实源码 ==')
const openPersonSrc = extractFn(html, 'openPerson')
const closePersonSrc = extractFn(html, 'closePerson')
const sendSrc = extractFn(html, 'sendToTerminal')
const reattachSrc = extractFn(html, 'dshReattach')
const bubbleSrc = extractFn(html, 'dshBubbleFor')
ok(!!openPersonSrc, '抽到 openPerson')
ok(!!closePersonSrc, '抽到 closePerson')
ok(!!sendSrc, '抽到 sendToTerminal')
ok(!!reattachSrc, '抽到 dshReattach')
ok(!!bubbleSrc, '抽到 dshBubbleFor')

console.log('\n== B. 切卡片不再 abort 流（② 的根因）==')
exclude(stripComments(openPersonSrc), 'dshAbortStream', 'openPerson 不再断流')
exclude(stripComments(closePersonSrc), 'dshAbortStream', 'closePerson 不再断流')
include(html, 'window.addEventListener(\'pagehide\'', '只在页面卸载时统一断流')
include(html, 'window.addEventListener(\'beforeunload\'', 'beforeunload 也兜底断流')
include(html, 'const dshStreams = {}', '流按成员持有（dshStreams）')
include(sendSrc || '', 'signal: run.ctrl.signal', '本轮用自己的 AbortController')

console.log('\n== C. 同一成员单飞，回车不再掐掉上一轮 ==')
include(sendSrc || '', 'if (dshStreams[targetAgent])', '发送前先看这个成员是否已有在跑的一轮')
include(sendSrc || '', 'dshActivityPush(\'这一轮还在跑', '重复发送被忽略且说明原因')

console.log('\n== D. final 缺气泡要补、收尾要自愈（②）==')
include(sendSrc || '', 'const b = dshBubbleFor(run)', 'final/text 走 dshBubbleFor（缺就补）')
include(bubbleSrc || '', 'box.contains(run.bubbleEl)', '气泡被重建过才重插')
include(sendSrc || '', 'if (!run.gotReply && ev.ok) syncDshHistoryFromServer', '这轮没画出来 → 取服务端历史自愈')
include(html, 'async function syncDshHistoryFromServer', '有自愈函数')
include(reattachSrc || '', 'flushApprovalQueue(who)', '切回卡片时把暂存的审批画出来')

console.log('\n== E. 别的成员的审批事件不丢 ==')
include(html, 'function queueOrRenderApproval', '有审批暂存入口')
include(html, 'function dropQueuedApproval', '超时/迟到时把暂存的清掉')
include(sendSrc || '', 'queueOrRenderApproval(ev, targetAgent)', 'approval 事件走暂存/渲染')
include(html, 'const dshApprovalQueue = {}', '审批队列按成员分开')

console.log('\n== F. 自动唤醒不再渲染成「你」（①）==')
include(html, 'meta.auto_wake', 'dshMsgHtml 认识 auto_wake')
include(html, '🔔 看板唤醒（不是你说的）', '唤醒提示词有独立标签')
include(html, '🔔 唤醒回复', '唤醒回复有独立标签')
include(html, "h.map(m => dshMsgHtml(m.role, m.content, null, m))", '历史渲染把整条记录（含 auto_wake）传下去')

// 用真实函数跑：普通消息 vs 唤醒记录
const code = [
  extractFn(html, 'esc2'),
  extractFn(html, 'dshBody'),
  extractFn(html, 'dshMsgHtml')
].join('\n')
const api = new Function('renderMiniMarkdown', code + '\nreturn { dshMsgHtml: dshMsgHtml };')(
  function (text) { return '<div class="md">' + String(text) + '</div>' }
)
const normalUser = api.dshMsgHtml('user', '你好')
const normalAsst = api.dshMsgHtml('assistant', '收到')
const wakePrompt = api.dshMsgHtml('user', '【自动唤醒】…触发者：联合Planner（DSH2）…', null, { auto_wake: true })
const wakeReply = api.dshMsgHtml('assistant', '我看完了', null, { auto_wake: true })
ok(/>你</.test(normalUser), '普通用户消息仍标「你」')
ok(/>终端 Agent</.test(normalAsst), '普通回复仍标「终端 Agent」')
ok(wakePrompt.indexOf('你<') < 0 && wakePrompt.indexOf('>你') < 0, '唤醒提示词不再标成「你」', wakePrompt.slice(0, 80))
ok(wakePrompt.indexOf('看板唤醒') > 0, '唤醒提示词标成「看板唤醒」')
ok(wakePrompt.indexOf('<details') > 0, '唤醒提示词默认折叠（太长，不淹对话）')
ok(wakeReply.indexOf('唤醒回复') > 0, '唤醒回复有标记')
ok(wakeReply.indexOf('我看完了') > 0, '唤醒回复正文仍然显示')

console.log('\n== G. 服务端：自动唤醒的回复也带 auto_wake ==')
const workerStart = server.indexOf('def _dsh_auto_wake_worker(')
const workerEnd = server.indexOf('def _append_agent_message(', workerStart)
ok(workerStart > 0 && workerEnd > workerStart, '抽到 _dsh_auto_wake_worker')
const workerSrc = server.slice(workerStart, workerEnd)
const awAppends = (workerSrc.match(/"auto_wake": True/g) || []).length
ok(awAppends >= 3, '唤醒提示词 + 两处回复落盘都带 auto_wake（实测 ' + awAppends + ' 处）')
const chatStart = server.indexOf('def _handle_dsh_chat(')
const chatEnd = server.indexOf('def _handle_dsh_model(', chatStart)
const chatSrc = chatStart > 0 && chatEnd > chatStart ? server.slice(chatStart, chatEnd) : ''
ok(chatSrc.indexOf('auto_wake') < 0, '用户对话落盘不带 auto_wake（不能误标）')

console.log(out.join('\n'))
console.log('\n' + pass + ' passed, ' + fail + ' failed')
process.exit(fail ? 1 : 0)
