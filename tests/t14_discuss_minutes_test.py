#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T14 回归测试：讨论室转写落盘 + 会议纪要 + 与会者记忆（v2.20.18）

背景
----
v2.20 的讨论室把房间放在内存里（_discuss_rooms），stop / server 重启即清空；讨论
发言也从不写进 dsh_chat/<成员>.json（每轮都开全新会话，且不调 _dsh_append_history）。
于是真实事故：用户开完 52 轮讨论、点了结束，再问某个与会者「刚才讨论了什么」，
它只能如实回答「我这边没有任何记录」——用户看到的是「Agent 不记得自己开过的会」。

本测试锁住 v2.20.18 补上的三件事：
  A 转写落盘  _discuss_write_transcript / _discuss_finalize
  B 会议纪要  _discuss_post_minutes（看板 type=discuss 消息）
  C 成员记忆  _discuss_remember（dsh_chat/<成员>.json 追加一条）
外加两个防回归点：discuss 类型不触发自动唤醒、discussions/ 进 git 快照范围。

用法：python3 tests/t14_discuss_minutes_test.py
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location("agentnexus_server_t14", os.path.join(ROOT, "server.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

PASS = 0
FAIL = 0
FAILS = []


def ok(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILS.append(label)
        print("  \u2717 %s" % label)


def eq(got, want, label):
    ok(got == want, "%s (got=%r want=%r)" % (label, got, want))


# ---------- 隔离环境：临时项目目录，不碰真实数据仓库 ----------
TMP = tempfile.mkdtemp(prefix="t14_discuss_")
PROJ = "DemoProj"
PDIR = os.path.join(TMP, PROJ)
os.makedirs(PDIR)
M.PROJECTS_ROOT = TMP
M._auto_git_commit = lambda *a, **k: None     # 临时目录不是 git 仓库，别真的跑 git
M.DSH_LOG_FILE = os.path.join(TMP, "t14_dsh.log")   # 别把测试日志写进真实 /tmp/dsh_server.log


def write_board(messages=None):
    board = {
        "project_info": {"name": PROJ, "workspace": TMP},
        "agents": [
            {"id": "user", "name": "Zames", "role": "主导方"},
            {"id": "dsh-a", "name": "甲", "role": "美术", "entry": {"type": "dsh"}},
            {"id": "dsh-b", "name": "乙", "role": "策划", "entry": {"type": "dsh"}},
            {"id": "dsh-c", "name": "丙", "role": "验收", "entry": {"type": "dsh"}},
        ],
        "tasks": [], "messages": messages or [], "decisions": [],
        "protocol": {"mode": "working"},
    }
    with open(os.path.join(PDIR, "collab_board.json"), "w", encoding="utf-8") as f:
        json.dump(board, f, ensure_ascii=False)


def read_board():
    with open(os.path.join(PDIR, "collab_board.json"), encoding="utf-8") as f:
        return json.load(f)


def make_room(topic="测试话题", hist=None, **kw):
    room = {
        "topic": topic, "participants": ["dsh-a", "dsh-b", "dsh-c"], "history": [],
        "turn_idx": 0, "round_count": 0, "round_limit": None,
        "active": True, "busy": None, "end_reason": None,
        "member_names": {"dsh-a": "甲", "dsh-b": "乙", "dsh-c": "丙"},
        "started_at": "2026-10-10_1110", "started_full": "2026-10-10 11:10:07",
    }
    room["history"].extend(hist or [])
    room.update(kw)
    return room


def three_turns():
    return [
        {"from": "你", "text": "主题：测试话题", "ts": "11:10:07"},
        {"from": "甲", "text": "我认为先做 A。", "ts": "11:10:20"},
        {"from": "乙", "text": "我同意甲，但要补 B。", "ts": "11:11:00"},
        {"from": "丙", "text": "判据不齐就一律阻断。", "ts": "11:11:40"},
    ]


# ---------- A：转写 ----------
print("A. 转写落盘")
eq(M._discuss_slug("a/b\\c:d*e?f\"g<h>i|j"), "a b c d e f g h i j", "话题 → 安全文件名片段")
eq(M._discuss_slug("   "), "讨论", "空话题回退为「讨论」")
ok(len(M._discuss_slug("x" * 200)) <= 40, "文件名片段限长 40")

room = make_room(hist=three_turns())
rel = M._discuss_transcript_rel(PROJ, room)
eq(rel, os.path.join("discussions", "2026-10-10_1110_测试话题.md"), "转写相对路径")
eq(M._discuss_transcript_rel(PROJ, room), rel, "同一房间的转写路径稳定（不重复生成）")

txt = M._discuss_transcript_text(PROJ, room)
ok("# 讨论室转写 · 测试话题" in txt, "转写含标题")
ok("- 参与者：甲、乙、丙" in txt, "转写含参与者")
ok("- 开始：2026-10-10 11:10:07" in txt, "转写含开始时间")
ok("### 4. 丙 · 11:11:40" in txt, "转写逐条编号 + 发言人 + 时间")
ok("判据不齐就一律阻断。" in txt, "转写含正文")

got = M._discuss_write_transcript(PROJ, room)
eq(got, rel, "写转写返回相对路径")
path = os.path.join(PDIR, rel)
ok(os.path.isfile(path), "转写文件真的落盘")
ok(not os.path.exists(path + ".tmp"), "原子写不留 .tmp 残留")
with open(path, encoding="utf-8") as f:
    ok("判据不齐就一律阻断。" in f.read(), "落盘内容完整")

eq(M._discuss_write_transcript("NoSuchProj", make_room()), None, "项目不存在时不写（返回 None）")

# 同名冲突 → 加序号
room2 = make_room(hist=three_turns())
rel2 = M._discuss_transcript_rel(PROJ, room2)
eq(rel2, os.path.join("discussions", "2026-10-10_1110_测试话题_2.md"), "同名转写自动加序号")

# 超长历史截断
big = make_room(hist=[{"from": "甲", "text": "第%d条" % i, "ts": "11:00:00"} for i in range(450)])
eq(len(M._discuss_transcript_text(PROJ, big).split("### ")) - 1, M.DISCUSS_TRANSCRIPT_MAX,
   "转写按 DISCUSS_TRANSCRIPT_MAX 截断")

# ---------- 收尾立场 ----------
print("B. 收尾立场与纪要文本")
room = make_room(hist=three_turns())
pos = M._discuss_last_positions(room)
eq([p[0] for p in pos], ["dsh-a", "dsh-b", "dsh-c"], "收尾立场按参与者顺序")
eq([p[1] for p in pos], ["甲", "乙", "丙"], "收尾立场带显示名")
eq(pos[2][2], "判据不齐就一律阻断。", "取的是每人最后一条发言")

room_nobody = make_room(hist=[{"from": "甲", "text": "只有甲说了话", "ts": "11:00:00"}])
pos_nb = M._discuss_last_positions(room_nobody)
eq([p[2] for p in pos_nb], ["只有甲说了话", "", ""], "没发言的参与者留空（不丢人）")

room = make_room(hist=three_turns(), round_count=52, end_reason="user_stop")
mt = M._discuss_minutes_text(room, rel)
ok("【会议纪要】讨论室「测试话题」" in mt, "纪要含标题")
ok("· 参与者：甲、乙、丙" in mt, "纪要含参与者")
ok("· 轮次：52 轮" in mt, "纪要含轮次")
ok("· 结束：用户叫停" in mt, "纪要含结束原因（中文标签）")
ok("· 转写：" + rel in mt, "纪要含转写指针")
ok("· 丙：判据不齐就一律阻断。" in mt, "纪要含各人收尾立场")
eq(M._DISCUSS_END_LABEL["round_limit"], "达到轮次上限", "结束原因标签表")

# ---------- B：看板纪要 ----------
print("C. 看板会议纪要")
write_board()
room = make_room(hist=three_turns(), round_count=4, end_reason="user_stop")
new_id = M._discuss_post_minutes(PROJ, room, rel)
board = read_board()
eq(len(board["messages"]), 1, "看板新增 1 条消息")
msg = board["messages"][0]
eq(msg["id"], new_id, "返回的消息 id 与落库一致")
eq(msg["type"], "discuss", "消息 type=discuss")
eq(msg["sender"], "system", "消息 sender=system")
eq(msg["to"], ["all"], "消息广播给所有人")
ok("【会议纪要】" in msg["content"], "消息正文是纪要")
eq(msg["discuss"]["participants"], ["dsh-a", "dsh-b", "dsh-c"], "discuss.participants")
eq(msg["discuss"]["rounds"], 4, "discuss.rounds")
eq(msg["discuss"]["transcript"], rel, "discuss.transcript 指针")
eq(msg["discuss"]["reason"], "user_stop", "discuss.reason")

# id 递增
room_b = make_room(hist=three_turns(), round_count=5)
M._discuss_post_minutes(PROJ, room_b, rel)
eq(len(read_board()["messages"]), 2, "第二条纪要 id 递增不覆盖")

# ---------- C：成员记忆 ----------
print("D. 与会者记忆")
write_board()
hist_dir = os.path.join(PDIR, "dsh_chat")
if os.path.isdir(hist_dir):
    shutil.rmtree(hist_dir)
room = make_room(hist=three_turns(), round_count=4, end_reason="user_stop")
n = M._discuss_remember(PROJ, room, rel)
eq(n, 3, "给 3 位与会者各写一条")
for mid in ("dsh-a", "dsh-b", "dsh-c"):
    hp = os.path.join(hist_dir, mid + ".json")
    ok(os.path.isfile(hp), "%s 的聊天历史文件已生成" % mid)
    with open(hp, encoding="utf-8") as f:
        h = json.load(f)
    eq(len(h), 1, "%s 恰好追加 1 条（不是 52 轮全文）" % mid)
    e = h[0]
    eq(e["role"], "assistant", "%s 记忆以 assistant 身份写入" % mid)
    ok(e.get("discuss") is True, "%s 记忆带 discuss 标记" % mid)
    eq(e.get("transcript"), rel, "%s 记忆带转写指针" % mid)
    ok("【会议纪要】我参加了讨论室「测试话题」" in e["content"], "%s 记忆含纪要开头" % mid)
    ok("完整转写：" + rel in e["content"], "%s 记忆含文件指针" % mid)

with open(os.path.join(hist_dir, "dsh-b.json"), encoding="utf-8") as f:
    hb = json.load(f)[0]["content"]
ok("我在会上的收尾立场：我同意甲，但要补 B。" in hb, "记忆含自己的收尾立场")
ok("其他人的收尾立场：" in hb, "记忆含其他人的收尾立场")
ok("· 甲：我认为先做 A。" in hb, "记忆含甲（别人）的立场")
ok("· 乙：我同意甲" not in hb, "记忆不含自己（避免自我重复）")

# ---------- finalize 幂等 ----------
print("E. _discuss_finalize 幂等与逐轮刷新")
write_board()
shutil.rmtree(hist_dir, ignore_errors=True)
room = make_room(hist=three_turns(), round_count=4, end_reason="user_stop")
rel_f = M._discuss_finalize(PROJ, room)
ok(rel_f and rel_f.startswith(os.path.join("discussions", "2026-10-10_1110_测试话题")),
   "finalize 返回转写路径（同名自动加序号）")
eq(len(read_board()["messages"]), 1, "finalize 落一条纪要")
eq(room.get("_written_len"), 4, "记录已写入的历史长度")
eq(room.get("_minutes_done"), True, "纪要只做一次（打标）")

with open(os.path.join(PDIR, rel_f), encoding="utf-8") as f:
    first_txt = f.read()
M._discuss_finalize(PROJ, room)          # 再调一次
eq(len(read_board()["messages"]), 1, "重复 finalize 不重复落纪要")
with open(os.path.join(hist_dir, "dsh-a.json"), encoding="utf-8") as f:
    eq(len(json.load(f)), 1, "重复 finalize 不重复写成员记忆")
with open(os.path.join(PDIR, rel_f), encoding="utf-8") as f:
    eq(f.read(), first_txt, "历史没变时转写内容不变")

room["history"].append({"from": "甲", "text": "补充一句。", "ts": "11:12:00"})
M._discuss_finalize(PROJ, room, minutes=False)
with open(os.path.join(PDIR, rel_f), encoding="utf-8") as f:
    txt2 = f.read()
ok("补充一句。" in txt2, "逐轮刷新把新发言写进同一个转写文件")
eq(len(read_board()["messages"]), 1, "minutes=False 不落纪要")
eq(M._discuss_finalize(PROJ, room), rel_f, "收尾再 finalize 仍返回同一路径")

# ---------- 停止处理 ----------
print("F. /api/discuss/stop 收尾")
write_board()
shutil.rmtree(hist_dir, ignore_errors=True)
M._discuss_rooms.clear()
M._discuss_subs.clear()


class FakeSelf(object):
    def __init__(self):
        self.sent = []

    def _send_json(self, payload, status=200):
        self.sent.append((payload, status))


room = make_room(hist=three_turns(), round_count=4)
M._discuss_rooms[PROJ] = room
fs = FakeSelf()
M._handle_discuss_stop(fs, PROJ)
eq(fs.sent[-1][0], {"ok": True}, "stop 返回 ok")
ok(PROJ not in M._discuss_rooms, "房间已清理")
stop_rel = room.get("_transcript_rel")
ok(stop_rel and os.path.isfile(os.path.join(PDIR, stop_rel)), "stop 前把转写落了盘")
with open(os.path.join(PDIR, stop_rel), encoding="utf-8") as f:
    stop_txt = f.read()
ok("- 结束：用户叫停" in stop_txt, "叫停后转写头部从「进行中」改为「用户叫停」")
eq(len(read_board()["messages"]), 1, "stop 落了会议纪要")
with open(os.path.join(hist_dir, "dsh-c.json"), encoding="utf-8") as f:
    ok(len(json.load(f)) == 1, "stop 给与会者留了记忆")
ok(room.get("end_reason") == "user_stop", "结束原因记为 user_stop")

# 停止时还有一轮在场 → 交给后台线程，等它说完再落盘
M._discuss_rooms.clear()
write_board()
shutil.rmtree(hist_dir, ignore_errors=True)
room2 = make_room(topic="在场轮次", hist=three_turns(), round_count=4, busy="dsh-a")
M._discuss_rooms[PROJ] = room2
M._handle_discuss_stop(FakeSelf(), PROJ)
ok(PROJ in M._discuss_rooms, "有轮次在场时不当场清房间")
room2["history"].append({"from": "甲", "text": "最后一句。", "ts": "11:12:30"})
room2["busy"] = None
for _ in range(40):
    if PROJ not in M._discuss_rooms:
        break
    time.sleep(0.25)
ok(PROJ not in M._discuss_rooms, "后台线程收尾后清掉房间")
room2_rel = room2.get("_transcript_rel")
ok(room2_rel and os.path.isfile(os.path.join(PDIR, room2_rel)), "后台收尾写了转写")
with open(os.path.join(PDIR, room2_rel), encoding="utf-8") as f:
    ok("最后一句。" in f.read(), "在场那一轮的最后一句没被截掉")

# 房间不存在时 stop 也要正常
M._discuss_rooms.clear()
fs = FakeSelf()
M._handle_discuss_stop(fs, PROJ)
eq(fs.sent[-1][0], {"ok": True}, "没有房间时 stop 依然返回 ok")

# ---------- 死订阅 ----------
print("G. 死订阅摘除")
got = []


def dead(ev):
    pass


dead._state = {"dead": True}


def boom(ev):
    raise BrokenPipeError("client gone")


def live(ev):
    got.append(ev)


M._discuss_subs[PROJ] = [dead, boom, live]
M._discuss_broadcast(PROJ, {"type": "x"})
eq(M._discuss_subs[PROJ], [live], "死订阅与抛异常的订阅都被摘掉")
eq(len(got), 1, "活订阅照常收到广播")

# ---------- 防回归：唤醒与快照 ----------
print("H. 防回归")
ok("discuss" in M.DSH_AUTO_WAKE_NO_WAKE_TYPES, "discuss 类型不触发自动唤醒")
ok("system" in M.DSH_AUTO_WAKE_NO_WAKE_TYPES and "auto_reply" in M.DSH_AUTO_WAKE_NO_WAKE_TYPES,
   "原有跳过类型没被破坏")
ok("discussions" in M.AUTO_GIT_EXTRA_PATHS, "discussions/ 进 git 快照范围")

# _auto_git_commit 真的会把 discussions/ 列进 git add
calls = []
_real_run = subprocess.run


def fake_run(cmd, *a, **k):
    calls.append(cmd)

    class R(object):
        returncode = 0
        stdout = b""
        stderr = b""
    return R()


subprocess.run = fake_run
try:
    spec2 = importlib.util.spec_from_file_location("agentnexus_server_t14b", os.path.join(ROOT, "server.py"))
    M2 = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(M2)
    M2.PROJECTS_ROOT = TMP
    os.makedirs(os.path.join(PDIR, "discussions"), exist_ok=True)
    M2._auto_git_commit(os.path.join(PDIR, "collab_board.json"))
finally:
    subprocess.run = _real_run
add_cmd = [c for c in calls if len(c) > 4 and c[3] == "add"]
ok(bool(add_cmd), "触发了 git add")
if add_cmd:
    ok(any("discussions" in str(x) for x in add_cmd[0]), "git add 目标含 discussions/")
    ok(any("collab_board.json" in str(x) for x in add_cmd[0]), "git add 目标含看板本体")

# ---------- 收尾 ----------
M.PROJECTS_ROOT = os.path.join(ROOT, "..")
shutil.rmtree(TMP, ignore_errors=True)

print("")
print("T14 讨论室纪要：%d 通过 / %d 失败" % (PASS, FAIL))
if FAILS:
    print("失败项：")
    for f in FAILS:
        print("  - %s" % f)
sys.exit(1 if FAIL else 0)
