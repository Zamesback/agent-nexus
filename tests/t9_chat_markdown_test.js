#!/usr/bin/env node
/**
 * T9 回归测试：托管成员聊天气泡里的 markdown 渲染（v2.20.13）
 *
 * 背景
 * ----
 * 托管 Agent 的回复本来就是 markdown，`| 列 | 列 |` 表格、`1. 2. 3.` 有序列表、
 * `- [x]` 待办清单都很常见（真实的 DSH-1 历史 78 条里有 13 条含表格），但气泡
 * 一直用 esc2() 原样输出，用户看到的是裸竖线和星号。v2.20.13 改为：Agent 的回复
 * 走 renderMiniMarkdown()（复用交付物预览那套零依赖渲染器），用户自己打的字保持
 * 原样——打什么看到什么。
 *
 * 本测试从 org_chart.html 抽出真实函数运行，不复制实现，避免测试与产品脱节。
 * 覆盖：表格结构/分隔行过滤/单元格内联、有序列表与 start 属性、有序↔无序切换、
 * 待办复选框、XSS 转义、用户消息不被渲染、围栏代码块、thinking 角色、
 * dshSetBody 流式替换时 md-wide 的同步。
 *
 * 跑法：node tests/t9_chat_markdown_test.js
 */
const fs = require('fs');
const path = require('path').join(__dirname, '..', 'org_chart.html');
const src = fs.readFileSync(path, 'utf8');

function between(startMark, endMark) {
  const i = src.indexOf(startMark);
  const j = src.indexOf(endMark);
  if (i < 0 || j < 0 || j <= i) throw new Error('extract failed: ' + startMark);
  return src.slice(i, j);
}

// 真实实现：esc() + renderMiniMarkdown() + esc2()/dshBody()/dshMsgHtml()/dshSetBody()
const code = [
  between('function esc(s) {', '/* ===== 消息兼容层'),
  between('function renderMiniMarkdown(src) {', 'function closeArtPreview'),
  between('function esc2(x) {', 'function renderDshHistory'),
].join('\n');

const sandbox = {};
const fn = new Function('window', code + '\n;return {esc, esc2, renderMiniMarkdown, dshBody, dshMsgHtml, dshSetBody};');
const M = fn(sandbox);

let pass = 0, fail = 0;
const t = (name, cond, extra) => { if (cond) { pass++; } else { fail++; console.log('FAIL: ' + name + (extra ? '\n      ' + extra : '')); } };

// ---------- 1. 表格（用户报的核心诉求）----------
const agentReply = [
  'T74 验收结论如下：',
  '',
  '| 检查项 | 结果 | 备注 |',
  '| --- | --- | --- |',
  '| BGM 接口 | ✅ | 4 处接线完成 |',
  '| 主菜单 | ✅ | 已补 setBGM |',
  '| 战斗内 | ⏳ | 待复验 |',
  '',
  '综上，剩 `战斗内` 一项。',
].join('\n');

const html = M.renderMiniMarkdown(agentReply);
t('表格：生成 <table>', /<table>/.test(html), html);
t('表格：表头三列', /<thead><tr><th>检查项<\/th><th>结果<\/th><th>备注<\/th><\/tr><\/thead>/.test(html), html);
t('表格：表头 1 行 + 数据 3 行', (html.match(/<tr>/g) || []).length === 4, '实际 ' + (html.match(/<tr>/g) || []).length + ' 个 <tr>');
t('表格：每个数据行 3 个单元格', (html.match(/<tr><td>/g) || []).length === 3 && (html.match(/<td>/g) || []).length === 9, html);
t('表格：分隔行 |---| 未被当成数据行', !/<td>-{3,}<\/td>/.test(html) && !/<td><\/td>/.test(html), html);
t('表格：单元格内联码生效', /<td>✅<\/td>/.test(html) && /<code>战斗内<\/code>/.test(html), html);
t('表格：段落在表格前后各自成 <p>', /<p>T74 验收结论如下：<\/p>/.test(html), html);

const wide = M.dshMsgHtml('assistant', agentReply);
t('含表格的气泡带 md-wide（放宽到整行）', /dsh-msg assistant md-wide/.test(wide), wide.slice(0, 120));
t('气泡正文包在 .md 里（关掉 pre-wrap，否则块级元素间会多空行）', /<div class="md">/.test(wide), wide.slice(0, 200));

const plainReply = M.dshMsgHtml('assistant', '收到，不需要排版。');
t('不含表格的气泡不加 md-wide', !/md-wide/.test(plainReply), plainReply);

// ---------- 2. 有序列表 ----------
const olHtml = M.renderMiniMarkdown('1. 第一步\n2. 第二步\n3. 第三步');
t('有序列表：<ol>', /<ol>/.test(olHtml) && !/<ul>/.test(olHtml), olHtml);
t('有序列表：三个 <li>', (olHtml.match(/<li>/g) || []).length === 3, olHtml);
t('有序列表：编号未被写进正文', !/<li>1\./.test(olHtml), olHtml);

const olStart = M.renderMiniMarkdown('3. 从三开始\n4. 继续');
t('有序列表：非 1 起始带 start 属性', /<ol start="3">/.test(olStart), olStart);

const ulHtml = M.renderMiniMarkdown('- 甲\n- 乙');
t('无序列表仍是 <ul>', /<ul>/.test(ulHtml) && !/<ol/.test(ulHtml), ulHtml);

const mixed = M.renderMiniMarkdown('- 甲\n1. 乙');
t('无序切有序：先闭合 </ul> 再开 <ol>', /<\/ul>\s*<ol>/.test(mixed), mixed);

// ---------- 3. 待办清单 ----------
const taskHtml = M.renderMiniMarkdown('- [x] 已完成的事\n- [ ] 还没做的事\n- [X] 大写 X');
const boxes = taskHtml.match(/<input type="checkbox" disabled( checked)?>/g) || [];
t('待办：三个复选框', boxes.length === 3, taskHtml);
t('待办：勾选状态正确（checked / 无 / checked）',
  boxes[0] === '<input type="checkbox" disabled checked>' &&
  boxes[1] === '<input type="checkbox" disabled>' &&
  boxes[2] === '<input type="checkbox" disabled checked>', JSON.stringify(boxes));
t('待办：检查框文本被剥掉（不显示 [x]）', !/\[x\]/.test(taskHtml) && !/\[ \]/.test(taskHtml), taskHtml);
t('待办：li 带 md-task 类', (taskHtml.match(/class="md-task"/g) || []).length === 3, taskHtml);

// ---------- 4. 转义与安全性 ----------
const evil = M.renderMiniMarkdown('看这个 <img src=x onerror=alert(1)> 和 <script>bad()</script>');
t('XSS：<> 被转义，不产生真的标签', !/<img/.test(evil) && !/<script/.test(evil), evil);
t('XSS：文本仍可见', /&lt;img/.test(evil) && /&lt;script&gt;/.test(evil), evil);

const dq = M.renderMiniMarkdown('他说 "你好" 并用了 \'引号\'');
t('引号被转义（不破坏属性）', /&quot;/.test(dq) && /&#39;/.test(dq), dq);

// ---------- 5. 用户自己的话保持原样 ----------
const userMd = M.dshMsgHtml('user', '**不是加粗** | 不是表格 |\n第二行');
t('用户消息：不渲染 markdown', !/<strong>/.test(userMd) && !/<table>/.test(userMd), userMd);
t('用户消息：星号和竖线原样保留', /\*\*不是加粗\*\* \| 不是表格 \|/.test(userMd), userMd);
t('用户消息：不是 .md（保留 pre-wrap，换行可见）', !/<div class="md">/.test(userMd), userMd);
t('用户消息：角色类为 user、称呼为「你」', /dsh-msg user/.test(userMd) && /<span class="who">你<\/span>/.test(userMd), userMd);

// ---------- 6. 代码块与其他角色 ----------
const fence = M.renderMiniMarkdown('```js\nconst a = 1 | 2;\n```');
t('围栏代码块：<pre><code>', /<pre><code>const a = 1 \| 2;/.test(fence), fence);
t('围栏代码块：竖线不被当表格', !/<table>/.test(fence), fence);

const thinking = M.dshMsgHtml('thinking', '**思考中** | x');
t('thinking 角色：不渲染 markdown', !/<strong>/.test(thinking) && /dsh-msg thinking/.test(thinking), thinking);

const customWho = M.dshMsgHtml('assistant', '你好', '规划Agent');
t('自定义发言人名（讨论室用）', /<span class="who">规划Agent<\/span>/.test(customWho), customWho);

// ---------- 7. dshSetBody：流式替换时必须同步 md-wide ----------
function fakeEl() {
  const set = new Set();
  return {
    innerHTML: '',
    classList: { toggle: (n, on) => { on ? set.add(n) : set.delete(n); }, contains: n => set.has(n), _set: set },
  };
}
const el = fakeEl();
M.dshSetBody(el, 'assistant', '普通一句话');
t('dshSetBody：普通回复不加 md-wide', !el.classList.contains('md-wide'), el.innerHTML);
t('dshSetBody：换行符不被渲染，正文进 .md', /<div class="md">/.test(el.innerHTML) && !/dsh-msg/.test(el.innerHTML), el.innerHTML);
M.dshSetBody(el, 'assistant', agentReply);
t('dshSetBody：流式更新到含表格时补上 md-wide', el.classList.contains('md-wide'), el.innerHTML.slice(0, 80));
M.dshSetBody(el, 'assistant', '表格又没了');
t('dshSetBody：正文不再含表格时摘掉 md-wide', !el.classList.contains('md-wide'), el.innerHTML);
M.dshSetBody(null, 'assistant', '空元素不崩');
t('dshSetBody：null 元素安全返回', true);

console.log('\n' + pass + ' 通过 / ' + fail + ' 失败');
process.exit(fail ? 1 : 0);
