#!/usr/bin/env node
/*
 * T10 — 看板页与组织架构页的「设计语言统一」回归测试（v2.20.14）
 *
 * 背景：collab_board_nothing.html 原本是 1.x 的 Nothing 像素复古风
 *（VT323/Silkscreen 像素字体、42px 网格底纹、橙红 #ff3621、
 * 「TASK BOARD //」式大写标签）。v2.20.14 把它整体并入 org_chart.html 的 2.0
 * 设计语言：同一套 CSS 令牌、同一套 .btn 按钮、纯色底、去掉外链字体。
 *
 * 这个测试直接读两个 HTML 文件（不依赖浏览器），锁住：
 *   1. 两页的 2.0 令牌逐项一致
 *   2. 外链像素字体与网格底纹确实没了
 *   3. 三个皮肤（nothing/apple/premium）下 1.x 的变量名都改指 2.0 的值
 *   4. 复古文案（「// TASK BOARD」「已复制 // COPIED」…）不再出现
 *   5. 顶栏结构对齐、切换/返回路径对称
 *   6. 沟通区全屏与 embed 模式的关键规则在位
 *
 * 跑法：node tests/t10_board_theme_test.js
 */
'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const BOARD = fs.readFileSync(path.join(ROOT, 'collab_board_nothing.html'), 'utf8');
const ORG = fs.readFileSync(path.join(ROOT, 'org_chart.html'), 'utf8');

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (extra ? '  → ' + extra : '')); }
}
function section(title) { console.log('\n' + title); }

/* ---------- 解析工具 ---------- */

// 取出从 `sel {` 开始到配对 `}` 的声明块文本（CSS 里没有嵌套规则，因此取第一个 }
function blockAfter(src, needle) {
  const i = src.indexOf(needle);
  if (i < 0) return null;
  const open = src.indexOf('{', i + needle.length - 1);
  if (open < 0) return null;
  const close = src.indexOf('}', open);
  if (close < 0) return null;
  return src.slice(open + 1, close);
}

// 找出所有 `[data-theme="x"] {` 块，返回按出现顺序的声明块数组
function themeBlocks(src, theme) {
  const out = [];
  let from = 0;
  for (;;) {
    const i = src.indexOf('[data-theme="' + theme + '"] {', from);
    if (i < 0) break;
    const open = src.indexOf('{', i);
    const close = src.indexOf('}', open);
    out.push(src.slice(open + 1, close));
    from = close;
  }
  return out;
}

// 从声明块里读一个自定义属性（返回规范化的值）
function cssVar(block, name) {
  if (!block) return null;
  const re = new RegExp('--' + name + '\\s*:\\s*([^;]+);');
  const m = block.match(re);
  return m ? m[1].trim() : null;
}

// 只在 CSS 区段里找（避免 JS 字符串干扰）
function styleOf(src) {
  const i = src.indexOf('<style>');
  const j = src.indexOf('</style>', i);
  return src.slice(i, j);
}
const BOARD_CSS = styleOf(BOARD);
const ORG_CSS = styleOf(ORG);

// 只看真实内容：注释里为了说明历史而提到旧文案/旧字体是允许的
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')      // CSS 块注释
    .replace(/<!--[\s\S]*?-->/g, '')       // HTML 注释
    .replace(/^[ \t]*\/\/.*$/gm, '');      // JS 行注释
}
const BOARD_TEXT = stripComments(BOARD);

/* ---------- 1. 2.0 令牌逐项一致 ---------- */
section('1. 两页共用同一套 2.0 令牌');
const boardRoot = blockAfter(BOARD_CSS, ':root {');
const orgRoot = blockAfter(ORG_CSS, ':root {');
const TOKENS = ['bg', 'bg-2', 'panel', 'panel-2', 'text', 'dim', 'dim-2', 'line', 'line-2',
  'accent', 'accent-rgb', 'green', 'amber', 'blue', 'purple', 'sans', 'mono'];
let tokenSame = 0;
TOKENS.forEach(function (t) {
  const b = cssVar(boardRoot, t), o = cssVar(orgRoot, t);
  const same = b !== null && b === o;
  if (same) tokenSame++;
  ok('--' + t + ' 与组织视图一致' + (same ? '（' + o + '）' : ''), same,
    '看板=' + JSON.stringify(b) + ' 组织=' + JSON.stringify(o));
});
ok('17 个令牌全部一致（实际 ' + tokenSame + ' 个）', tokenSame === TOKENS.length);

/* ---------- 2. 去掉外链像素字体与网格底纹 ---------- */
section('2. 1.x 的像素复古外观已退役');
ok('不再引用 Google Fonts', !/fonts\.googleapis\.com|fonts\.gstatic\.com/.test(BOARD_TEXT));
ok('不再有 Silkscreen / VT323 字样', !/Silkscreen|VT323/.test(BOARD_TEXT));
ok('--pixel 指向系统字体栈', cssVar(boardRoot, 'pixel') === 'var(--sans)');
ok('--radius 指向 --radius-md', cssVar(boardRoot, 'radius') === 'var(--radius-md)');
ok('body 背景改为纯色 var(--bg)',
  /body\s*\{[^}]*background:\s*var\(--bg\)/.test(BOARD_CSS));
ok('不再有 42px 网格底纹', !/repeating-linear-gradient[^;]*42px/.test(BOARD_CSS));
ok('旧橙红 #ff3621 已清空（除注释外）',
  (BOARD.match(/#ff3621/gi) || []).length === 0);

/* ---------- 3. 三个皮肤下 1.x 变量名都改指 2.0 的值 ---------- */
section('3. 皮肤覆盖块（apple / premium）');
const boardApple = themeBlocks(BOARD_CSS, 'apple');
const boardPremium = themeBlocks(BOARD_CSS, 'premium');
const newApple = boardApple[boardApple.length - 1];
const newPremium = boardPremium[boardPremium.length - 1];
ok('apple 覆盖块至少两个（旧块之后还有 2.0 对齐块）', boardApple.length >= 2, '实际 ' + boardApple.length);
ok('premium 覆盖块至少两个', boardPremium.length >= 2, '实际 ' + boardPremium.length);
ok('apple --accent 为 2.0 的 #ff3b30', cssVar(newApple, 'accent') === '#ff3b30', cssVar(newApple, 'accent'));
ok('apple --bg 为 2.0 的 #f5f5f7', cssVar(newApple, 'bg') === '#f5f5f7', cssVar(newApple, 'bg'));
ok('apple --red 改指 --accent（否则仍是旧块的蓝）', cssVar(newApple, 'red') === 'var(--accent)');
ok('apple --red-rgb 改指 --accent-rgb', cssVar(newApple, 'red-rgb') === 'var(--accent-rgb)');
ok('apple --pixel 指向 --sans', cssVar(newApple, 'pixel') === 'var(--sans)');
ok('premium --red 改指 --accent', cssVar(newPremium, 'red') === 'var(--accent)');
ok('premium --red-rgb 改指 --accent-rgb', cssVar(newPremium, 'red-rgb') === 'var(--accent-rgb)');
ok('premium --pixel 指向 --sans', cssVar(newPremium, 'pixel') === 'var(--sans)');
// 与组织视图的同名皮肤对齐
const orgApple = themeBlocks(ORG_CSS, 'apple');
const orgPremium = themeBlocks(ORG_CSS, 'premium');
const orgAppleLast = orgApple[orgApple.length - 1];
const orgPremiumLast = orgPremium[orgPremium.length - 1];
['bg', 'bg-2', 'text', 'accent'].forEach(function (t) {
  ok('apple --' + t + ' 与组织视图一致',
    cssVar(newApple, t) === cssVar(orgAppleLast, t),
    '看板=' + cssVar(newApple, t) + ' 组织=' + cssVar(orgAppleLast, t));
});
['bg', 'text', 'accent'].forEach(function (t) {
  ok('premium --' + t + ' 与组织视图一致',
    cssVar(newPremium, t) === cssVar(orgPremiumLast, t),
    '看板=' + cssVar(newPremium, t) + ' 组织=' + cssVar(orgPremiumLast, t));
});
ok('2.0 覆盖块写在 1.x 旧块之后（靠后写覆盖）',
  BOARD_CSS.indexOf('--accent: #ff3b30') > BOARD_CSS.indexOf('--red: #007aff'));

/* ---------- 4. 复古文案不再出现 ---------- */
section('4. 复古文案清理');
// 只看真实内容：注释里为了说明历史而提到旧文案是允许的
const RETRO = [
  'TASK BOARD', 'COLLAB //', '协作讨论 // CHAT', '任务板 //',
  'SET STATUS', '已复制 // COPIED', 'NOTIFICATION', 'ONBOARDING',
  'AGENT 接入状态 //', 'TYPE MESSAGE', '> TYPE', 'LIVE 状态',
  'API 设置 // LLM', '定时触发 // SCHEDULE', '指令 // CMD',
  '复制检查指令 // COPY', '轮询准则 // POLL', '接入引导 // GUIDE',
];
RETRO.forEach(function (t) {
  ok('不再出现「' + t + '」', BOARD_TEXT.indexOf(t) < 0);
});
// 皮肤按钮文案与组织视图保持一致（这三个是有意的 // 用法）
ok('皮肤按钮文案与组织视图一致（A // 亮色）', /A \/\/ 亮色/.test(BOARD) && /A \/\/ 亮色/.test(ORG));
ok('静态文本 AUTO / MANUAL 已中文化', !/>\s*AUTO\s*</.test(BOARD) && !/>\s*MANUAL\s*</.test(BOARD));

/* ---------- 5. 顶栏结构与切换/返回路径 ---------- */
section('5. 顶栏同构与切换返回');
ok('顶栏左侧有 .logo-mark 且为 ◆ AGENTNEXUS', /class="logo-mark"[^>]*>◆ AGENTNEXUS</.test(BOARD));
ok('顶栏左侧有 #home-toggle 且跳 /index.html',
  /id="home-toggle"[^>]*location\.href='\/index\.html'/.test(BOARD));
ok('顶栏有 #proj-name（项目名）', /id="proj-name"/.test(BOARD));
ok('顶栏有 #project-subtitle（页面名）', /id="project-subtitle"[^>]*>协作看板</.test(BOARD));
ok('顶栏右侧 #org-toggle 走 openOrgView()', /id="org-toggle"[^>]*openOrgView\(\)/.test(BOARD));
ok('顶栏右侧 #theme-toggle 走 toggleTheme()', /id="theme-toggle"[^>]*toggleTheme\(\)/.test(BOARD));
ok('顶栏按钮统一用 .btn 体系（不再有 .org-btn / .api-settings-btn 规则）',
  !/^\s*\.org-btn\s*\{/m.test(BOARD_CSS) && !/^\s*\.api-settings-btn\s*\{/m.test(BOARD_CSS));
ok('openOrgView 指向 /org_chart.html 并带 project 参数',
  /window\.location\.href\s*=\s*'\/org_chart\.html'/.test(BOARD) && /encodeURIComponent\(proj\)/.test(BOARD));
ok('组织视图侧「看板」入口仍在（#btn-board）', /id="btn-board"/.test(ORG));
ok('组织视图侧浮层可关闭（#boardClose → closeBoardModal）',
  /id="boardClose"/.test(ORG) && /boardClose'\)\.onclick\s*=\s*closeBoardModal/.test(ORG));
ok('浮层「← 返回组织」贴在左上（不与看板右上控件重叠）',
  /\.board-close\s*\{[^}]*left:\s*20px/.test(ORG_CSS));
ok('看板 :root 与组织视图 :root 都声明在各自样式表开头',
  BOARD_CSS.indexOf(':root {') < BOARD_CSS.indexOf('.topbar') &&
  ORG_CSS.indexOf(':root {') < ORG_CSS.indexOf('.topbar'));

/* ---------- 6. 沟通区全屏与 embed ---------- */
section('6. 沟通区全屏 + embed 模式');
ok('沟通区有 ⛶ 全屏按钮并绑定 toggleChatFullscreen()',
  /id="chat-fullscreen"[^>]*toggleChatFullscreen\(\)/.test(BOARD));
ok('.chat-area.fullscreen 固定铺满视口',
  /\.chat-area\.fullscreen\s*\{[^}]*position:\s*fixed/.test(BOARD_CSS) &&
  /\.chat-area\.fullscreen\s*\{[^}]*inset:\s*0/.test(BOARD_CSS));
ok('全屏时宽度用 !important 压过拖拽产生的内联宽度',
  /\.chat-area\.fullscreen\s*\{[^}]*width:\s*100%\s*!important/.test(BOARD_CSS));
ok('全屏时消息列居中限宽', /\.chat-area\.fullscreen \.chat-messages\s*\{[^}]*max-width/.test(BOARD_CSS));
ok('Esc 退出全屏已绑定', /addEventListener\('keydown'[\s\S]{0,200}Escape/.test(BOARD));
ok('embed 模式给 body 打标记', /classList\.add\('embed'\)/.test(BOARD));
ok('embed 模式空出顶栏左侧（留给父页面「← 返回组织」）',
  /body\.embed \.topbar-left\s*\{[^}]*visibility:\s*hidden/.test(BOARD_CSS));
ok('embed 模式仍隐藏 #org-toggle / #home-toggle',
  /embed[\s\S]{0,400}getElementById\('org-toggle'\)/.test(BOARD) &&
  /embed[\s\S]{0,400}getElementById\('home-toggle'\)/.test(BOARD));

/* ---------- 结果 ---------- */
console.log('\n' + '='.repeat(60));
console.log(pass + ' 通过 / ' + fail + ' 失败');
if (fail === 0) console.log('全部通过 ✓');
process.exit(fail === 0 ? 0 : 1);
