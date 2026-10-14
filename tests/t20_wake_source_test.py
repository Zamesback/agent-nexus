# -*- coding: utf-8 -*-
"""T20：v2.20.26「自动唤醒只认 Zames 的 @」+ 三条节奏规则 + 成员卡工具可见。

起因（2026-10-13 用户反馈，三条都已批准）：
  ① 成员卡只显示「正在工作中 · 第 N 步」，一轮 101 步 / 33.8 分钟里用户看不到它在干什么；
  ② 一轮里同一文件被 edit 16 次，每一步都要重发全部上下文——用户只收到一个迟到的结论；
  ③ 自动唤醒以前「谁 @ 都算」，成员互相 @ 把彼此的坑占满，用户自己的 @ 反而排队十几分钟。

本测试锁住：
  A) _is_owner_sender()：只有 Zames（id=user / 姓名 Zames，含 @ 前缀与大小写）算发起人；
  B) trigger_agent_if_mentioned() 的托管唤醒分支：非 Zames 的 @ 明确跳过（并留日志）；
  C) 任务 assignee 那条唤醒路径不受影响（成员仍可派工）；
  D) 身份锚定前缀与自动唤醒提示词里有三条节奏规则；
  E) 前端（org_chart.html）确实把 tool_call / tool_result 画进活动行。

跑法：python3 tests/t20_wake_source_test.py
"""
import importlib.util
import os
import re
import sys
import tempfile

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
    spec = importlib.util.spec_from_file_location("srv_t20", os.path.join(REPO, "server.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ROOT = tempfile.mkdtemp(prefix="t20_")
os.environ["AGENTNEXUS_DSH_LOG"] = os.path.join(ROOT, "dsh.log")
os.environ["COLLAB_PROJECTS_ROOT"] = ROOT
M = load_server()
M.PROJECTS_ROOT = ROOT
M.DSH_LOG_FILE = os.path.join(ROOT, "dsh.log")
M._auto_git_snapshot = lambda *a, **k: None
M._auto_git_commit = lambda *a, **k: None

SRC = open(os.path.join(REPO, "server.py"), encoding="utf-8").read()
HTML = open(os.path.join(REPO, "org_chart.html"), encoding="utf-8").read()

# 名册：与真实三国指挥官同形（主导方 id=user / name=Zames；其余成员 id 形如 planner / DSH2）
BOARD = {
    "agents": [
        {"id": "user", "name": "Zames", "role": "项目主导方/最终决策者"},
        {"id": "planner", "name": "规划Agent", "role": "任务拆解/方案评审/进度协调/验收"},
        {"id": "DSH2", "name": "联合Planner", "role": "联合Planner", "entry": {"type": "dsh"}},
        {"id": "设计总负责DSH-1", "name": "设计总负责DSH-1", "role": "美工设计总体负责人",
         "entry": {"type": "dsh"}},
    ],
    "messages": [],
    "tasks": [],
    "protocol": {"mode": "working"},
}

print("A) _is_owner_sender —— 只有 Zames 算发起人")
for s, want, label in [
    ("user", True, "id=user"),
    ("Zames", True, "姓名 Zames"),
    ("zames", True, "姓名小写"),
    ("@Zames", True, "带 @ 前缀"),
    ("  Zames  ", True, "首尾空格"),
    ("项目主导方", True, "角色别名"),
    ("planner", False, "规划Agent 不是发起人"),
    ("DSH2", False, "成员 DSH2 不是发起人"),
    ("设计总负责DSH-1", False, "成员 DSH-1 不是发起人"),
    ("system", False, "system 不是发起人"),
    ("", False, "空发送者"),
    (None, False, "None 发送者"),
]:
    ck("_is_owner_sender(%r) == %s（%s）" % (s, want, label), M._is_owner_sender(BOARD, s) is want)
# 名册里主导方换了名字也要认（按名册兜底，而不是写死 Zames）
alt = {"agents": [{"id": "user", "name": "老板"}]}
ck("名册里主导方改名后仍认 id=user", M._is_owner_sender(alt, "user") is True)
# Zames 是写死的别名（历史消息的 sender 就写名字，而名册名称可能被改过）——保留兜底
ck("名册改名后 Zames 仍按别名认下（兼容历史消息）", M._is_owner_sender(alt, "Zames") is True)
ck("名册改名后成员 id 仍不被误认", M._is_owner_sender(alt, "planner") is False)
ck("空名册不崩", M._is_owner_sender(None, "user") is True)   # id 兜底仍认 user
ck("空名册 + 未知发送者不崩", M._is_owner_sender(None, "planner") is False)

print("\nB) 唤醒分支的源码契约 —— 非用户 @ 明确跳过")
hook = SRC[SRC.index("v2.20.7：托管（dsh）成员的自动唤醒"):]
hook = hook[:hook.index("# ===== 以下为API模式自动触发")]
ck("分支先算 _is_owner_sender", "_is_owner_sender(board_data, sender)" in hook)
ck("只有发起人时才 trigger_dsh_wake", "if _is_owner_sender(board_data, sender):" in hook)
ck("非发起人时留日志（自动唤醒跳过（非用户 @））", "自动唤醒跳过（非用户 @" in hook)
ck("日志里提示要用任务 assignee 派工", "要派工请用任务 assignee" in hook)
ck("非托管成员的 @ 不打日志（dsh_mentioned 过滤）", "_dsh_is_terminal_member(project, a)" in hook)
ck("跳过类型表仍然生效（system/auto_reply/discuss）",
   "DSH_AUTO_WAKE_NO_WAKE_TYPES" in hook)

print("\nC) 任务派工那条路径不受影响")
task_hook = SRC[SRC.index('reason="任务派工"'):]
task_hook = task_hook[-400:]
ck("任务派工仍调用 trigger_dsh_wake", "trigger_dsh_wake(project, update[\"assignee\"]" in SRC)
ck("派工路径没有加 _is_owner_sender 限制", "_is_owner_sender" not in task_hook)

print("\nD) 三条节奏规则进了提示词")
anchor = SRC[SRC.index("【身份锚定】你是"):]
anchor = anchor[:anchor.index("% (project, m_name, agent_id, m_role, dsh_input)")]
for rule in ("【先答后干】", "【能合并就合并】", "【长活收口】"):
    ck("身份锚定里有 %s" % rule, rule in anchor)
ck("锚定里说明了「先给结论再继续」", "先用一两行告诉他结论或方向" in anchor)
ck("锚定里说明了「合并 edit」", "合并进一次 edit / write" in anchor)
ck("锚定里说明了「写进任务卡」", "写进看板任务卡" in anchor)
wake = SRC[SRC.index("def _dsh_auto_wake_prompt"):]
wake = wake[:wake.index("def _dsh_append_board_reply")]
ck("自动唤醒提示词有第 6 条（先回一句）", "6) 若这一轮的活要干很久" in wake)
ck("自动唤醒提示词有第 7 条（合并 + 收口）", "7) 同一个文件的多处改动尽量合并" in wake)
ck("自动唤醒提示词说明只有 Zames 的 @ 会唤醒成员", "只有 Zames 的 @ 会唤醒成员" in wake)

print("\nE) 前端把工具画进活动行")
ck("有 dshToolLabel()", "function dshToolLabel(ev) {" in HTML)
ck("有 dshActivityPush()", "function dshActivityPush(text, agentId) {" in HTML)
ck("活动行支持多行日志（log 数组）", "dshAct.log = (dshAct.log || []).slice(-3);" in HTML)
ck("SSE 分支处理 tool_call", "if (t === 'tool_call') {" in HTML)
ck("SSE 分支处理 tool_result 失败", "if (t === 'tool_result') {" in HTML and "工具失败：" in HTML)
ck("每轮重置工具计数", "dshToolCount = 0;" in HTML)
ck("bash 工具显示命令头", "if (name === 'bash') detail = one(inp.command);" in HTML)
ck("edit/write/read 显示文件名",
   "['edit', 'write', 'read', 'present'].includes(name)" in HTML)
ck("隐藏活动行时清掉日志",
   "dshAct = { agent: null, t0: 0, text: '', log: [], timer: null };" in HTML)

print("\nF) 行为验证 —— 真的调 trigger_agent_if_mentioned（线程被替换，零模型成本）")
import json as _json
import types as _types

FROJ = "T20Proj"
PDIR = os.path.join(ROOT, FROJ)
os.makedirs(PDIR, exist_ok=True)
M.PROJECTS_ROOT = ROOT
with open(os.path.join(PDIR, "project_config.json"), "w", encoding="utf-8") as fh:
    _json.dump({"dsh": {"auto_wake": True}}, fh)
_fboard = {
    "project_info": {"name": FROJ, "workspace": PDIR},
    "agents": [
        {"id": "user", "name": "Zames", "role": "项目主导方/最终决策者"},
        {"id": "planner", "name": "规划Agent", "role": "任务拆解"},
        {"id": "dshtest", "name": "托管试验员", "role": "执行", "entry": {"type": "dsh", "target": "进程托管（本机 dsh）"}},
    ],
    "messages": [], "tasks": [], "decisions": [], "protocol": {"mode": "working"},
}
with open(os.path.join(PDIR, "collab_board.json"), "w", encoding="utf-8") as fh:
    _json.dump(_fboard, fh, ensure_ascii=False)

_started = []


class _FakeThread(object):
    def __init__(self, target=None, args=(), kwargs=None, daemon=None, **kw):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        _started.append(self.target.__name__ if self.target else "?")

    def join(self, *a, **k):
        return None


M.threading = _types.SimpleNamespace(Thread=_FakeThread)


def _post(sender, content, mid):
    """把消息写进看板再真的走一遍触发链。"""
    b = M.load_board(FROJ)
    b.setdefault("messages", []).append({"id": mid, "sender": sender, "content": content,
                                         "timestamp": "2026-10-14 10:00:00", "type": "text"})
    with M.board_lock:
        M.save_board(b, FROJ)
    msg = {"id": mid, "sender": sender, "content": content, "type": "text"}
    del _started[:]
    M._DSH_WAKE_ACTIVE.discard((FROJ, "dshtest"))
    M.trigger_agent_if_mentioned(msg, b, FROJ)
    return ("dshtest" in _started) or ((FROJ, "dshtest") in M._DSH_WAKE_ACTIVE)


owner_woke = _post("Zames", "@dshtest 请把 T20 的验收跑一遍", 101)
ck("Zames 的 @ 会唤醒托管成员", owner_woke is True)
log_txt = open(M.DSH_LOG_FILE, encoding="utf-8", errors="replace").read() if os.path.exists(M.DSH_LOG_FILE) else ""
ck("发起人那条没被拦（日志里没有对应的跳过记录）", "自动唤醒跳过（非用户 @）" not in log_txt)

member_woke = _post("planner", "@dshtest 你接手这个任务", 102)
ck("成员的 @ 不再唤醒托管成员", member_woke is False)
log_txt = open(M.DSH_LOG_FILE, encoding="utf-8", errors="replace").read()
ck("跳过时留了日志", "自动唤醒跳过（非用户 @" in log_txt)
ck("日志点明了发送者与目标", "sender=planner 点了 dshtest" in log_txt)
ck("日志提示改用任务 assignee", "要派工请用任务 assignee" in log_txt)

status_woke = _post("Zames", "@dshtest 系统提示", 103)
ck("（对照）发起人再 @ 一次仍然会唤醒", status_woke is True)

print("\nG) 活动行的真实渲染（假 DOM + 真源码，node 跑）")
import subprocess
import json as _j

NODE = ("/Users/zames/Library/Application Support/DoubaoWork/sandbox_runtime/bases/"
        "98670218a5f0d8bc9b9ebf3f70881304/bin/node")


def _pick(pattern):
    m = re.search(pattern, HTML, re.S)
    return m.group(0) if m else ""


pieces = [
    _pick(r"let dshAct = \{.*?\};"),
    _pick(r"let dshToolCount = 0;"),
    _pick(r"const DSH_TOOL_ICON = \{.*?\};"),
    _pick(r"function dshToolLabel\(ev\) \{.*?\n\}"),
    _pick(r"function dshFmtDur\(sec\) \{.*?\n\}"),
    _pick(r"function dshActivityPaint\(\) \{.*?\n\}"),
    _pick(r"function dshActivityShow\(text, agentId\) \{.*?\n\}"),
    _pick(r"function dshActivityPush\(text, agentId\) \{.*?\n\}"),
]
ck("抽到 8 段活动行真实源码", all(pieces), str([bool(x) for x in pieces]))

if all(pieces) and os.path.exists(NODE):
    # 注意：inserts[0] 是 dshActivityShow('已发送…') 的插入，第一次 push 的 HTML 在 inserts[1]。
    # JS 里不要写行内 // 注释——它会把同一行后面的字段一起吞掉。
    js = """
const inserts = [];
const box = { insertAdjacentHTML: (p, h) => inserts.push(h), scrollTop: 0, scrollHeight: 10 };
function esc2(s) { return String(s); }
const document = { getElementById: (id) => (id === 'dshMsgs' ? box : null) };
function setInterval() { return 1; }
function clearInterval() {}
let currentDshAgent = 'A';
""" + "\n".join(pieces) + """
const labels = [
  dshToolLabel({ tool: 'bash', input: { command: 'ls -la /tmp | head -3' } }),
  dshToolLabel({ tool: 'edit', input: { file_path: '/Users/zames/Desktop/Project/三国指挥官/prototype/m1/index.html' } }),
  dshToolLabel({ tool: 'grep', input: { pattern: 'spawnFlame', path: '/a/b/index.html' } }),
  dshToolLabel({ tool: 'weird', input: { a: 1 } }),
  dshToolLabel({ tool: 'bash', input: { command: 'git status --short' } }),
];
dshActivityShow('已发送，正在唤醒 Agent…', 'A');
dshActivityPush(labels[0] + '（本轮第 1 次工具）', 'A');
dshActivityPush(labels[1], 'A');
dshActivityPush(labels[2], 'A');
dshActivityPush(labels[3], 'A');
dshActivityPush(labels[4], 'A');
console.log(JSON.stringify({ labels: labels, first: inserts[1], last: inserts[inserts.length - 1], n: inserts.length }));
"""
    path = os.path.join(ROOT, "act_probe.js")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(js)
    out = subprocess.run([NODE, path], capture_output=True, text=True)
    try:
        got = _j.loads((out.stdout or "").strip().splitlines()[-1])
    except Exception:
        got = None
    ck("node 跑通了活动行", got is not None, (out.stderr or "")[:200])
    if got:
        lab = got["labels"]
        ck("bash 显示图标 + 命令头", "🔧 bash" in lab[0] and "ls -la /tmp | head -3" in lab[0], lab[0])
        ck("edit 只显示文件名", "✏️ edit" in lab[1] and "index.html" in lab[1] and "prototype" not in lab[1], lab[1])
        ck("grep 显示模式 + 文件", "🔎 grep" in lab[2] and "spawnFlame" in lab[2] and "index.html" in lab[2], lab[2])
        ck("未知工具不崩且用兜底图标", lab[3].startswith("🛠 weird"), lab[3])
        last = got["last"]
        ck("活动行是真实 DOM 插入", got["n"] >= 1 and "<div" in last, last[:120])
        ck("工具计数写进活动行（第一次 push 的那行）",
           "本轮第 1 次工具" in got["first"] and "🔧 bash" in got["first"] and "ls -la /tmp" in got["first"],
           got["first"][:200])
        ck("只保留最近 4 行（最老的被挤掉）", "ls -la /tmp" not in last)
        ck("最新一行仍在", "🔧 bash" in last and "git status --short" in last, last[:200])
        ck("活动行带秒表后缀", "秒" in last and "dsh-act-elapsed" in last)

print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
