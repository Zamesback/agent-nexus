/**
 * AgentNexus 待批准提醒（v2.20.6）
 *
 * 解决的问题：Agent 请求权限时，用户可能不在组织架构页——甚至可能已经刷新过页面。
 * 这两种情况下 SSE 卡片都不存在，用户看不到、也听不到，Agent 只能等到超时被拒。
 *
 * 这个脚本可以放进任意页面：它轮询 /api/dsh/approvals，只要还有待批准的请求就
 *   1. 响铃（WebAudio 合成，无需音频文件），未处理则每 8 秒再响一次
 *   2. 发桌面通知（用户已授权时）
 *   3. 页面标题加「⚠️ 待批准 N」前缀
 *   4. 右下角浮出提醒条，直接带「批准 / 拒绝」按钮——不必跳页也能处理
 *
 * 去重：若页面已提供 renderApproval（组织架构页的 SSE 卡片），就把渲染交给它，
 * 本脚本不再响铃/弹条，避免同一请求响两次。
 */
(function () {
  'use strict';

  var SOUND_KEY = 'agentnexus_approval_sound';
  var POLL_MS = 3000;      // 轮询间隔
  var REPEAT_MS = 8000;    // 未处理时的重响间隔
  var TOAST_ID = 'agentnexusApprovalToast';

  var audioCtx = null;
  var timers = {};         // requestId -> 重响定时器
  var pending = {};        // requestId -> 最近一次的服务端描述

  function soundEnabled() {
    try { return localStorage.getItem(SOUND_KEY) !== 'off'; } catch (e) { return true; }
  }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function currentProject() {
    if (window.AGENTNEXUS_PROJECT) return String(window.AGENTNEXUS_PROJECT);
    try {
      return new URLSearchParams(window.location.search).get('project') || '';
    } catch (e) { return ''; }
  }

  // 浏览器要求先有用户手势才允许发声
  function unlockAudio() {
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!audioCtx) audioCtx = new AC();
      if (audioCtx.state === 'suspended') audioCtx.resume();
    } catch (e) {}
  }
  ['click', 'keydown', 'pointerdown'].forEach(function (ev) {
    window.addEventListener(ev, function () {
      unlockAudio();
      try {
        if (typeof Notification !== 'undefined' && Notification.permission === 'default') {
          Notification.requestPermission();
        }
      } catch (e) {}
    }, { passive: true });
  });

  function chime() {
    try {
      if (!audioCtx || audioCtx.state !== 'running') return;
      var now = audioCtx.currentTime;
      [[880, 0], [1174.7, 0.16], [1568, 0.32]].forEach(function (p) {
        var osc = audioCtx.createOscillator();
        var g = audioCtx.createGain();
        osc.type = 'sine';
        osc.frequency.value = p[0];
        g.gain.setValueAtTime(0.0001, now + p[1]);
        g.gain.exponentialRampToValueAtTime(0.3, now + p[1] + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, now + p[1] + 0.3);
        osc.connect(g);
        g.connect(audioCtx.destination);
        osc.start(now + p[1]);
        osc.stop(now + p[1] + 0.32);
      });
    } catch (e) {}
  }

  function desktopNotify(title, body) {
    try {
      if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
      var n = new Notification(title, { body: body, tag: 'agentnexus-approval' });
      n.onclick = function () { try { window.focus(); n.close(); } catch (e) {} };
    } catch (e) {}
  }

  function refreshTitle() {
    var n = Object.keys(timers).length;
    document.title = n > 0 ? ('⚠️ 待批准 ' + n + ' · AgentNexus') : 'AgentNexus';
  }

  function startAlert(rid, label) {
    if (timers[rid]) return;
    // 组织架构页的卡片自己会响铃（apvAlerts 是它维护的全局表）——别响两次
    try { if (typeof apvAlerts !== 'undefined' && apvAlerts[rid]) return; } catch (e) {}
    if (soundEnabled()) chime();
    desktopNotify('AgentNexus · 需要你批准', label);
    timers[rid] = setInterval(function () {
      if (soundEnabled()) chime();
    }, REPEAT_MS);
    refreshTitle();
  }

  function stopAlert(rid) {
    if (timers[rid]) { clearInterval(timers[rid]); delete timers[rid]; }
    refreshTitle();
  }

  // ---------- 浮层（非组织架构页用） ----------
  function ensureStyles() {
    if (document.getElementById('agentnexusApprovalStyle')) return;
    var s = document.createElement('style');
    s.id = 'agentnexusApprovalStyle';
    s.textContent =
      '#' + TOAST_ID + '{position:fixed;right:18px;bottom:18px;z-index:99999;max-width:340px;' +
      'font:13px/1.5 -apple-system,"PingFang SC",sans-serif;color:#eee;' +
      'background:#1d1f27;border:1px solid #b8860b;border-left:4px solid #e8b923;' +
      'border-radius:10px;padding:12px 14px;box-shadow:0 8px 28px rgba(0,0,0,.45)}' +
      '#' + TOAST_ID + ' .aa-head{font-weight:600;color:#e8b923;margin-bottom:6px}' +
      '#' + TOAST_ID + ' .aa-body{color:#c8ccd4;margin-bottom:10px;word-break:break-all}' +
      '#' + TOAST_ID + ' button{font:12px inherit;padding:4px 12px;border-radius:6px;' +
      'border:1px solid #3a3f4b;background:#262a34;color:#e6e8ec;cursor:pointer;margin-right:8px}' +
      '#' + TOAST_ID + ' button.aa-allow{background:#1f7a3d;border-color:#2ea34f;color:#fff}' +
      '#' + TOAST_ID + ' button.aa-reject{background:#7a1f24;border-color:#a34f4f;color:#fff}' +
      '#' + TOAST_ID + ' .aa-tip{color:#8b909b;font-size:11px;margin-top:8px}';
    document.head.appendChild(s);
  }

  function renderToast() {
    var ids = Object.keys(pending);
    var el = document.getElementById(TOAST_ID);
    if (!ids.length) {
      if (el) el.remove();
      return;
    }
    ensureStyles();
    if (!el) {
      el = document.createElement('div');
      el.id = TOAST_ID;
      document.body.appendChild(el);
    }
    el.innerHTML = ids.map(function (rid) {
      var p = pending[rid] || {};
      var secs = Math.round((p.remainingMs || 0) / 1000);
      var left = secs >= 60 ? (Math.floor(secs / 60) + ' 分 ' + (secs % 60) + ' 秒') : (secs + ' 秒');
      return '<div class="aa-head">⚠️ 需要你批准 · ' + esc(p.toolName || '工具') + '</div>' +
        '<div class="aa-body">' + esc(p.agentId || 'Agent') + '：' + esc(p.reason || '执行此操作需要权限升级') + '</div>' +
        '<button class="aa-allow" data-rid="' + esc(rid) + '" data-opt="allow">批准</button>' +
        '<button class="aa-reject" data-rid="' + esc(rid) + '" data-opt="reject">拒绝</button>' +
        '<div class="aa-tip">约 ' + left + ' 后自动拒绝（安全默认）</div>';
    }).join('<hr style="border:0;border-top:1px solid #2c313c;margin:10px 0">');
    Array.prototype.slice.call(el.querySelectorAll('button')).forEach(function (b) {
      b.onclick = function () { answer(b.getAttribute('data-rid'), b.getAttribute('data-opt'), b); };
    });
  }

  function answer(rid, option, btn) {
    if (btn) btn.disabled = true;
    var proj = currentProject();
    fetch('/api/dsh/approve' + (proj ? '?project=' + encodeURIComponent(proj) : ''), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ requestId: rid, option: option })
    }).then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (r) {
        if (r && r.ok) {
          stopAlert(rid);
          delete pending[rid];
          renderToast();
        } else if (btn) {
          btn.disabled = false;
          var tip = btn.parentElement && btn.parentElement.querySelector('.aa-tip');
          if (tip) tip.textContent = '⚠️ ' + ((r && r.error) || '应答失败');
        }
      })
      .catch(function () { if (btn) btn.disabled = false; });
  }

  // ---------- 轮询 ----------
  function tick() {
    var proj = currentProject();
    fetch('/api/dsh/approvals' + (proj ? '?project=' + encodeURIComponent(proj) : ''))
      .then(function (r) { return r.json(); })
      .then(function (r) {
        var list = (r && r.approvals) || [];
        var seen = {};
        list.forEach(function (a) {
          var rid = a.requestId;
          if (!rid) return;
          seen[rid] = true;
          var label = (a.toolName || '工具') + ' 请求权限升级';
          var card = document.getElementById('apv-' + rid);
          if (typeof window.renderApproval === 'function' && !card) {
            // 组织架构页：交给它渲染带按钮的卡片（卡片自己会响铃）
            window.renderApproval({
              requestId: rid, toolName: a.toolName, reason: a.reason,
              timeoutMs: a.timeoutMs, remainingMs: a.remainingMs
            });
            // 卡片容器只在成员弹窗打开时存在；没渲染出来就退回浮层，别让提醒丢掉
            if (document.getElementById('apv-' + rid)) return;
          }
          if (card) return;   // 卡片已在，页面自己的逻辑负责提醒
          pending[rid] = a;
          startAlert(rid, label);
        });
        Object.keys(pending).forEach(function (rid) {
          if (!seen[rid]) { stopAlert(rid); delete pending[rid]; }
        });
        renderToast();
      })
      .catch(function () { /* 网络抖动忽略，下一轮再试 */ });
  }

  function boot() {
    tick();
    setInterval(tick, POLL_MS);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
