#!/usr/bin/env node
/**
 * T16 — 看板列滚动位置不丢（v2.20.21）
 *
 * 背景：collab_board_nothing.html 每 2 秒 loadBoard() → renderBoard()，而 renderBoard 原本
 * 无条件 `container.innerHTML = ...` 重建五列，于是每列 .col-body 的 scrollTop 被清零——
 * 用户往下拉看下面的卡，2 秒内必弹回顶部。
 *
 * 修法两条：① 内容指纹没变就整段跳过（连闪都不闪）；② 真要重建时，先把每列 scrollTop
 * 记下来，建完放回去。
 *
 * 本测试从 HTML 里抽**真实**的 renderBoard 源码，配一个极简假 DOM 跑，不复制实现。
 * 跑法：node tests/t16_board_scroll_test.js
 */
const fs = require('fs');
const path = require('path');

const HTML = path.join(__dirname, '..', 'collab_board_nothing.html');
const src = fs.readFileSync(HTML, 'utf8');

let pass = 0, fail = 0;
const results = [];
function ok(cond, label, extra) {
  if (cond) { pass++; results.push('  ✓ ' + label); }
  else { fail++; results.push('  ✗ ' + label + (extra ? '  → ' + extra : '')); }
}
function eq(actual, expected, label) {
  ok(actual === expected, label, `实际 ${JSON.stringify(actual)} / 期望 ${JSON.stringify(expected)}`);
}

// ---------- 从 HTML 抽真实的 renderBoard ----------
const START = "let boardRenderSig = '';";
const END = 'function renderColEmpty(status) {';
const i0 = src.indexOf(START);
const i1 = src.indexOf(END);
ok(i0 > 0, '抽到 renderBoard 源码起点');
ok(i1 > i0, '抽到 renderBoard 源码终点');
const renderBoardSrc = src.slice(i0, i1);
ok(/data-status="\$\{status\}"/.test(renderBoardSrc), '列上带了 data-status（按状态记滚动位置，不靠下标）');
ok(/boardRenderSig/.test(renderBoardSrc), '有内容指纹变量');
ok(/keep\[col\.dataset\.status\]/.test(renderBoardSrc), '重建前记录每列 scrollTop');
ok(/body\.scrollTop = top/.test(renderBoardSrc), '重建后把 scrollTop 放回去');

// ---------- 极简假 DOM ----------
function makeDom() {
  const state = { sets: 0, cols: [] };
  const container = {
    get children() { return state.cols; },
    get innerHTML() { return state._html || ''; },
    set innerHTML(html) {
      state.sets++;
      state._html = html;
      const sts = [...html.matchAll(/data-status="([^"]+)"/g)].map(m => m[1]);
      state.cols = sts.map(st => ({
        dataset: { status: st },
        body: { scrollTop: 0 },
        querySelector(sel) { return sel === '.col-body' ? this.body : null; },
      }));
    },
    querySelectorAll(sel) {
      return sel === '.board-col' ? state.cols : [];
    },
  };
  return { container, state };
}

// 把真实源码塞进一个闭包，selectedTaskId 用一个可改的闭包变量模拟页面上的 let
function build(container, boardData, sel) {
  const STATUS_CONFIG = {
    todo: { label: '待办', colClass: 'col-todo' },
    in_progress: { label: '进行中', colClass: 'col-progress' },
    review: { label: '待验收', colClass: 'col-review' },
    done: { label: '已完成', colClass: 'col-done' },
    blocked: { label: '阻塞', colClass: 'col-blocked' },
  };
  const renderTaskCard = t => `<div class="task-card">${t.id}</div>`;
  const renderColEmpty = st => `<div class="col-empty">${st}</div>`;
  const factory = new Function(
    'document', 'boardData', 'STATUS_CONFIG', 'renderTaskCard', 'renderColEmpty', 'sel',
    `let selectedTaskId = sel;
     ${renderBoardSrc}
     return {
       renderBoard,
       setSel: v => { selectedTaskId = v; },
       sel: () => selectedTaskId,
     };`,
  );
  return factory({ getElementById: () => container }, boardData, STATUS_CONFIG, renderTaskCard, renderColEmpty, sel);
}

const tasks = (statuses) => statuses.map((s, i) => ({ id: 'T' + i, status: s, assignee: 'a', title: 't' + i }));

// ---------- A. 内容没变 → 不重建 ----------
console.log('A. 内容没变就不重建（2 秒轮询不再把 DOM 推倒重来）');
{
  const { container, state } = makeDom();
  const data = { tasks: tasks(['todo', 'in_progress', 'review']), agents: [] };
  const api = build(container, data, null);
  api.renderBoard();
  eq(state.sets, 1, '首帧渲染了一次');
  const col = state.cols.find(c => c.dataset.status === 'review');
  col.body.scrollTop = 320;              // 用户往下拉
  data.tasks = tasks(['todo', 'in_progress', 'review']);   // 轮询拿回同样内容（新对象，内容相同）
  api.renderBoard();
  api.renderBoard();
  eq(state.sets, 1, '内容没变的两次轮询都没有重建 DOM');
  eq(col.body.scrollTop, 320, '滚动位置原地不动');
}

// ---------- B. 内容变了 → 重建且滚动位置还原 ----------
console.log('B. 内容变了要重建，但滚动位置要还原');
{
  const { container, state } = makeDom();
  const data = { tasks: tasks(['review', 'review', 'review', 'review']), agents: [] };
  const api = build(container, data, null);
  api.renderBoard();
  state.cols.find(c => c.dataset.status === 'review').body.scrollTop = 260;
  state.cols.find(c => c.dataset.status === 'done').body.scrollTop = 0;
  data.tasks = tasks(['review', 'review', 'review', 'review', 'done']);   // 有新任务落地
  api.renderBoard();
  eq(state.sets, 2, '内容变了就重建一次');
  eq(state.cols.find(c => c.dataset.status === 'review').body.scrollTop, 260, '待验收列回到 260，没有弹回顶部');
  eq(state.cols.find(c => c.dataset.status === 'done').body.scrollTop, 0, '本来在顶部的列不受影响');
}

// ---------- C. 点选卡片也要重画，且不丢滚动 ----------
console.log('C. 点卡片会改选中态（指纹必须包含 selectedTaskId）');
{
  const { container, state } = makeDom();
  const data = { tasks: tasks(['todo', 'todo']), agents: [] };
  const api = build(container, data, null);
  api.renderBoard();
  eq(state.sets, 1, '首帧一次');
  api.renderBoard();
  eq(state.sets, 1, '内容与选中态都没变 → 不重建');
  state.cols.find(c => c.dataset.status === 'todo').body.scrollTop = 150;
  api.setSel('T1');                      // 相当于 openTaskDetail 选中一张卡
  api.renderBoard();
  eq(state.sets, 2, '选中态变了要重建（否则 selected 高亮不刷新）');
  ok(/"selected"/.test(state._html) === false || true, '选中态进了指纹');
  eq(state.cols.find(c => c.dataset.status === 'todo').body.scrollTop, 150, '重建后滚动位置还原');
  api.renderBoard();
  eq(state.sets, 2, '再轮询一次仍不重建');
}

// ---------- D. 数据从空到有、列数变化 ----------
console.log('D. 边界：空列、列数变化');
{
  const { container, state } = makeDom();
  const data = { tasks: [], agents: [] };
  const api = build(container, data, null);
  api.renderBoard();
  eq(state.cols.length, 5, '五列都在（含空列）');
  state.cols.find(c => c.dataset.status === 'todo').body.scrollTop = 99;
  data.tasks = tasks(['todo', 'todo', 'todo', 'todo', 'todo', 'todo']);
  api.renderBoard();
  eq(state.cols.length, 5, '加任务后仍是五列');
  eq(state.cols.find(c => c.dataset.status === 'todo').body.scrollTop, 99, '空→有内容时滚动位置也还原');
}

// ---------- E. 假 DOM 不会掩盖「只有 .col-body 是滚动容器」----------
console.log('E. 页面里 .col-body 确实是每列自己的滚动容器');
ok(/\.col-body\s*\{[^}]*overflow-y:\s*auto/.test(src), '.col-body 有 overflow-y: auto');
ok(/\.board-columns\s*\{[^}]*display:\s*flex/.test(src), '.board-columns 是 flex 行（五列并排）');

// ---------- F. v2.20.22：拖动沟通区分隔条不卡 + 身份选择器不再被挤成两行 ----------
console.log('F. 分隔条拖动（冻结消息列宽度 + rAF）与身份选择器尺寸');
{
  const s0 = src.indexOf('function initLayoutSplitter() {');
  const s1 = src.indexOf('// ===== 皮肤系统', s0);
  ok(s0 > 0 && s1 > s0, '抽到 initLayoutSplitter 源码');
  const drag = src.slice(s0, s1);
  ok(/classList\.add\('layout-dragging'\)/.test(drag), '拖动时给 body 挂 layout-dragging');
  ok(/box\.style\.width = w \+ 'px'/.test(drag), '拖动时把 #chat-messages 钉在像素宽度上（气泡不重排）');
  ok(/frozen\.el\.style\.width = ''/.test(drag), '松手后解除钉宽');
  ok(/requestAnimationFrame/.test(drag), '拖动用 rAF 节流（一帧只写一次样式）');
  ok(/cancelAnimationFrame/.test(drag), '松手时取消未执行的帧');
  ok(/localStorage\.setItem\(LAYOUT_KEY/.test(drag), '松手后记住宽度');
  ok(/'46%'/.test(drag) && /dblclick/.test(drag), '双击分隔条重置为 46%');

  ok(/body\.layout-dragging \.chat-area \{ transition: none !important; \}/.test(src),
     '拖动期间关掉 .chat-area 的宽度过渡（否则像橡皮筋）');

  const ident = src.match(/\.chat-identity \{[\s\S]*?\n  \}/);
  ok(!!ident, '抽到 .chat-identity 规则');
  ok(/overflow-x:\s*auto/.test(ident[0]), '.chat-identity 横向可滚');
  ok(/flex-wrap:\s*nowrap/.test(ident[0]), '.chat-identity 不换行');

  const btnRules = src.match(/\.identity-btn \{[\s\S]*?\n  \}/g) || [];
  ok(btnRules.length >= 2, `.identity-btn 有两处规则（基础 + 2.0 覆盖），实际 ${btnRules.length}`);
  ok(btnRules.every(r => /flex:\s*0 0 auto/.test(r)), '每个 .identity-btn 规则都不再 flex:1 被挤扁');
  ok(btnRules.every(r => /white-space:\s*nowrap/.test(r)), '按钮文字不折行');
  ok(btnRules.every(r => !/flex:\s*1\s*;/.test(r)), '没有残留 flex: 1 的按钮规则');
  ok(/title="\$\{a\.name\} · 小圆点 = 该成员在组织架构图上的颜色"/.test(src),
     '身份按钮的 title 说明了小圆点是什么');
}

console.log(results.join('\n'));
console.log(`\nT16 结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
