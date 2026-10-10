#!/usr/bin/env node
/**
 * T13 回归测试：左栏收起 + 空白处拖动整个画布（v2.20.17）
 *
 * 背景
 * ----
 * 两个手感需求：
 *  1. 左边项目栏能收起来，把宽度让给组织架构图（收起状态记在 localStorage）。
 *  2. 在画布空白处按住鼠标左键就能拖整个界面（平移），不必去够滚动条。
 *
 * 平移的实现刻意选了「改滚动容器的 scrollLeft/scrollTop」而不是给 #zoomLayer 加 transform：
 * 后者会和 applyZoom() 的 transform、以及节点绝对定位坐标打架。代价是必须放过
 * .node —— 节点自己有一套 pointerdown 拖拽（enableNodeDrag），抢了就会「点节点变拖画布」。
 *
 * 本测试从 org_chart.html 抽出真实函数/IIFE，用假 DOM 跑，不复制实现。
 *
 * 跑法：node tests/t13_projbar_pan_test.js
 */
const fs = require('fs');
const path = require('path').join(__dirname, '..', 'org_chart.html');
const src = fs.readFileSync(path, 'utf8');

function between(startMark, endMark) {
  const i = src.indexOf(startMark);
  if (i < 0) throw new Error('extract failed: ' + startMark);
  const j = src.indexOf(endMark, i);
  if (j < 0) throw new Error('extract end failed: ' + endMark);
  return src.slice(i, j);
}

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (extra ? '\n      → ' + extra : '')); }
}

/* ---------- 假 DOM ---------- */
function fakeClassList() {
  const s = new Set();
  return {
    _s: s,
    add: (c) => s.add(c),
    remove: (c) => s.delete(c),
    contains: (c) => s.has(c),
    toggle: (c, on) => { if (on === undefined) { s.has(c) ? s.delete(c) : s.add(c); } else if (on) { s.add(c); } else { s.delete(c); } },
  };
}
function fakeEl() {
  const h = {};
  return {
    _h: h,
    scrollLeft: 0, scrollTop: 0,
    classList: fakeClassList(),
    addEventListener(t, fn) { (h[t] = h[t] || []).push(fn); },
    fire(t, ev) { (h[t] || []).forEach(fn => fn(ev || {})); },
    setPointerCapture() {}, releasePointerCapture() {},
    closest() { return null; },
  };
}
function fakeStorage(init) {
  const m = Object.assign({}, init || {});
  return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, _m: m };
}

/* ---------- 抽出真实代码 ---------- */
const projCode = between('function toggleProjBar(collapse) {', '/* ===== 空白处按住左键拖动整个画布（v2.20.17）===== */');
const panCode = between('(function initCanvasPan() {', '/* ===== 渲染 ===== */');

// 每次 eval 都造一套新的假环境（IIFE 会在 eval 时立刻执行）
function loadProjBar(stored) {
  const body = fakeEl();
  const doc = { body };
  const storage = fakeStorage(stored);
  const fn = new Function('document', 'localStorage', projCode + '\n;return { toggleProjBar };');
  const api = fn(doc, storage);   // initProjBar 是具名 IIFE，eval 时已执行，取不到名字
  return { body, storage, api, doc };
}
function loadPan() {
  const wrap = fakeEl();
  const body = fakeEl();
  const doc = { getElementById: (id) => (id === 'canvasWrap' ? wrap : null), body };
  const win = fakeEl();
  const fn = new Function('document', 'window', panCode);
  fn(doc, win);
  return { wrap, body, win };
}
const ev = (x, y, target, button) => ({
  button: button === undefined ? 0 : button,
  clientX: x, clientY: y,
  pointerId: 1,
  target: target || { closest: () => null },
});
const onNode = { closest: (sel) => (sel === '.node' ? { node: true } : null) };
const onButton = { closest: (sel) => (sel === '.node' ? null : (sel.indexOf('button') >= 0 ? { btn: true } : null)) };

console.log('== 1. 左栏收起：状态与持久化 ==');
{
  const t = loadProjBar({});
  ok('初始不是收起态', !t.body.classList.contains('proj-collapsed'));
  t.api.toggleProjBar(true);
  ok('收起后 body 带 proj-collapsed', t.body.classList.contains('proj-collapsed'));
  ok('收起状态写进 localStorage', t.storage._m['agentnexus_projbar'] === 'collapsed', JSON.stringify(t.storage._m));
  t.api.toggleProjBar(false);
  ok('展开后类被移除', !t.body.classList.contains('proj-collapsed'));
  ok('展开状态写进 localStorage', t.storage._m['agentnexus_projbar'] === 'open');
  t.api.toggleProjBar(true);
  t.api.toggleProjBar();
  ok('不传参数＝取反（收起→展开）', !t.body.classList.contains('proj-collapsed'));
  t.api.toggleProjBar();
  ok('再取反（展开→收起）', t.body.classList.contains('proj-collapsed'));
}

console.log('\n== 2. 左栏收起：刷新后恢复 ==');
{
  const a = loadProjBar({ agentnexus_projbar: 'collapsed' });
  ok('存的是 collapsed → 首帧就是收起态', a.body.classList.contains('proj-collapsed'));
  const b = loadProjBar({ agentnexus_projbar: 'open' });
  ok('存的是 open → 不收起', !b.body.classList.contains('proj-collapsed'));
  const c = loadProjBar({});
  ok('没存过 → 默认展开（不改变老用户观感）', !c.body.classList.contains('proj-collapsed'));
}

console.log('\n== 3. 左栏收起：接线与样式 ==');
{
  ok('项目栏标题里有收起按钮', /class="proj-bar-head">项目<button class="proj-collapse"[^>]*onclick="toggleProjBar\(true\)"/.test(src));
  ok('收起按钮有 aria-label', /id="projCollapse"[^>]*aria-label="收起项目栏"/.test(src));
  ok('画布左上角有展开按钮', /id="projReveal"[^>]*onclick="toggleProjBar\(false\)"/.test(src));
  ok('展开按钮有 aria-label', /id="projReveal"[^>]*aria-label="展开项目栏"/.test(src));
  ok('收起时隐藏项目栏与分隔条', /body\.proj-collapsed \.proj-bar, body\.proj-collapsed #splitLeft\s*\{\s*display:\s*none/.test(src));
  ok('展开按钮默认不显示', /\.proj-reveal\s*\{[^}]*display:\s*none/.test(src));
  ok('收起时才显示展开按钮', /body\.proj-collapsed \.proj-reveal\s*\{\s*display:\s*inline-flex/.test(src));
  ok('.main 是定位上下文（展开按钮绝对定位）', /\.main\s*\{[^}]*position:\s*relative/.test(src));
}

console.log('\n== 4. 画布平移：空白处拖动 ==');
{
  const p = loadPan();
  p.wrap.scrollLeft = 300; p.wrap.scrollTop = 120;
  p.wrap.fire('pointerdown', ev(500, 400));
  ok('按下时先不动（单击不该闪 grabbing）', !p.wrap.classList.contains('panning'));
  p.wrap.fire('pointermove', ev(460, 430));
  // 内容跟着手走：往左拖 40px → scrollLeft +40；往下拖 30px → scrollTop -30
  ok('内容跟着手走（scrollLeft +40 / scrollTop -30）', p.wrap.scrollLeft === 340 && p.wrap.scrollTop === 90,
     'scrollLeft=' + p.wrap.scrollLeft + ' scrollTop=' + p.wrap.scrollTop);
  ok('拖动时给容器加 panning', p.wrap.classList.contains('panning'));
  ok('拖动时给 body 加 canvas-panning（禁选中）', p.body.classList.contains('canvas-panning'));
  p.wrap.fire('pointerup', ev(460, 430));
  ok('松手后清掉 panning', !p.wrap.classList.contains('panning'));
  ok('松手后清掉 canvas-panning', !p.body.classList.contains('canvas-panning'));
}

console.log('\n== 5. 画布平移：不抢节点的拖拽 ==');
{
  const p = loadPan();
  p.wrap.scrollLeft = 300; p.wrap.scrollTop = 120;
  p.wrap.fire('pointerdown', ev(500, 400, onNode));
  p.wrap.fire('pointermove', ev(400, 300));
  ok('按在 .node 上不平移', p.wrap.scrollLeft === 300 && p.wrap.scrollTop === 120,
     'scrollLeft=' + p.wrap.scrollLeft);
  ok('按在 .node 上不加 panning', !p.wrap.classList.contains('panning'));
}

console.log('\n== 6. 画布平移：不抢按钮/右键 ==');
{
  const a = loadPan();
  a.wrap.fire('pointerdown', ev(500, 400, onButton));
  a.wrap.fire('pointermove', ev(400, 300));
  ok('按在按钮上不平移', a.wrap.scrollLeft === 0 && !a.wrap.classList.contains('panning'));
  const b = loadPan();
  b.wrap.fire('pointerdown', ev(500, 400, null, 2));
  b.wrap.fire('pointermove', ev(400, 300));
  ok('右键不平移', b.wrap.scrollLeft === 0 && !b.wrap.classList.contains('panning'));
  const c = loadPan();
  c.wrap.fire('pointerdown', ev(500, 400, null, 1));
  c.wrap.fire('pointermove', ev(400, 300));
  ok('中键不平移', c.wrap.scrollLeft === 0);
}

console.log('\n== 7. 画布平移：收尾路径 ==');
{
  const p = loadPan();
  p.wrap.fire('pointerdown', ev(500, 400));
  p.wrap.fire('pointermove', ev(400, 300));
  p.wrap.fire('pointercancel', ev(400, 300));
  ok('pointercancel 也收尾', !p.wrap.classList.contains('panning') && !p.body.classList.contains('canvas-panning'));
  const q = loadPan();
  q.wrap.fire('pointerdown', ev(500, 400));
  q.wrap.fire('pointermove', ev(400, 300));
  q.win.fire('blur');
  ok('拖出窗口后 blur 收尾', !q.wrap.classList.contains('panning') && !q.body.classList.contains('canvas-panning'));
  const r = loadPan();
  r.wrap.fire('pointerup', ev(500, 400));
  ok('没按下就松手不报错', !r.wrap.classList.contains('panning'));
}

console.log('\n== 8. 画布平移：实现契约 ==');
{
  ok('平移改的是滚动位置，不是 transform', /wrap\.scrollLeft\s*=/.test(panCode) && !/transform/.test(panCode));
  ok('画布默认 grab 光标', /\.canvas-wrap\s*\{[^}]*cursor:\s*grab/.test(src));
  ok('拖动中 grabbing 光标（含子元素）', /\.canvas-wrap\.panning \*\s*\{\s*cursor:\s*grabbing !important/.test(src));
  ok('拖动中禁止选中文字', /body\.canvas-panning, body\.canvas-panning \*\s*\{\s*user-select:\s*none !important/.test(src));
  ok('用 pointer capture（拖出容器也能跟手）', /wrap\.setPointerCapture\(e\.pointerId\)/.test(panCode));
}

console.log('\n' + '='.repeat(60));
console.log(pass + ' 通过 / ' + fail + ' 失败');
if (fail) process.exit(1);
console.log('全部通过 ✓');
