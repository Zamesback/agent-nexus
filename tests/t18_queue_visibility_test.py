# -*- coding: utf-8 -*-
"""T18：v2.20.24「等锁不再静默」——运行登记表 + 排队状态事件 + 前端秒表。

起因（2026-10-11 用户报障）：布置完任务后卡片长时间没动静，关掉卡片再点进来才看到结果。
根因：同一成员的几轮由 _DSH_LOCKS 串行化，等锁那一轮在拿到锁之前一个字节都不发给客户端
（日志实测最长静默 410 秒）。修法：发完 SSE 头立刻发 accepted；探测到该成员正在跑就发
queued（带 running_label / running_elapsed）；拿到锁发 dequeued（带真实 waited）；
模块级 _DSH_RUNNING 登记谁在跑，并开 GET /api/dsh/running。

跑法：python3 tests/t18_queue_visibility_test.py
"""
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import tempfile
import threading
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
    spec = importlib.util.spec_from_file_location("srv_t18", os.path.join(REPO, "server.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ROOT = tempfile.mkdtemp(prefix="t18_")
# 必须在加载 server.py 之前指好日志与数据根，免得污染真实的 /tmp/dsh_server.log 与数据仓库
os.environ["AGENTNEXUS_DSH_LOG"] = os.path.join(ROOT, "dsh.log")
os.environ["COLLAB_PROJECTS_ROOT"] = ROOT
M = load_server()
M.PROJECTS_ROOT = ROOT
M.DSH_LOG_FILE = os.path.join(ROOT, "dsh.log")
M._auto_git_snapshot = lambda *a, **k: None
M._auto_git_commit = lambda *a, **k: None
M._dsh_lock_patch = lambda project: os.path.join(ROOT, "lock.yml")
M._dsh_ensure_profile = lambda project, agent_id, name, role: None
M._dsh_verify_model = lambda project=None, profile_name=None: (True, "t18")
M.DSH_NODE = sys.executable
M.DSH_CLI = os.path.join(ROOT, "fake.py")

PROJ = "T18Proj"
PDIR = os.path.join(ROOT, PROJ)
os.makedirs(PDIR, exist_ok=True)
BOARD = {
    "project_info": {"title": "T18", "workspace": ROOT},
    "agents": [
        {"id": "user", "name": "Zames", "role": "主导方", "entry": None, "entry_type": "manual"},
        {"id": "dsh-a", "name": "甲", "role": "美术设计",
         "entry": {"type": "dsh", "target": "进程托管（本机 dsh）"}, "entry_type": "dsh"},
    ],
    "tasks": [], "messages": [], "decisions": [],
    "protocol": {"mode": "working", "updated_by": "user", "updated_at": "2026-10-12T00:00:00", "exemptions": []},
}
with open(os.path.join(PDIR, "collab_board.json"), "w", encoding="utf-8") as f:
    json.dump(BOARD, f, ensure_ascii=False, indent=2)
with open(os.path.join(PDIR, "project_config.json"), "w", encoding="utf-8") as f:
    json.dump({"dsh": {"model": "space-bunny-free", "members": {}, "auto_wake": True}}, f, ensure_ascii=False)

SRC = open(os.path.join(REPO, "server.py"), encoding="utf-8").read()
ORG = open(os.path.join(REPO, "org_chart.html"), encoding="utf-8").read()


# ---------------------------------------------------------------- A 运行登记表
print("A 段 _dsh_running_* 语义")
M._dsh_running_clear(PROJ, "dsh-a")
ck("A1 没跑过时 info 返回 None", M._dsh_running_info(PROJ, "dsh-a") is None)
M._dsh_running_set(PROJ, "dsh-a", "对话")
info = M._dsh_running_info(PROJ, "dsh-a")
ck("A2 set 之后能查到", isinstance(info, dict) and info.get("label") == "对话", str(info))
ck("A3 带 started/started_str/elapsed", (info or {}).get("started", 0) > 0
   and bool((info or {}).get("started_str")) and (info or {}).get("elapsed", -1) >= 0, str(info))
ck("A4 info 返回的是副本（改它不影响登记表）",
   (lambda d: (d.update({"label": "改坏了"}), M._dsh_running_info(PROJ, "dsh-a").get("label") == "对话")[1])(info))
M._dsh_running_set("OtherProj", "dsh-b", "自动唤醒")
lst = M._dsh_running_list()
ck("A5 list() 含两个项目的成员", len(lst) == 2 and {x["agentId"] for x in lst} == {"dsh-a", "dsh-b"}, str(lst))
ck("A6 list(project) 只返回本项目", [x["agentId"] for x in M._dsh_running_list(PROJ)] == ["dsh-a"])
ck("A7 列表元素带 project 字段", all("project" in x for x in lst), str(lst[:1]))
M._dsh_running_clear(PROJ, "dsh-a")
ck("A8 clear 之后查不到", M._dsh_running_info(PROJ, "dsh-a") is None)
M._dsh_running_clear("OtherProj", "dsh-b")
ck("A9 全清空后 list() 为空", M._dsh_running_list() == [])
ck("A10 标签表 cover 三条路径",
   M._DSH_RUN_LABELS.get("chat") == "对话" and M._DSH_RUN_LABELS.get("auto_wake") == "自动唤醒"
   and M._DSH_RUN_LABELS.get("discuss") == "讨论室", str(M._DSH_RUN_LABELS))


# ---------------------------------------------------------------- B 源码断言
print("\nB 段 服务端接线（源码断言）")
ck("B1 chat 路径发 accepted 再探测", SRC.index('"phase": "accepted"') < SRC.index("_prev_run = _dsh_running_info"))
ck("B2 queued 带 running_label/running_elapsed/waited",
   '"phase": "queued"' in SRC and '"running_label": _prev_run["label"]' in SRC
   and '"running_elapsed": _prev_run["elapsed"]' in SRC)
ck("B3 dequeued 带真实 waited", '"phase": "dequeued"' in SRC and '"waited": _waited' in SRC)
ck("B4 with lock 之前就已发 accepted",
   SRC.index('"phase": "accepted"') < SRC.index("_queue_t0 = time.time()") < SRC.index("with lock:"))
ck("B5 chat 路径用 try/finally 清登记",
   re.search(r"_dsh_running_set\(project, agent_id, _DSH_RUN_LABELS\.get\(\"chat\"", SRC)
   and re.search(r"finally:\s*\n\s*_dsh_running_clear\(project, agent_id\)", SRC))
ck("B6 自动唤醒登记/清理", "_DSH_RUN_LABELS.get(\"auto_wake\"" in SRC
   and SRC.count("_dsh_running_clear(project, agent_id)") >= 2, "count=%d" % SRC.count("_dsh_running_clear(project, agent_id)"))
ck("B7 讨论室登记/清理", "_DSH_RUN_LABELS.get(\"discuss\"" in SRC
   and SRC.count("_dsh_running_clear(project, mid)") >= 1)
ck("B8 GET /api/dsh/running 路由存在且返回 running",
   '"/api/dsh/running"' in SRC and '"running": _dsh_running_list(project)' in SRC)
ck("B9 SSE 写失败不再静默（每轮流一次日志）",
   "SSE 写入失败（客户端可能已断开）" in SRC and "_write_failed = [False]" in SRC
   and "if not _write_failed[0]:" in SRC)
ck("B10 排队进出都有日志", "进入排队:" in SRC and "排队结束:" in SRC)


# ---------------------------------------------------------------- C 真实两轮并发
print("\nC 段 同成员两轮并发（真实 _handle_dsh_chat，假 _dsh_run）")


def fake_run(project, agent_id, dsh_input, session_id, emit, kill_after_final=False):
    emit({"type": "status", "phase": "step_start", "turn": 1, "step": 1})
    time.sleep(0.6)
    emit({"type": "text", "text": "第 %s 轮跑完了。" % dsh_input[:4]})
    emit({"type": "final", "text": "第 %s 轮跑完了。" % dsh_input[:4]})
    return True, "sid-t18", {"totalTokens": 1}


M._dsh_run = fake_run


class _W:
    def __init__(self):
        self.buf = []

    def write(self, b):
        self.buf.append(b.decode("utf-8"))

    def flush(self):
        pass


class FakeHandler:
    def __init__(self, body):
        self.path = "/api/dsh/chat?project=%s&agent=dsh-a" % PROJ
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.wfile = _W()
        self.status = None
        self.json = None

    def send_response(self, s):
        self.status = s

    def send_header(self, k, v):
        pass

    def end_headers(self):
        pass

    def _send_json(self, payload, status=200):
        self.json = payload
        self.status = status

    def events(self):
        raw = "".join(self.wfile.buf)
        out = []
        for line in raw.split("\n"):
            line = line.strip()
            if line.startswith("data: "):
                try:
                    out.append(json.loads(line[6:]))
                except Exception:
                    pass
        return out


def phases(evs):
    return [e.get("phase") for e in evs if e.get("type") == "status"]


body_a = json.dumps({"message": "第一句：先到的这一轮"}).encode()
body_b = json.dumps({"message": "第二句：应该被告知在排队"}).encode()
ha, hb = FakeHandler(body_a), FakeHandler(body_b)
ta = threading.Thread(target=M._handle_dsh_chat, args=(ha, PROJ))
ta.start()
time.sleep(0.15)
tb = threading.Thread(target=M._handle_dsh_chat, args=(hb, PROJ))
tb.start()
seen = []
for _ in range(40):
    li = M._dsh_running_list(PROJ)
    if li:
        seen = li
        break
    time.sleep(0.05)
ck("C1 跑的过程中登记表能看到 dsh-a（label=对话）",
   bool(seen) and seen[0]["label"] == "对话" and seen[0]["elapsed"] >= 0, str(seen))
ta.join(20)
tb.join(20)
ea, eb = ha.events(), hb.events()
ck("C2 先到的一轮有 accepted", "accepted" in phases(ea), str(phases(ea)))
ck("C3 先到的一轮没有 queued", "queued" not in phases(ea), str(phases(ea)))
ck("C4 后到的一轮有 accepted→queued→dequeued", phases(eb)[:3] == ["accepted", "queued", "dequeued"], str(phases(eb)))
q = next((e for e in eb if e.get("phase") == "queued"), {})
ck("C5 queued 带 running_label=对话", q.get("running_label") == "对话", json.dumps(q, ensure_ascii=False))
ck("C6 queued 带 running_elapsed>=0 与 running_since", "running_elapsed" in q and bool(q.get("running_since")), json.dumps(q, ensure_ascii=False))
dq = next((e for e in eb if e.get("phase") == "dequeued"), {})
ck("C7 dequeued 的 waited 在 0.1~3 秒之间", 0.1 <= (dq.get("waited") or 0) <= 3.0, json.dumps(dq, ensure_ascii=False))
ck("C8 两轮都正常结束（有 final + dsh_done）",
   any(e.get("type") == "dsh_done" for e in ea) and any(e.get("type") == "dsh_done" for e in eb))
ck("C9 跑完登记表清空", M._dsh_running_list(PROJ) == [], str(M._dsh_running_list(PROJ)))
log = open(M.DSH_LOG_FILE, encoding="utf-8", errors="replace").read() if os.path.exists(M.DSH_LOG_FILE) else ""
ck("C10 日志留下「进入排队」与「排队结束」", "进入排队" in log and "排队结束" in log)

hc = FakeHandler(json.dumps({"message": "第三句：独占一轮"}).encode())
M._handle_dsh_chat(hc, PROJ)
ec = hc.events()
ck("C11 独占一轮不发 queued", "queued" not in phases(ec), str(phases(ec)))
ck("C12 独占一轮仍有 accepted", "accepted" in phases(ec), str(phases(ec)))


# ---------------------------------------------------------------- D 前端秒表
print("\nD 段 org_chart.html 前端（源码断言）")
ck("D1 有 accepted 文案", "已受理，正在准备工作环境" in ORG)
ck("D2 有排队文案且带 label/已跑", "这个成员上一轮还在跑" in ORG and "running_label" in ORG and "running_elapsed" in ORG)
ck("D3 有排队结束文案带 waited", "排队结束（等了" in ORG and "dequeued" in ORG)
ck("D4 发出即显示「已发送，正在唤醒 Agent…」", "已发送，正在唤醒 Agent" in ORG)
ck("D5 秒表函数齐备", all(x in ORG for x in ("function dshFmtDur", "function dshActivityPaint", "function dshActivityShow", "function dshActivityHide")))
ck("D6 打开卡片查一次 running", "loadDshRunning()" in ORG and "/api/dsh/running" in ORG)
ck("D7 秒表样式存在", ".dsh-act-elapsed" in ORG)
ck("D8 已无遗留的 actFor 引用", "actFor" not in ORG)
ck("D9 断开/结束都会收掉转圈", ORG.count("dshActivityHide(targetAgent)") >= 4, "count=%d" % ORG.count("dshActivityHide(targetAgent)"))

shutil.rmtree(ROOT, ignore_errors=True)
print("\n%d PASS / %d FAIL" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
