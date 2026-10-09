#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T7 回归测试：审批倒计时的单位（v2.20.12）

背景
----
审批桥传来的 `timeoutMs` 是毫秒（默认 120000），而 `time.time()` 是秒。
v2.20.6 有两处把两者直接放在一起算，谁都没换算：

  * `_dsh_approval_pending`  —— `remainingMs` 被乘大了 1000 倍
  * `_dsh_approval_state`    —— 本地兜底超时变成「等 33 小时」

第一处是用户看得见的：任意页面右下角的提醒浮层会写成
「约 1999 分 25 秒后自动拒绝（安全默认）」，而成员卡里的卡片同时显示
「剩余 1 分 41 秒」——因为卡片用的是 SSE 里的 timeoutMs 自己算的。
第二处是潜伏的：桥自己超时后会发 expired 事件，服务端这条兜底只在
「桥没报超时」（例如桥崩了）时才有用，而它偏偏永远不触发。

本测试锁住两件事：
  1. remainingMs 的量级必须和 timeoutMs 同单位（毫秒）
  2. 本地兜底必须在 timeoutMs/1000 + grace 秒之后失效，且不误伤

用法：python3 tests/t7_approval_units_test.py
"""

import importlib.util
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location("agentnexus_server_t7", os.path.join(ROOT, "server.py"))
server = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = server
_spec.loader.exec_module(server)

PASS = 0
FAIL = 0

TIMEOUT_MS = 120000.0        # 桥上配的真实值
GRACE = server._DSH_APPROVAL_GRACE


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s\n         got  = %r\n         want = %r" % (name, got, want))


def approx(name, got, want, tol):
    global PASS, FAIL
    if isinstance(got, (int, float)) and abs(got - want) <= tol:
        PASS += 1
        print("  [PASS] %s (%.1f ±%d)" % (name, got, tol))
    else:
        FAIL += 1
        print("  [FAIL] %s\n         got  = %r\n         want = %r ±%s" % (name, got, want, tol))


def reset():
    server._DSH_APPROVALS.clear()


def put(rid, timeout_ms=TIMEOUT_MS, age_s=0.0, **extra):
    """登记一条审批，ts 往回拨 age_s 秒以模拟时间流逝"""
    ent = {
        "project": "三国指挥官", "agent_id": "DSH2", "ts": time.time() - age_s,
        "timeoutMs": timeout_ms, "toolName": "bash", "reason": "escalate sandbox",
        "expired": False, "resolved": False,
    }
    ent.update(extra)
    server._DSH_APPROVALS[rid] = ent
    return ent


def pend(rid):
    """取 _dsh_approval_pending 里某条记录（不存在返回 None）"""
    for it in server._dsh_approval_pending("三国指挥官"):
        if it["requestId"] == rid:
            return it
    return None


def main():
    print("=== remainingMs 的量级（v2.20.6 的 1000 倍 bug）===")
    reset()
    put("r-fresh")
    it = pend("r-fresh")
    approx("刚登记的 120000ms 请求，剩余约 120000ms", it["remainingMs"], TIMEOUT_MS, 2000)
    check("剩余不能是 120000000（乘了 1000 倍）", it["remainingMs"] < 200000, True)
    check("timeoutMs 原样透出", it["timeoutMs"], TIMEOUT_MS)
    check("字段名齐全", sorted(it.keys()),
          ["agentId", "project", "reason", "remainingMs", "requestId", "timeoutMs", "toolName", "ts"])

    reset()
    put("r-30s", age_s=30.0)
    approx("过了 30 秒 → 剩余约 90000ms", pend("r-30s")["remainingMs"], 90000, 2000)

    reset()
    put("r-clamp", age_s=TIMEOUT_MS / 1000.0 + 1.0)   # 已过时限，但还在宽限期内
    check("过时限但仍在宽限期 → 剩余夹到 0（不是负数）", pend("r-clamp")["remainingMs"], 0)

    reset()
    put("r-old", age_s=TIMEOUT_MS / 1000.0 + GRACE + 5)
    check("彻底超时 → 整个请求不再列出", pend("r-old"), None)

    print("=== 没有 timeoutMs 的老桥（不误杀）===")
    reset()
    put("r-notmo", timeout_ms=0)
    check("老桥没有 timeoutMs → remainingMs 为 0", pend("r-notmo")["remainingMs"], 0)
    check("老桥仍然被列出（交给桥自己超时）", pend("r-notmo") is not None, True)

    print("=== 已处理 / 已超时的不再列出 ===")
    reset()
    put("r-resolved", resolved=True)
    check("已应答的不列出", pend("r-resolved"), None)
    reset()
    put("r-expired", expired=True)
    check("已超时的不列出", pend("r-expired"), None)

    print("=== 项目过滤 ===")
    reset()
    put("r-p1")
    server._DSH_APPROVALS["r-p1"]["project"] = "别的项目"
    check("按项目过滤", pend("r-p1"), None)
    check("不带项目时仍能看到", len(server._dsh_approval_pending(None)), 1)

    print("=== 本地兜底的时限（v2.20.6 的 33 小时 bug）===")
    reset()
    put("s-live", age_s=10.0)
    check("120000ms 的请求过了 10 秒 → 仍可应答", server._dsh_approval_state("s-live")[0], True)
    reset()
    put("s-over", age_s=TIMEOUT_MS / 1000.0 + GRACE + 5)
    live, why, _ = server._dsh_approval_state("s-over")
    check("超过时限 → 本地兜底判失效", live, False)
    check("失效理由说明是超时", "超时" in why, True)
    check("失效后条目被标记 expired", server._DSH_APPROVALS["s-over"]["expired"], True)

    reset()
    put("s-g-in", timeout_ms=2000.0, age_s=2.0 + GRACE - 0.5)
    check("宽限期内仍可应答", server._dsh_approval_state("s-g-in")[0], True)
    reset()
    put("s-g-out", timeout_ms=2000.0, age_s=2.0 + GRACE + 0.5)
    check("刚过宽限期即失效", server._dsh_approval_state("s-g-out")[0], False)

    reset()
    put("s-notmo", timeout_ms=0, age_s=99999.0)
    check("无 timeoutMs 的老桥不因时间被判失效", server._dsh_approval_state("s-notmo")[0], True)

    print("=== 已处理过的不能再应答 ===")
    reset()
    put("s-done", resolved=True)
    live, why, _ = server._dsh_approval_state("s-done")
    check("resolved → 不可应答", live, False)
    check("理由说明已处理过", "处理过" in why, True)

    reset()
    print("\n" + "=" * 60)
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    if FAIL:
        print("存在失败用例 ✗")
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
