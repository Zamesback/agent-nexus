# -*- coding: utf-8 -*-
"""T21：v2.20.27「单轮预算」——到量先收口回报一句，到硬上限强行收口。

起因：2026-10-13 用户问「为什么现在的活会多了4.5倍」。实测那一轮 101 步 / 33.8 分钟
（16 次 edit + 64 次 bash，其中 12 分钟在等上游、别的成员还在排队），用户只看到
「正在工作中 · 第 N 步」。dsh 侧不支持从进程外给正在跑的一轮塞消息（headless 只在启动前
读一次 stdin），但进程内 `agent.steer()` 会在下一个 step 边界被消费 —— 所以走
「服务端写共享提醒文件 → 桥接插件轮询 → agent.steer()」这条路。

跑法：python3 tests/t21_turn_budget_test.py
      node   tests/t21_nudge_bridge_test.js   （桥接侧单测，见 D 段说明）
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS = FAIL = 0


def ck(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % name)
    else:
        FAIL += 1
        print("  ✗ %s  %s" % (name, extra))


def load_server():
    spec = importlib.util.spec_from_file_location("srv_t21", os.path.join(REPO, "server.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ROOT = tempfile.mkdtemp(prefix="t21_")
os.environ["AGENTNEXUS_DSH_LOG"] = os.path.join(ROOT, "dsh.log")
os.environ["COLLAB_PROJECTS_ROOT"] = ROOT
M = load_server()
M.PROJECTS_ROOT = ROOT
M.DSH_LOG_FILE = os.path.join(ROOT, "dsh.log")
M.DSH_NUDGE_FILE = os.path.join(ROOT, "nudges.jsonl")
M._auto_git_snapshot = lambda *a, **k: None
M._auto_git_commit = lambda *a, **k: None
M._dsh_lock_patch = lambda project: os.path.join(ROOT, "lock.yml")
REAL_ENSURE_PROFILE = M._dsh_ensure_profile   # F 段要真跑（桩只给 _dsh_run 用）
M._dsh_ensure_profile = lambda project, agent_id, name, role: None
M._dsh_verify_model = lambda project=None, profile_name=None: (True, "t21")
M._dsh_session_usable = lambda session_id, cwd: True
M.DSH_NODE = sys.executable

PROJ = "T21Proj"
PDIR = os.path.join(ROOT, PROJ)
os.makedirs(PDIR, exist_ok=True)
BOARD = {
    "project_info": {"title": "T21", "workspace": ROOT},
    "agents": [
        {"id": "user", "name": "Zames", "role": "主导方", "entry": None, "entry_type": "manual"},
        {"id": "dsh-a", "name": "甲", "role": "美术设计",
         "entry": {"type": "dsh", "target": "进程托管（本机 dsh）"}, "entry_type": "dsh"},
    ],
    "tasks": [], "messages": [], "decisions": [],
    "protocol": {"mode": "working", "updated_by": "user", "updated_at": "2026-10-13T00:00:00", "exemptions": []},
}
with open(os.path.join(PDIR, "collab_board.json"), "w", encoding="utf-8") as f:
    json.dump(BOARD, f, ensure_ascii=False, indent=2)

SRC = open(os.path.join(REPO, "server.py"), encoding="utf-8").read()
ORG = open(os.path.join(REPO, "org_chart.html"), encoding="utf-8").read()
BRIDGE = os.path.join(REPO, "tools", "approval-bridge", "index.js")
BRIDGE_SRC = open(BRIDGE, encoding="utf-8").read()
LOG_PATH = os.path.join(ROOT, "dsh.log")


def read_log():
    try:
        return open(LOG_PATH, encoding="utf-8", errors="replace").read()
    except Exception:
        return ""


print("A 段 阈值")
ck("A1 默认步数阈值递增 soft<firm<hard",
   M.DSH_TURN_SOFT_STEPS < M.DSH_TURN_FIRM_STEPS < M.DSH_TURN_HARD_STEPS,
   "%s/%s/%s" % (M.DSH_TURN_SOFT_STEPS, M.DSH_TURN_FIRM_STEPS, M.DSH_TURN_HARD_STEPS))
ck("A2 默认时间阈值递增 soft<firm<hard",
   M.DSH_TURN_SOFT_SECONDS < M.DSH_TURN_FIRM_SECONDS < M.DSH_TURN_HARD_SECONDS,
   "%s/%s/%s" % (M.DSH_TURN_SOFT_SECONDS, M.DSH_TURN_FIRM_SECONDS, M.DSH_TURN_HARD_SECONDS))
ck("A3 默认软阈值是 25 步 / 8 分钟",
   (M.DSH_TURN_SOFT_STEPS, M.DSH_TURN_SOFT_SECONDS) == (25, 480),
   "%s/%s" % (M.DSH_TURN_SOFT_STEPS, M.DSH_TURN_SOFT_SECONDS))
ck("A4 提醒是非空的（不要发出空消息）",
   all(isinstance(M.DSH_NUDGE_TEXT.get(k), str) and M.DSH_NUDGE_TEXT[k].strip()
       for k in (1, 2)))
ck("A5 两级提醒文案都能带 {mins}/{steps} 参数",
   all("{mins}" in M.DSH_NUDGE_TEXT[k] and "{steps}" in M.DSH_NUDGE_TEXT[k] for k in (1, 2)))

print("B 段 _dsh_nudge_send")
ck("B1 没有 session_id 时不写文件，返回 False", M._dsh_nudge_send("", 1, "x") is False)
ck("B2 没有 session_id 时留下日志", "预算提醒跳过" in read_log())
ok_ret = M._dsh_nudge_send("sid-b", 1, "提醒正文")
ck("B3 有 session_id 时返回 True", ok_ret is True)
lines = [l for l in open(M.DSH_NUDGE_FILE, encoding="utf-8").read().split("\n") if l.strip()]
ck("B4 恰好写了一行", len(lines) == 1, "lines=%d" % len(lines))
row = json.loads(lines[0])
ck("B5 行形状正确",
   row.get("type") == "agentnexus_nudge" and row.get("sessionId") == "sid-b"
   and row.get("level") == 1 and row.get("text") == "提醒正文"
   and isinstance(row.get("ts"), (int, float)),
   json.dumps(row, ensure_ascii=False))
M._dsh_nudge_send("sid-b", 2, "第二次")
lines2 = [l for l in open(M.DSH_NUDGE_FILE, encoding="utf-8").read().split("\n") if l.strip()]
ck("B6 追加而不是覆盖（桥按偏移读，历史行必须保留）", len(lines2) == 2, "lines=%d" % len(lines2))
ck("B7 第二行 level=2", json.loads(lines2[1]).get("level") == 2)
ck("B8 默认提醒文件路径在 /tmp（三处回退的最后一级）",
   M.DSH_NUDGE_FILE and "/tmp/agentnexus_nudges.jsonl" == os.environ.get(
       "AGENTNEXUS_NUDGE_DEFAULT_FOR_TEST", "/tmp/agentnexus_nudges.jsonl"))
ck("B9 server 默认常量就是 /tmp/agentnexus_nudges.jsonl",
   'os.environ.get("AGENTNEXUS_NUDGE_FILE", "/tmp/agentnexus_nudges.jsonl")' in SRC)

print("C 段 源码契约（服务端 / 桥接 / 前端）")
ck("C1 watchdog 里有两级提醒", SRC.count("单轮预算提醒 L") >= 1 and "for _lvl" in SRC)
ck("C2 硬上限分支存在并带日志", "单轮预算到硬上限" in SRC)
ck("C3 发出 budget / budget_stop 事件",
   '"type": "budget"' in SRC and '"type": "budget_stop"' in SRC)
ck("C4 步数由 status.step_start 计数",
   'ev.get("phase") == "step_start"' in SRC and "_steps[0] = max(_steps[0]" in SRC)
ck("C5 收尾日志按原因分派（budget/watchdog 不再混写）",
   'dsh 因单轮预算硬上限终止' in SRC and '_wd_reason[0] = "watchdog"' in SRC
   and '_wd_reason[0] = "final"' in SRC)
ck("C6 身份锚定里有【每轮预算】", "【每轮预算】" in SRC)
ck("C7 nudge 文件可按环境变量覆盖（测试/多实例用）",
   'AGENTNEXUS_NUDGE_FILE' in SRC and 'AGENTNEXUS_NUDGE_FILE' in BRIDGE_SRC)
ck("C8 桥接声明 inject=[agents]（cordis 不做隐式注入）",
   re.search(r"module\.exports\.inject\s*=\s*\['agents'\]", BRIDGE_SRC) is not None)
ck("C9 桥接用 agent.steer 投递，且没有 steer 时退回 followup",
   "agent.steer(message)" in BRIDGE_SRC and "agent.followup(message)" in BRIDGE_SRC)
ck("C10 桥接按 sessionId 认领，认不到就回报失败",
   "sid) === want" in BRIDGE_SRC and "agentnexus_nudge_failed" in BRIDGE_SRC)
ck("C11 桥接投递成功回报 agentnexus_nudge_delivered",
   "agentnexus_nudge_delivered" in BRIDGE_SRC)
ck("C12 桥接的提醒通道整体包在 try/catch 里（失败不能弄死一轮）",
   "function deliverNudge(ctx, msg)" in BRIDGE_SRC
   and BRIDGE_SRC.count("catch (_e)") >= 5)
ck("C13 成员 profile 升级会补 inject + nudgeFile",
   "成员 profile 审批桥段升级" in SRC and 'inject: [agents]' in SRC
   and "nudgeFile" in SRC)
ck("C14 前端有 budget / budget_stop 两个分支",
   "t === 'budget'" in ORG and "t === 'budget_stop'" in ORG)
ck("C15 前端文案说清「已请它先回报一句」", "已请它先回报一句" in ORG)
ck("C16 前端到硬上限的文案是收口", "本轮已收口" in ORG)

print("D 段 桥接插件单测（node，假 ctx + 假 agent）")
NODE = None
for cand in ("/Users/zames/Library/Application Support/DoubaoWork/sandbox_runtime/bases/"
             "98670218a5f0d8bc9b9ebf3f70881304/bin/node", "/usr/local/bin/node", "/opt/homebrew/bin/node"):
    if os.path.exists(cand):
        NODE = cand
        break
    try:
        if subprocess.run(["node", "--version"], capture_output=True).returncode == 0:
            NODE = "node"
            break
    except Exception:
        pass
if NODE:
    r = subprocess.run([NODE, os.path.join(REPO, "tests", "t21_nudge_bridge_test.js")],
                       capture_output=True, text=True, timeout=120)
    ck("D1 tests/t21_nudge_bridge_test.js 全绿", r.returncode == 0,
       (r.stdout or "")[-600:] + (r.stderr or "")[-300:])
else:
    ck("D1 找到 node 可执行文件", False, "node not found")

print("E 段 端到端：小阈值 + 假 CLI 连发 step_start（进程内真跑 _dsh_run）")
FAKE = os.path.join(ROOT, "fake_dsh.py")
open(FAKE, "w", encoding="utf-8").write('''# -*- coding: utf-8 -*-
import json, sys, time
def out(o):
    sys.stdout.write(json.dumps(o, ensure_ascii=False) + "\\n")
    sys.stdout.flush()
out({"type": "session", "sessionId": "sid-budget", "cwd": sys.argv[0].rsplit("/", 1)[0]})
step = 0
t0 = time.time()
while time.time() - t0 < 60:
    step += 1
    out({"type": "status", "phase": "step_start", "turn": 1, "step": step})
    time.sleep(0.2)
out({"type": "final", "text": "never reached in this test"})
''')
M.DSH_CLI = FAKE
M.DSH_TURN_SOFT_STEPS, M.DSH_TURN_SOFT_SECONDS = 10, 3600
M.DSH_TURN_FIRM_STEPS, M.DSH_TURN_FIRM_SECONDS = 40, 3600
M.DSH_TURN_HARD_STEPS, M.DSH_TURN_HARD_SECONDS = 45, 3600
open(M.DSH_NUDGE_FILE, "w", encoding="utf-8").close()

events = []
t0 = time.time()
ok, sid, usage = M._dsh_run(PROJ, "dsh-a", "跑一轮很长很长的活", "sid-budget",
                            lambda ev: events.append(ev), kill_after_final=False)
elapsed = time.time() - t0
ck("E1 _dsh_run 因硬上限被终止后仍算成功返回", ok is True, "ok=%s" % ok)
ck("E2 没有白等：5~25 秒内就收口（watchdog 5s 一查）", elapsed < 40, "elapsed=%.1fs" % elapsed)
rows = [json.loads(l) for l in open(M.DSH_NUDGE_FILE, encoding="utf-8").read().split("\n") if l.strip()]
ck("E3 提醒文件里 L1 与 L2 各一条", [r.get("level") for r in rows] == [1, 2],
   json.dumps([r.get("level") for r in rows]))
ck("E4 提醒都带真实 sessionId", all(r.get("sessionId") == "sid-budget" for r in rows),
   json.dumps(rows, ensure_ascii=False)[:300])
# steps 是 SSE 事件里的字段（提醒文件只带 text，够桥接用）：用事件断言两级推进
bud = [e for e in events if e.get("type") == "budget"]
stop = [e for e in events if e.get("type") == "budget_stop"]
bud_steps = [b.get("steps", 0) for b in bud]
ck("E5 L1 的 steps 落在 SOFT 与 FIRM 之间（说明两级是分开发的）",
   M.DSH_TURN_SOFT_STEPS <= bud_steps[0] < M.DSH_TURN_FIRM_STEPS,
   "steps=%s" % bud_steps)
ck("E6 L2 的 steps >= FIRM 阈值", bud_steps[1] >= M.DSH_TURN_FIRM_STEPS, "steps=%s" % bud_steps)
ck("E7 两级提醒之间真的有进展（不是同一 tick 连发）",
   bud_steps[1] > bud_steps[0], "%s -> %s" % (bud_steps[0], bud_steps[1]))
ck("E7b 提醒文件的行只带桥接要用的三个字段（sessionId/level/text）",
   all(set(r.keys()) >= {"type", "sessionId", "level", "text"} for r in rows)
   and all("steps" not in r for r in rows), json.dumps(rows[:1], ensure_ascii=False))
ck("E8 前端能收到 budget 事件（两级）", [b.get("level") for b in bud] == [1, 2],
   json.dumps(bud, ensure_ascii=False)[:300])
ck("E9 前端能收到 budget_stop", len(stop) == 1, json.dumps(stop, ensure_ascii=False)[:200])
ck("E10 budget 事件带 已投递 标记（桥没接上时要能看出来）",
   all(b.get("delivered") is True for b in bud), json.dumps(bud, ensure_ascii=False)[:300])
log = read_log()
ck("E11 日志有 L1/L2 与硬上限三行",
   "单轮预算提醒 L1" in log and "单轮预算提醒 L2" in log and "单轮预算到硬上限" in log)
ck("E12 收尾日志说明是预算终止（不是 watchdog 超时）",
   "dsh 因单轮预算硬上限终止" in log and "watchdog 空闲" not in log)
ck("E13 假 CLI 进程已被杀（没有残留）",
   subprocess.run(["bash", "-c", "pgrep -f fake_dsh.py >/dev/null; echo $?"],
                  capture_output=True, text=True).stdout.strip() == "1")

print("F 段 启动时 profile 升级（inject + nudgeFile）")
PROF_ROOT = os.path.join(ROOT, "profiles")
os.makedirs(PROF_ROOT, exist_ok=True)
M._dsh_member_profile_dir = lambda p, a: os.path.join(PROF_ROOT, "dsh-%s-%s" % (p, a))
# 真实成员 profile 里有身份段标记 —— 有它才不会触发「身份段升级，整体重建」。
BRIDGE_ROW = ("# " + "【你的身份 · 每次开口前先确认】" + "\n"
              "- insert:\n"
              "    # AgentNexus 审批桥：headless 权限批准闭环\n"
              "    - id: agentnexus-approval-bridge\n"
              "      name: '@deepseek-ai/dsh-agentnexus-approval-bridge'\n"
              "      config:\n"
              "        timeoutMs: 120000\n"
              '        answerFile: "/tmp/agentnexus_answers.jsonl"\n')
M._dsh_ensure_profile = REAL_ENSURE_PROFILE   # F 段验证的是真实升级逻辑，不能用桩
d_old = os.path.join(PROF_ROOT, "dsh-%s-%s" % (PROJ, "dsh-a"))
os.makedirs(d_old, exist_ok=True)
p_old = os.path.join(d_old, "cordis.patch.yml")
open(p_old, "w", encoding="utf-8").write(BRIDGE_ROW)
M._dsh_migrate_all_profiles()
body = open(p_old, encoding="utf-8").read()
ck("F1 老 profile 被补上 inject: [agents]", "inject: [agents]" in body, body[:200])
ck("F2 老 profile 被补上 nudgeFile",
   'nudgeFile: "/tmp/agentnexus_nudges.jsonl"' in body, body[-200:])
ck("F3 nudgeFile 落在 config 段里（缩进 8 空格）",
   '\n        nudgeFile: "/tmp/agentnexus_nudges.jsonl"' in body)
ck("F3b inject 落在 name 行之后、缩进 6 空格",
   "      name: '@deepseek-ai/dsh-agentnexus-approval-bridge'\n      inject: [agents]\n" in body)
ck("F3c 身份段没有被误重建（文件里没有模板占位符）", "{PROJECT}" not in body and "{NAME}" not in body)
snapshot = body
M._dsh_migrate_all_profiles()
ck("F4 幂等：再跑一次内容不变", open(p_old, encoding="utf-8").read() == snapshot)
ck("F5 升级有日志", "托管成员 profile" in read_log())
p_nb = os.path.join(PROF_ROOT, "dsh-NoBridgeProj-dsh-b")
os.makedirs(p_nb, exist_ok=True)
nb_path = os.path.join(p_nb, "cordis.patch.yml")
open(nb_path, "w", encoding="utf-8").write("# 没有审批桥段的 profile\n")
nb_before = open(nb_path, encoding="utf-8").read()
M._dsh_migrate_all_profiles()
ck("F6 没有审批桥段的 profile 不被乱改",
   open(nb_path, encoding="utf-8").read() == nb_before)
ck("F7 main() 里真的调了它（启动即升级，不等成员下一次开口）",
   "        _dsh_migrate_all_profiles()" in SRC)
ck("F8 升级只认托管成员（_dsh_is_terminal_member）",
   "if not isinstance(a, dict) or not _dsh_is_terminal_member(proj, a):" in SRC)

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
