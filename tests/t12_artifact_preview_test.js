#!/usr/bin/env node
/**
 * T12 回归测试：交付物「点得开」（v2.20.16）
 *
 * 背景
 * ----
 * 用户点成员卡里的「交付物」条目时**看不到任何东西**。真因不是接口坏了
 * （`curl /api/dsh/artifact?...` 是 200），而是 `#artPreview` 的 z-index 是 90，
 * 而人卡浮层 `.modal-mask` 是 100 —— 预览整个被画在人卡底下，只有高出卡片
 * 顶边的那条标题栏露出来，看着就像「点了没反应」。
 *
 * 同一轮还补了三件事：
 *  1. Agent 也会把文件写到 /tmp 或自己的家目录，那些路径登记得到、但预览接口
 *     按白名单拒绝（403）。接口现在每条返回 `previewable` / `reason`，前端把它
 *     渲染成「不可预览 + 原因」，而不是弹一个空白预览或把错误 JSON 当正文。
 *  2. 可预览的行加一个右侧 `↗`，直接在新标签页打开原始文件。
 *  3. 文本/代码分支先判 `response.ok`，403 不再被当作文件内容。
 *
 * 这些大多是「层叠/接线」类问题，没有 DOM 就测不到行为，所以本测试盯住**结构契约**：
 * 一旦有人把 z-index 调回去、或把 previewable 分支删掉，这里立刻红。
 *
 * 跑法：node tests/t12_artifact_preview_test.js
 */
const fs = require('fs');
const path = require('path');
const root = path.join(__dirname, '..');
const src = fs.readFileSync(path.join(root, 'org_chart.html'), 'utf8');
const srv = fs.readFileSync(path.join(root, 'server.py'), 'utf8');

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (extra ? '\n      → ' + extra : '')); }
}
// 取某个 CSS 规则体里某个数值属性
function cssNum(selector, prop) {
  const i = src.indexOf(selector);
  if (i < 0) return null;
  const body = src.slice(i, src.indexOf('}', i));
  const m = body.match(new RegExp(prop.replace('-', '\\-') + ':\\s*([0-9]+)'));
  return m ? Number(m[1]) : null;
}
function between(startMark, endMark, hay) {
  const s = hay || src;
  const i = s.indexOf(startMark);
  if (i < 0) return '';
  const j = s.indexOf(endMark, i);
  return j < 0 ? '' : s.slice(i, j);
}

console.log('== 1. 层叠顺序（「点不开」的真因）==');
{
  const pv = cssNum('#artPreview', 'z-index');
  const mask = cssNum('.modal-mask {', 'z-index');
  ok('#artPreview 有 z-index', pv !== null, 'got ' + pv);
  ok('.modal-mask 有 z-index', mask !== null, 'got ' + mask);
  ok('预览高于人卡浮层', pv > mask, 'artPreview=' + pv + ' modal-mask=' + mask);
  ok('预览高于讨论室', pv > (cssNum('.discuss-room {', 'z-index') || 0));
  ok('是人卡浮层的老值 100 导致的（回归说明）', mask === 100);
}

console.log('\n== 2. 列表行按 previewable 分支 ==');
{
  const load = between('async function loadArtifacts()', 'function _artItems()');
  ok('loadArtifacts 存在', load.length > 0);
  ok('行模板按 a.previewable 决定类名', /art-item \$\{a\.previewable \? '' : 'art-gone'\}/.test(load), load.slice(0, 600));
  ok('可预览显示大小', /a\.previewable \? fmtBytes\(a\.size\)/.test(load));
  ok('不可预览显示「不可预览」', load.includes("'不可预览'"));
  ok('已不在显示「已不在」', load.includes("'已不在'"));
  ok('可预览行带 ↗ 按钮', /class="art-open"[^>]*>↗<\/span>/.test(load));
  ok('↗ 不冒泡触发预览', load.includes('event.stopPropagation()'));
  ok('↗ 调 openArtifactRawIdx', /openArtifactRawIdx\(\$\{i\}\)/.test(load));
  ok('不可预览的行不再显示为可点（art-gone）', /\.art-item\.art-gone\s*\{[^}]*cursor:\s*default/.test(src));
}

console.log('\n== 3. 点开后的三种结果 ==');
{
  const fn = between('function openArtifactPreview(idx)', 'function closeArtPreview()');
  ok('openArtifactPreview 存在', fn.length > 0);
  ok('先判文件是否还在', /if \(!a\.exists\)/.test(fn));
  ok('再判 previewable === false', /if \(a\.previewable === false\)/.test(fn));
  ok('解释文案含服务端给的 reason', /esc\(a\.reason \|\| /.test(fn));
  ok('空预览不再是唯一结局（有 fallback 分支）', (fn.match(/art-pv-fallback/g) || []).length >= 2);
}

console.log('\n== 4. 新标签页直开 ==');
{
  const f = between('function openArtifactRawIdx(idx)', 'function ');
  ok('openArtifactRawIdx 存在', src.includes('function openArtifactRawIdx(idx)'));
  ok('只对可预览的项开新页', /if \(a && a\.previewable\) window\.open\(/.test(src));
  ok('已有的 openArtifactRaw() 仍在（预览浮层里的「新标签页」）', src.includes('function openArtifactRaw()'));
}

console.log('\n== 5. HTTP 错误不再当正文 ==');
{
  ok('文本分支判 response.ok', /if \(!r\.ok\) throw new Error\('HTTP ' \+ r\.status/.test(src));
  ok('403 有专门说明', src.includes('不在允许预览的目录内'));
}

console.log('\n== 6. 服务端 previewable / reason ==');
{
  ok('列表项带 previewable', /item\["previewable"\]\s*=/.test(srv));
  ok('列表项带 reason', /item\["reason"\]\s*=/.test(srv));
  ok('previewable 同时要求 in_root 与 exists', /bool\(in_root and item\["exists"\]\)/.test(srv));
  ok('没有在列表里反复调 _dsh_artifact_allowed（避免 300 次重读登记表）',
     !/_dsh_artifact_allowed\(project, aid, p\)/.test(srv));
  ok('白名单三个根仍在', /def _dsh_artifact_roots/.test(srv) && /workspace/.test(between('def _dsh_artifact_roots', 'def _dsh_artifact_allowed', srv)));
  ok('预览接口仍走白名单校验', /_dsh_artifact_allowed\(project, agent_id, path\)/.test(srv) || /_dsh_artifact_allowed\(/.test(between('def _handle_dsh_artifact', '\ndef ', srv)));
}

console.log('\n' + '='.repeat(60));
console.log(pass + ' 通过 / ' + fail + ' 失败');
if (fail) process.exit(1);
console.log('全部通过 ✓');
