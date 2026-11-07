#!/usr/bin/env node
/**
 * T23 — v2.20.31：把图片/文件拖进聊天框，以及回复下面挂附件
 *
 * 两个需求：
 * ① 「给托管成员的聊天框能不能把图片拖进去」——dsh 的 headless CLI 只吃一段文本任务，
 *    没有附件/多模态入口。所以路径是：前端把文件传到成员的工作目录（它一定读得到），
 *    再把路径写进消息，让它用 read_image / read 自己看。
 * ② 「它回复的东西能不能像别的 agent 一样，文字下面挂着图片和交付文件」——用这一轮
 *    交付物登记（ts >= 本轮开始）算出来，落到历史条目上，前端画成缩略图/文件条。
 *
 * 本测试从真实文件里抽源码跑，不复制实现。
 * 跑法：node tests/t23_attachments_test.js
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

section('== A. 服务端：上传落盘 ==')
include(server, 'if parsed.path == "/api/dsh/upload":', 'POST /api/dsh/upload 已挂路由')
include(server, 'def _handle_dsh_upload(self, project=None):', '有上传处理器')
include(server, 'DSH_UPLOAD_MAX_BYTES = 15 * 1024 * 1024', '有 15 MB 上限')
include(server, '"error": "文件过大（上限 %.0f MB）"', '超限会明确报错')
include(server, 'def _dsh_inbox_dir(project):', '有落盘目录解析')
include(server, 'd = os.path.join(wd, "_inbox")', '落在成员工作目录的 _inbox 下（它读得到）')
include(server, 'def _dsh_safe_upload_name(name, mime):', '文件名会清洗')
include(server, 'os.path.basename(str(name or "").replace("\\\\", "/"))', '只取 basename（防路径穿越）')
include(server, '"is_image": ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "avif")', '回传是否图片')

section('\n== B. 服务端：本轮交付物挂到回复上 ==')
include(server, 'def _dsh_turn_attachments(project, agent_id, since', '有「本轮附件」计算')
include(server, 'if not ts or ts < since:', '按本轮时间窗过滤旧交付物')
include(server, '"attachments": _dsh_turn_attachments(project, agent_id, _turn_since, _turn_since_epoch)', 'final 实时落盘时带上附件')
include(server, '"attachments": _dsh_turn_attachments(project, agent_id, _turn_since, _turn_since_epoch)})', 'dsh_done 事件带上附件（前端可立即画）')
include(server, '_turn_since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 1))', '时间窗从本轮开始前 1 秒算（避开同秒边界）')

section('\n== C. 前端：回复下面挂附件 ==')
include(html, 'function dshAttachHtml(items, agentId)', '有附件渲染函数')
include(html, 'const DSH_ATT_IMG = { png: 1, jpg: 1', '图片按扩展名识别')
include(html, 'class="dsh-att img"', '图片渲染为缩略图链接')
include(html, "📄 <span class=\"att-name\">", '其它文件渲染为文件条')
include(html, '&agent=\' + encodeURIComponent(agentId || \'\')', '预览链接带成员 id（服务端要鉴权）')
include(html, 'meta.attachments', 'dshMsgHtml 认识 attachments')
include(html, 'dshAttachHtml(atts, targetAgent)', 'dsh_done 时把本轮附件画到气泡下')

section('\n== D. 前端：拖拽 / 粘贴 ==')
include(html, 'function initDshDrop()', '有拖拽初始化')
include(html, "panel.ondragover = function (e) { e.preventDefault(); panel.classList.add('dragover'); }", '拖入有高亮反馈')
include(html, "panel.ondrop = function (e) {", '有 drop 处理')
include(html, "input.onpaste = function (e) {", '支持粘贴图片')
include(html, 'async function dshUploadFile(file)', '有上传函数')
include(html, "fetch('/api/dsh/upload'", '走后台上传接口')
include(html, 'let dshPendingAtt = []', '待发附件有独立状态')
include(html, 'function dshRenderPendingAtt()', '输入区上方显示待发附件')
include(html, 'function dshClearPendingAtt()', '发送/切卡片后清掉')

section('\n== E. 前端：发送时把路径写进消息 ==')
include(html, "if (!typed && !dshPendingAtt.length) return;", '只拖附件不写字也能发')
include(html, "'【附件 ' + (i + 1) + '】' + a.path", '消息里带绝对路径')
include(html, "（图片，用 read_image 查看）", '图片提示用 read_image')
include(html, "（用 read 查看）", '其它文件提示用 read')
include(html, "const text = attLines + (typed || '（见附件）');", '只有附件时有兜底文案')
include(html, 'dshClearPendingAtt();   // 附件已经写进消息', '发出后清空待发条')

section('\n== F. 行为验证（真实源码 + 假 DOM）==')
const consts = (html.match(/const DSH_ATT_IMG = \{[^}]*\};/) || [''])[0]
const helpers = [
  consts,
  extractFn(html, 'esc2'),
  extractFn(html, 'dshAttExt'),
  extractFn(html, 'dshAttUrl'),
  extractFn(html, 'dshAttachHtml')
].join('\n')
ok(helpers.indexOf('function dshAttachHtml') > 0, '抽到附件渲染真实源码')
let api = null
if (helpers.indexOf('function dshAttachHtml') > 0) {
  try {
    api = new Function('PROJECT', 'fmtBytes', helpers +
      '\nreturn { dshAttachHtml: dshAttachHtml, dshAttExt: dshAttExt };')(
      '三国指挥官', function (n) { return (Number(n) || 0) + 'B' }
    )
  } catch (e) { api = null }
}
ok(api !== null, '附件渲染源码可执行（否则下面的行为检查无意义）')
const A = api || { dshAttachHtml: function () { return '' }, dshAttExt: function () { return '' } }
const img = A.dshAttachHtml([{ path: '/p/a/b/shot.png', size: 1234 }], 'DSH2')
const txt = A.dshAttachHtml([{ path: '/p/a/b/report.md', size: 2048 }], 'DSH2')
ok(img.indexOf('<img src=') > 0, 'png 渲染成缩略图', img.slice(0, 120))
ok(img.indexOf('shot.png') > 0, '缩略图带文件名')
ok(img.indexOf('/api/dsh/artifact?project=') > 0, '走交付物预览接口')
ok(img.indexOf('agent=DSH2') > 0, '链接带成员 id')
ok(txt.indexOf('<img') < 0, 'md 不渲染成图片')
ok(txt.indexOf('📄') > 0 && txt.indexOf('report.md') > 0, 'md 渲染成文件条', txt.slice(0, 120))
ok(A.dshAttachHtml([], 'DSH2') === '', '空列表不产出多余 DOM')
ok(A.dshAttachHtml(null, 'DSH2') === '', 'null 不崩')
ok(A.dshAttExt('/x/y.PNG') === 'png', '扩展名大小写不敏感')

section('\n== G. 版本号 ==')
ok(/const BUILD_VERSION = 'v2\.\d+\.\d+'/.test(html), '页面版本号格式正确')

console.log(out.join('\n'))
console.log('\n' + pass + ' passed, ' + fail + ' failed')
process.exit(fail ? 1 : 0)
