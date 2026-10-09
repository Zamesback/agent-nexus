#!/usr/bin/env node
/**
 * T8 回归测试：右下角审批浮层的倒计时文案（v2.20.12）
 *
 * 背景
 * ----
 * 浮层的倒计时过去直接用轮询回来的 `remainingMs`。v2.20.12 之前服务端把它算大了
 * 1000 倍（毫秒当秒），于是用户在任意页面右下角看到的是
 *
 *     约 1999 分 25 秒后自动拒绝（安全默认）
 *
 * 而同一条请求在成员卡的审批卡片里显示的是正确的「剩余 1 分 41 秒」——两个数字
 * 来自同一次审批，普通用户只会觉得工具坏了。
 *
 * `remainingText()` 按一条不变量收口：剩余时间不可能超过配置的时限（timeoutMs）。
 * 服务端已在 v2.20.12 修掉根因，这里锁住显示侧不再被任何单位错误放大。
 *
 * 用法：node tests/t8_approval_toast_test.js
 */

'use strict';

var fs = require('fs');
var path = require('path');
var vm = require('vm');

var FILE = path.join(__dirname, '..', 'approval_alert.js');

var PASS = 0;
var FAIL = 0;

function check(name, got, want) {
  if (got === want) {
    PASS++;
    console.log('  [PASS] ' + name);
  } else {
    FAIL++;
    console.log('  [FAIL] ' + name + '\n         got  = ' + JSON.stringify(got) +
                '\n         want = ' + JSON.stringify(want));
  }
}

// 用一个最小沙箱加载脚本。document.readyState 保持 'loading'，脚本会把 boot()
// 挂到 DOMContentLoaded 上而不真的执行，所以不会发出任何网络请求。
var sandbox = {
  console: console,
  setInterval: function () { return 0; },
  clearInterval: function () {},
  setTimeout: function () { return 0; },
  URLSearchParams: URLSearchParams,
  localStorage: { getItem: function () { return null; }, setItem: function () {} },
  fetch: function () { return Promise.reject(new Error('no network in tests')); },
  Notification: undefined
};
sandbox.window = {
  addEventListener: function () {},
  location: { search: '' }
};
sandbox.document = {
  readyState: 'loading',
  addEventListener: function () {},
  getElementById: function () { return null; },
  createElement: function () { return { style: {} }; },
  head: { appendChild: function () {} },
  body: { appendChild: function () {} },
  title: 'AgentNexus'
};

vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(FILE, 'utf8'), sandbox, { filename: FILE });

var fmt = sandbox.window.__agentnexusRemainingText;
if (typeof fmt !== 'function') {
  console.log('  [FAIL] 脚本没有导出 __agentnexusRemainingText');
  process.exit(1);
}

console.log('=== 不变量：剩余时间不可能超过时限 ===');
check('服务端算成 1000 倍时收口到时限（120000000ms → 2 分）',
      fmt({ timeoutMs: 120000, remainingMs: 120000000 }), '2 分 0 秒');
check('生产上真实出现的 119965000ms 也收口',
      fmt({ timeoutMs: 120000, remainingMs: 119965000 }), '2 分 0 秒');
check('修复后的正常值原样显示',
      fmt({ timeoutMs: 120000, remainingMs: 119000 }), '1 分 59 秒');

console.log('=== 常规显示 ===');
check('刚好 2 分钟', fmt({ timeoutMs: 120000, remainingMs: 120000 }), '2 分 0 秒');
check('整数分钟', fmt({ timeoutMs: 120000, remainingMs: 60000 }), '1 分 0 秒');
check('不足 1 分钟只报秒', fmt({ timeoutMs: 120000, remainingMs: 59000 }), '59 秒');
check('61 秒', fmt({ timeoutMs: 120000, remainingMs: 61000 }), '1 分 1 秒');

console.log('=== 边界 ===');
check('剩余 0', fmt({ timeoutMs: 120000, remainingMs: 0 }), '0 秒');
check('负数夹到 0', fmt({ timeoutMs: 120000, remainingMs: -5000 }), '0 秒');
check('老桥没有 timeoutMs 时不收口（45 秒）',
      fmt({ remainingMs: 45000 }), '45 秒');
check('完全空对象', fmt({}), '0 秒');
check('字段是字符串也能读',
      fmt({ timeoutMs: '120000', remainingMs: '119000' }), '1 分 59 秒');
check('timeoutMs 为 0 时不做上界收口',
      fmt({ timeoutMs: 0, remainingMs: 200000 }), '3 分 20 秒');

console.log('\n' + '='.repeat(60));
console.log('结果：' + PASS + ' 通过 / ' + FAIL + ' 失败');
if (FAIL) {
  console.log('存在失败用例 ✗');
  process.exit(1);
}
console.log('全部通过 ✓');
