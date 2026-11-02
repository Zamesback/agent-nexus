# -*- coding: utf-8 -*-
"""T24（Python 侧）：v2.20.32 叫停的决策逻辑

JS 那边验的是接线与按钮；这里用假进程对象跑真实的 _dsh_stop_turn：
  · 活着 → 杀进程组、登记 STOP_REQ
  · 已经退出 → 不杀、如实说「刚刚已经结束」
  · 没有这一轮 → 不杀，说明
  · 杀不动 → 撤回 STOP_REQ，别让下一轮被误标成「叫停」
跑法：python3 tests/t24_stop_units_test.py
"""
import os
import signal
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BASE_DIR)
for _p in (REPO_ROOT, BASE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import server as S

PASS = FAIL = 0


def ok(cond, label, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ " + label)
    else:
        FAIL += 1
        print("  ✗ " + label + (("  → " + str(extra)) if extra else ""))


class FakeProc:
    def __init__(self, alive=True):
        self.pid = 4242
        self._alive = alive
        self.killed = False

    def poll(self):
        return None if self._alive else 0

    def kill(self):
        self.killed = True


orig_killpg = S.os.killpg
calls = []


def fake_killpg(pid, sig):
    calls.append((pid, sig))


try:
    S.os.killpg = fake_killpg

    print("== A. 正常叫停 ==")
    S._DSH_PROCS.clear()
    S._DSH_STOP_REQ.clear()
    S._DSH_STOPPED.clear()
    proc = FakeProc(alive=True)
    S._DSH_PROCS[("proj", "A")] = {"proc": proc, "pid": 4242, "since": "10:00", "label": "对话"}
    okk, info = S._dsh_stop_turn("proj", "A")
    ok(okk is True, "返回 ok", info)
    ok(calls and calls[-1] == (4242, signal.SIGKILL), "对进程组发 SIGKILL", calls[-1:])
    ok(("proj", "A") in S._DSH_STOP_REQ, "登记 STOP_REQ（退出时认领）")
    ok(info.get("pid") == 4242 and info.get("since") == "10:00", "回报 pid / 开始时间", info)
    ok(proc.killed is True, "同时兜底 proc.kill()")

    print("\n== B. 已经结束的一轮 ==")
    S._DSH_PROCS.clear(); S._DSH_STOP_REQ.clear()
    calls.clear()
    dead = FakeProc(alive=False)
    S._DSH_PROCS[("proj", "A")] = {"proc": dead, "pid": 4242, "since": "10:00", "label": "对话"}
    okk, why = S._dsh_stop_turn("proj", "A")
    ok(okk is False and "已经结束" in str(why), "如实说已经结束", why)
    ok(not calls, "没有误杀")
    ok(("proj", "A") not in S._DSH_PROCS, "顺手清掉过期句柄")

    print("\n== C. 没有在跑的一轮 ==")
    S._DSH_PROCS.clear()
    okk, why = S._dsh_stop_turn("proj", "nobody")
    ok(okk is False and "没有在跑" in str(why), "如实说没有在跑", why)

    print("\n== D. 杀不动（进程已经没了）==")
    def boom(pid, sig):
        raise ProcessLookupError("gone")
    S.os.killpg = boom
    S._DSH_PROCS.clear(); S._DSH_STOP_REQ.clear()
    p2 = FakeProc(alive=True)

    class NoKill(FakeProc):
        def kill(self):
            raise ProcessLookupError("gone too")

    p2 = NoKill(alive=True)
    S._DSH_PROCS[("proj", "A")] = {"proc": p2, "pid": 4242, "since": "10:00", "label": ""}
    okk, why = S._dsh_stop_turn("proj", "A")
    ok(okk is False, "杀不动时返回 False", why)
    ok(("proj", "A") not in S._DSH_STOP_REQ, "撤回 STOP_REQ（不能让下一轮被误标成叫停）")

    print("\n== E. 服务端常量 ==")
    src = open(os.path.join(REPO_ROOT, "server.py"), encoding="utf-8").read()
    ok("_DSH_STOPPED.add((project, agent_id))" in src, "有 '被叫停' 标记集合")
    ok("if (project, agent_id) in _DSH_STOP_REQ:" in src, "退出路径会认领叫停")
finally:
    S.os.killpg = orig_killpg
    S._DSH_PROCS.clear()
    S._DSH_STOP_REQ.clear()
    S._DSH_STOPPED.clear()

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
