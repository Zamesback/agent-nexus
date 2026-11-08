# -*- coding: utf-8 -*-
"""T30：v2.20.38 引导词按「能不能被叫醒」分姿态

Zames 的原则：**能被唤醒的就不用轮询，叫不动的必须自己来查。**
  · 托管 dsh —— 工具拉起进程 → 会被叫醒 → 不用轮询
  · API      —— 服务端 POST webhook → 会被叫醒 → 不用轮询（只留兜底）
  · CLI      —— 我们叫不动它的进程 → 必须自己定时查
  · 引导词   —— 外包员工：在它自己的 Agent 里干活，只有公共看板交接 → 必须自己定时看板
                且要允许「纯聊天窗口」这种没联网能力的兜底

跑法：python3 tests/t30_guide_modes_test.py
"""
import os
import shutil
import sys
import tempfile

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


tmp = tempfile.mkdtemp(prefix="t30guide-")
orig = {k: getattr(S, k) for k in (
    "load_board", "save_board", "load_config", "save_config",
    "register_agent", "_dsh_ensure_profile")}
BOARD = {"agents": [], "protocol": {"poll_minutes": 7}}

try:
    S.load_board = lambda p=None: {"agents": [dict(a) for a in BOARD["agents"]],
                                   "protocol": dict(BOARD.get("protocol") or {})}
    S.save_board = lambda d, p=None: None
    S.load_config = lambda p=None: {"agents": []}
    S.save_config = lambda c, p=None: None
    S.register_agent = lambda *a, **k: (True, "ok")
    S._dsh_ensure_profile = lambda *a, **k: None

    print("== A. 轮询间隔可配 ==")
    ok(S._protocol_poll_minutes("demo") == 7, "读 protocol.poll_minutes=7", S._protocol_poll_minutes("demo"))
    BOARD["protocol"] = {"poll_minutes": 0}
    ok(S._protocol_poll_minutes("demo") == 30, "0 → 回默认 30")
    BOARD["protocol"] = {"poll_minutes": 99999}
    ok(S._protocol_poll_minutes("demo") == 30, "超范围 → 回默认 30")
    BOARD["protocol"] = {"poll_minutes": 7}

    print("\n== B. 引导词（外包）：自己定时看板 ==")
    g = S.build_guide("demo", "out1", "外包甲", "文案", mode="guide")
    ok("外包员工" in g, "定性为外包协作")
    ok("没有任何机制会叫醒你" in g, "明确说不会被唤醒")
    ok("自己定时来看" in g and "每 7 分钟" in g, "给出自己轮询的间隔")
    ok("在看板上交付" in g, "交付方式：写回看板")
    ok("纯聊天窗口" in g, "允许没联网能力的兜底")
    ok("/api/board" in g, "给了读取看板的具体接口")

    print("\n== C. CLI：叫不动，所以轮询 ==")
    g_cli = S.build_guide("demo", "cli1", "命令行甲", "开发", mode="cli")
    ok("不会" in g_cli and "推送" in g_cli, "明确说不会推送")
    ok("agentnexus status" in g_cli, "给出 CLI 轮询命令")
    ok("每 7 分钟" in g_cli, "轮询间隔可配")
    ok("收工态" in g_cli and "停止主动轮询" in g_cli, "收工态停轮询")

    print("\n== D. API：会被推送，不用轮询 ==")
    g_api = S.build_guide("demo", "api1", "接口甲", "后端", mode="api")
    ok("你不需要轮询" in g_api, "明确说不用轮询")
    ok("event=message.mention" in g_api, "说清推送是什么样")
    ok("每 7 分钟" not in g_api, "没有要求它自己定时查")
    ok("兜底" in g_api, "保留推送失败的兜底")

    print("\n== E. 托管：会被叫醒，不用轮询 ==")
    g_dsh = S.build_guide("demo", "dsh1", "托管甲", "设计", mode="dsh")
    ok("会被叫醒" in g_dsh, "说清会被叫醒")
    ok("你不需要轮询" in g_dsh, "明确说不用轮询")
    ok("每 7 分钟" not in g_dsh, "没有要求它自己定时查")

    print("\n== F. 四种模式共同的骨架 ==")
    for mode, g in (("guide", g), ("cli", g_cli), ("api", g_api), ("dsh", g_dsh)):
        ok("【你的身份】" in g and "身份 ID" in g, f"{mode}：有身份段")
        ok("agents 数组" in g and "禁止直接编辑或覆盖" in g, f"{mode}：有看板红线")
        ok("working" in g and "off" in g and "收工态" in g, f"{mode}：有工作态/收工态")
        ok("2~5 分钟轮询" not in g, f"{mode}：旧的「2~5 分钟轮询」已去掉")
        ok("30 分钟" not in g, f"{mode}：不再统一写死 30 分钟")

    print("\n== G. invite_agent 各分支产出的引导词 ==")
    ok_, msg, g_api2 = S.invite_agent("demo", "a-api", "接口乙", "后端", "api", "http://x.local/hook")
    ok(ok_ and "你不需要轮询" in g_api2 and "【接口清单 · API 接入】" in g_api2,
       "API 分支：推送姿态 + 接口清单", msg)
    ok_2, _, g_cli2 = S.invite_agent("demo", "a-cli", "命令行乙", "开发", "cli")
    ok(ok_2 and "agentnexus status" in g_cli2 and "你不需要轮询" not in g_cli2,
       "CLI 分支：轮询姿态")
    ok_3, _, g_dsh2 = S.invite_agent("demo", "a-dsh", "托管乙", "设计", "dsh")
    ok(ok_3 and "不需要轮询" in g_dsh2, "托管分支：不用轮询", g_dsh2[-80:] if g_dsh2 else "")
    ok_4, _, g_gd = S.invite_agent("demo", "a-guide", "外包乙", "文案", "guide")
    ok(ok_4 and "外包员工" in g_gd, "引导词分支：外包姿态")

    print("\n== H. 源码里不该再有写死的轮询话术 ==")
    src = open(os.path.join(REPO_ROOT, "server.py"), encoding="utf-8").read()
    ok("建议每 2~5 分钟轮询一次看板" not in src, "删掉了旧的 API 轮询话术")
    ok("工作态下每 30 分钟检查" not in src, "删掉了写死的 30 分钟")
    ok("_guide_mode_block" in src and "_protocol_poll_minutes" in src, "新机制就位")
finally:
    for k, v in orig.items():
        setattr(S, k, v)
    shutil.rmtree(tmp, ignore_errors=True)

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
