#!/usr/bin/env node
/**
 * T11 回归测试：组织架构页右栏＝任务板摘要（v2.20.16）
 *
 * 背景
 * ----
 * 右栏原本是四张互不相干的卡片（在等你 / 未闭合事项 / 决策沉淀 / 最近发生），
 * 用户反馈「信息还可以，但有点乱」，并要求：去掉决策沉淀、把右栏定位成
 * 「任务板最上面那段总结」、在右栏放一个进入任务板的入口。
 * v2.20.16 改成三张卡：在等你 / 任务板（阶段分布条 + 阶段图例 + 未闭合列表 +
 * 「展开 →」入口）/ 最近发生。「任务板」卡用与看板同一套阶段词（待办/进行中/
 * 待验收/阻塞/已完成）和同一套状态点颜色（.c-todo/.c-doing/.c-review/.c-blocked/
 * .c-done）——右栏与看板说的是同一种语言。
 *
 * 本测试从 org_chart.html 抽出真实的 renderSide() 运行（不复制实现），
 * 用假 board 数据断言渲出的 HTML。
 *
 * 跑法：node tests/t11_side_panel_test.js
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

// 真实实现：esc() + renderSide()
const code = [
  between('function esc(s) {', '/* ===== 消息兼容层'),
  between('function renderSide() {', 'function scrollToBottom(el)'),
].join('\n');

const fn = new Function('document', 'board', 'agentById', 'msgToText', 'fmtTime',
  code + '\n;return renderSide;');

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (extra ? '\n      → ' + extra : '')); }
}
function count(hay, needle) { return hay.split(needle).length - 1; }

// 用假数据渲染一次，返回右栏 innerHTML
function render(board) {
  const el = { innerHTML: '' };
  const doc = { getElementById: (id) => (id === 'sidePanel' ? el : null) };
  const agentById = {};
  (board.agents || []).forEach(a => { agentById[a.id] = a; });
  const msgToText = (m) => m.to || '所有人';
  const fmtTime = () => '10-09 00:00';
  fn(doc, board, agentById, msgToText, fmtTime)();
  return el.innerHTML;
}

const AG = [
  { id: 'user', name: 'Zames', role: '项目主导方' },
  { id: 'planner', name: '规划Agent', role: '任务拆解' },
  { id: 'builder5', name: '玩法设计Agent(builder5)', role: '玩法' },
];
const T = (id, status, assignee, title) => ({ id, status, assignee, title });

console.log('== 1. 三张卡的结构 ==');
{
  const h = render({ agents: AG, tasks: [T('T1', 'done', 'planner', '已完成的活')], messages: [], decisions: [] });
  ok('只剩三张 .panel', count(h, 'class="panel"') === 3, 'got ' + count(h, 'class="panel"'));
  ok('有「在等你」', h.includes('>在等你<'));
  ok('有「任务板」', h.includes('>任务板<'));
  ok('有「最近发生」', h.includes('>最近发生<'));
  ok('没有「决策沉淀」', !h.includes('决策沉淀'));
  ok('没有 .decision-item', !h.includes('decision-item'));
}

console.log('\n== 2. 进入任务板的入口 ==');
{
  const h = render({ agents: AG, tasks: [], messages: [], decisions: [] });
  ok('标题行有 .panel-more 按钮', h.includes('class="panel-more"'));
  ok('按钮 onclick 调 openBoard()', h.includes('onclick="openBoard()"'));
  ok('按钮文案是「展开 →」', /展开\s*<span class="arw">→<\/span>/.test(h));
  ok('按钮带 title 提示', h.includes('title="进入任务板"'));
  ok('文件里 openBoard 指向看板页', /function openBoard\(\)[\s\S]{0,200}collab_board_nothing\.html/.test(src));
  ok('文件里 openBoard 带 project 参数', /function openBoard\(\)[\s\S]{0,300}encodeURIComponent\(PROJECT\)/.test(src));
}

console.log('\n== 3. 阶段分布条与图例 ==');
{
  const h = render({
    agents: AG, decisions: [], messages: [],
    tasks: [
      T('T1', 'todo', 'planner', 'a'), T('T2', 'todo', 'planner', 'b'),
      T('T3', 'in_progress', 'planner', 'c'), T('T4', 'in_progress', 'planner', 'd'), T('T5', 'in_progress', 'planner', 'e'),
      T('T6', 'review', 'planner', 'f'),
      T('T7', 'done', 'planner', 'g'), T('T8', 'done', 'planner', 'h'),
    ],
  });
  ok('有 .stage-bar', h.includes('class="stage-bar"'));
  ok('段数与阶段数一致（4）', count(h, 'class="seg ') === 4, 'got ' + count(h, 'class="seg '));
  ok('待办段 c-todo flex=2', h.includes('class="seg c-todo" style="flex:2"'));
  ok('进行中段 c-doing flex=3', h.includes('class="seg c-doing" style="flex:3"'));
  ok('待验收段 c-review flex=1', h.includes('class="seg c-review" style="flex:1"'));
  ok('已完成段 c-done flex=2', h.includes('class="seg c-done" style="flex:2"'));
  ok('零计数的阶段不出现', !h.includes('c-blocked'));
  ok('图例用看板阶段词：待办', h.includes('待办 <b>2</b>'));
  ok('图例：进行中 3', h.includes('进行中 <b>3</b>'));
  ok('图例：待验收 1', h.includes('待验收 <b>1</b>'));
  ok('图例：已完成 2', h.includes('已完成 <b>2</b>'));
  ok('图例 4 条', count(h, 'class="lg"') === 4);
}

console.log('\n== 4. 阻塞阶段 ==');
{
  const h = render({
    agents: AG, decisions: [], messages: [],
    tasks: [T('T1', 'blocked', 'planner', '卡住了'), T('T2', 'done', 'planner', 'ok')],
  });
  ok('blocked 计为阻塞段', h.includes('class="seg c-blocked"'));
  ok('图例显示「阻塞 1」', h.includes('阻塞 <b>1</b>'));
  ok('未闭合列表里的点也是 c-blocked', h.includes('class="st-dot c-blocked"'));
}

console.log('\n== 5. 未闭合列表 ==');
{
  const tasks = [
    T('T1', 'in_progress', 'builder5', '在做的活'),
    T('T2', 'todo', 'user', '等我的活'),
    T('T3', 'done', 'planner', '做完的活'),
    T('T4', 'review', 'planner', '待验收的活'),
  ];
  const h = render({ agents: AG, tasks, messages: [], decisions: [] });
  ok('列表标题「未闭合 2」', h.includes('未闭合 2'), '(等我的 + 在做的，各 1)');
  ok('含非 user 的在途任务', h.includes('在做的活') && h.includes('待验收的活'));
  ok('不含指派给 user 的（那属于「在等你」）', !h.includes('等我的活'));
  ok('不含已完成的', !h.includes('做完的活'));
  ok('负责人显示为姓名', h.includes('玩法设计Agent(builder5)'));
  ok('「在等你」大数字为 1', /class="big-num warn">1</.test(h));
  ok('readout 未闭合事项 = 2', h.includes('<span class="readout-val">2</span>'), h.slice(0, 400));
  ok('readout 班子成员 = 3', h.includes('<span class="readout-val">3</span>'));
}

console.log('\n== 6. 列表上限 6 ==');
{
  const tasks = [];
  for (let i = 1; i <= 9; i++) tasks.push(T('T' + i, 'todo', 'planner', '任务' + i));
  const h = render({ agents: AG, tasks, messages: [], decisions: [] });
  ok('最多 6 行 .pending-item', count(h, 'class="pending-item"') === 6, 'got ' + count(h, 'class="pending-item"'));
  ok('含第 6 条', h.includes('任务6'));
  ok('不含第 7 条', !h.includes('任务7'));
}

console.log('\n== 7. 空看板不崩 ==');
{
  const h = render({ agents: [], tasks: [], messages: [], decisions: [] });
  ok('没有阶段条（零计数）', !h.includes('class="stage-bar"'));
  ok('标题与入口仍在', h.includes('>任务板<') && h.includes('onclick="openBoard()"'));
  ok('空态文案', h.includes('全部闭合，没有在途事项'));
  ok('最近发生空态', h.includes('还没有消息往来'));
  ok('未闭合 0', h.includes('未闭合 0'));
}

console.log('\n== 8. 转义 ==');
{
  const evil = '<img src=x onerror=alert(1)>';
  const h = render({
    agents: AG, decisions: [], messages: [],
    tasks: [T('T1', 'todo', 'planner', evil)],
  });
  ok('任务标题被转义', !h.includes(evil) && h.includes('&lt;img src=x onerror=alert(1)&gt;'));
}

console.log('\n== 9. 样式与死代码 ==');
{
  ok('.c-done 已定义（绿色）', /\.c-done\s*\{\s*background:\s*var\(--green\);\s*\}/.test(src));
  ok('.panel-more 的箭头有过渡', /\.panel-more:hover \.arw\s*\{\s*transform:\s*translateX\(3px\)/.test(src));
  ok('.stage-bar .seg 占满高度', /\.stage-bar \.seg\s*\{\s*display:\s*block;\s*height:\s*100%;/.test(src));
  ok('文件里已无 .decision-item 规则（死代码清干净）', !src.includes('.decision-item'));
  ok('文件里已无「决策沉淀」', !src.includes('决策沉淀'));
}

console.log('\n' + '='.repeat(60));
console.log(pass + ' 通过 / ' + fail + ' 失败');
if (fail) process.exit(1);
console.log('全部通过 ✓');
