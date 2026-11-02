#!/usr/bin/env node
/**
 * T24 — v2.20.32：叫停（停止按钮）
 *
 * 以前一轮跑起来只能硬等：watchdog 只管「180 秒没动静」，单轮预算上限是兜底，
 * 中间没有任何「立刻别干了」的手段。现在：服务端记住进程句柄，停止接口杀掉进程组，
 * 已经流出来的回复照常交付落盘，日志如实写「用户叫停」，并且不会自动重试。
 *
 * 本测试从真实文件里抽源码跑，不复制实现。
 * 跑法：node tests/t24_stop_test.js
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
function section(t) { out.push(t) }

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

section('== A. 服务端：进程句柄与停止接口 ==')
include(server, 'if parsed.path == "/api/dsh/stop":', 'POST /api/dsh/stop 已挂路由')
include(server, 'def _handle_dsh_stop(self, project=None):', '有停止处理器')
include(server, 'def _dsh_stop_turn(project, agent_id):', '有可单测的叫停逻辑')
include(server, '_DSH_PROCS = {}', '记住正在跑的进程句柄')
include(server, '_dsh_proc_register(project, agent_id, proc, "对话")', 'Popen 之后登记句柄')
include(server, '_dsh_proc_unregister(project, agent_id, proc)', '进程退出后注销句柄')
include(server, 'os.killpg(pid, signal.SIGKILL)', '进程组级终止（连子进程）')
include(server, 'if not killed:', '杀不掉时如实回报，不假装成功')

section('\n== B. 服务端：叫停要按「已交付」收口，且不重试 ==')
include(server, 'if (project, agent_id) in _DSH_STOP_REQ:', '退出时认领「是用户叫停的」')
include(server, '_DSH_STOPPED.add((project, agent_id))', '标记这一轮被叫停')
include(server, 'return (True, new_sid, usage_agg)', '叫停按 ok=True 收口（已产出的回复要留）')
const iWait = server.indexOf('proc.wait(timeout=30)')
const iStop = server.indexOf('if (project, agent_id) in _DSH_STOP_REQ:')
const iWd = server.indexOf('if _wd_stop[0]:', iWait)   // 注意：watchdog 线程里还有一处更早的同名判断
ok(iWait > 0 && iStop > iWait && iStop < iWd,
   '叫停判断在「进程退出之后、watchdog 判断之前」（否则会被记成超时/失败）',
   'wait=' + iWait + ' stop=' + iStop + ' wd=' + iWd)
include(server, 'if (not _stopped) and (not ok or not _reply_box["text"]) and session_id:', '聊天：叫停后不自动重试')
include(server, '"stopped": bool(_stopped)', 'dsh_done 带回 stopped')
include(server, '自动唤醒被用户叫停，不重试', '自动唤醒：叫停后不重试')

section('\n== C. 前端：停止按钮 ==')
include(html, 'id="dshStopBtn"', '输入区有停止按钮')
include(html, 'onclick="stopDshTurn()"', '按钮绑到叫停函数')
include(html, 'async function stopDshTurn()', '有叫停函数')
include(html, "fetch('/api/dsh/stop'", '走后台停止接口')
include(html, 'function dshStopVisible(on)', '有显示/隐藏控制')
include(html, 'dshStopVisible(true);   // v2.20.32：这一轮跑起来了，露出「停止」', '开跑就显示停止')
include(html, 'if (targetAgent === currentDshAgent) dshStopVisible(!!dshStreams[currentDshAgent]);', '结束按状态收起')
include(html, 'dshStopVisible(true);   // v2.20.32：不是当前页发起的活也能叫停', '看板唤醒的活也能叫停')
include(html, 'if (ev.stopped) {', '前端认识 stopped')
include(html, '⏹ 你叫停了这一轮', '叫停后如实说明')
include(html, "dshActivityPush('没停成：'", '没停成会说原因，不假装成功')

section('\n== C2. 叫停后审批卡要立刻失效（不能继续响铃到超时）==')
include(html, 'function expireApprovalsForAgent(agentId, msg)', '有按成员收卡函数')
include(html, "card.dataset.agent = ev.agentId || currentDshAgent || ''", '审批卡记住属于哪个成员')
include(html, 'expireApprovalsForAgent(targetAgent, ev.stopped', '一轮结束就收卡（叫停时尤其重要）')
include(html, "Object.keys(dshApprovalQueue).forEach", '暂存队列里的一并清掉')

section('\n== D. 行为验证：按钮显隐（真实源码 + 假 DOM）==')
const visSrc = extractFn(html, 'dshStopVisible')
ok(!!visSrc, '抽到 dshStopVisible 真实源码')
if (visSrc) {
  const fake = { style: { display: 'none' }, disabled: true, textContent: '停止中…' }
  const api = new Function('document', visSrc + '\nreturn dshStopVisible;')({
    getElementById: function (id) { return id === 'dshStopBtn' ? fake : null }
  })
  api(true)
  ok(fake.style.display === '', '显示时清掉 display')
  ok(fake.disabled === false, '显示时恢复可点')
  ok(fake.textContent === '⏹ 停止', '文案复位')
  api(false)
  ok(fake.style.display === 'none', '隐藏时 display=none')
  // 按钮不存在时不能崩
  const api2 = new Function('document', visSrc + '\nreturn dshStopVisible;')({ getElementById: function () { return null } })
  let threw = false
  try { api2(true) } catch (e) { threw = true }
  ok(!threw, '按钮不在时也不抛错（切卡片竞态）')
} else {
  ok(false, '抽不到 dshStopVisible（行为检查跳过）')
}

section('\n== E. 版本号 ==')
ok(/const BUILD_VERSION = 'v2\.\d+\.\d+'/.test(html), '页面版本号格式正确')

console.log(out.join('\n'))
console.log('\n' + pass + ' passed, ' + fail + ' failed')
process.exit(fail ? 1 : 0)
