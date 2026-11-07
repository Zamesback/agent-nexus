# -*- coding: utf-8 -*-
"""T27：v2.20.35 员工（跨项目记忆）P0

设计稿：docs/DESIGN-员工-跨项目记忆-v1.md
P0 只做最小闭环：转员工（确定性基线蒸馏）+ 指派入项目 + 身份锚定注入。

这里用假看板/假 profile 跑**真实函数**，锁住：
  · 员工 id = 可读 slug，重名自动加序号
  · 基线记忆来自看板事实（done 任务 / 交付物 / 决策），**不含聊天原文**
  · 转员工只给成员打 employee_id，不覆盖 agents 数组
  · 指派会建席位、初始化 dsh 配置、生成 profile、记录项目履历
  · 同一员工在同一项目不能占两个席位
  · 记忆有 8 KB 上限；注入锚定有 1500 字上限
跑法：python3 tests/t27_employees_test.py
"""
import contextlib
import json
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


tmp = tempfile.mkdtemp(prefix="t27emp-")

BOARD = {
    "agents": [
        {"id": "user", "name": "Zames"},
        {"id": "dsh-1", "name": "DSH-1", "role": "美工设计总体负责人", "entry_type": "dsh"},
    ],
    "tasks": [
        {"id": "T1", "title": "城墙贴图 v4", "assignee": "dsh-1", "status": "done"},
        {"id": "T2", "title": "谋略卡演出", "assignee": "dsh-1", "status": "done"},
        {"id": "T3", "title": "进行中的活", "assignee": "dsh-1", "status": "in_progress"},
        {"id": "T9", "title": "别人的活", "assignee": "dsh-2", "status": "done"},
    ],
    "decisions": [
        {"title": "水墨风作为主轴", "decision": "..."},
        {"title": "14px 以下不描边", "decision": "..."},
    ],
}

SAVED = []
PROFILES = []
MEMBER_CFG = {}


def fake_load_board(project):
    return json.loads(json.dumps(BOARD))


def fake_save_board(data, project=None):
    SAVED.append((project, json.loads(json.dumps(data))))
    # 假看板要跟着"落盘"更新，否则下一次 load_board 看不到刚写的 employee_id
    BOARD.clear()
    BOARD.update(json.loads(json.dumps(data)))


def fake_invite(project, agent_id, name=None, role="", entry_type="guide", entry_target=""):
    BOARD["agents"].append({"id": agent_id, "name": name or agent_id, "role": role,
                            "entry_type": entry_type, "entry": {"type": "dsh"}})
    return True, "ok", ""


def fake_ensure_profile(project, agent_id, name=None, role=None):
    PROFILES.append((project, agent_id, name, role))
    return os.path.join(tmp, "profiles", agent_id)


orig = {k: getattr(S, k) for k in (
    "PROJECTS_ROOT", "load_board", "save_board", "board_lock",
    "invite_agent", "_dsh_ensure_profile", "_dsh_member_cfg",
    "_dsh_save_member_cfg", "_dsh_artifacts_load", "_dsh_log")}

try:
    S.PROJECTS_ROOT = tmp
    S.load_board = fake_load_board
    S.save_board = fake_save_board
    S.board_lock = contextlib.nullcontext()
    S.invite_agent = fake_invite
    S._dsh_ensure_profile = fake_ensure_profile
    S._dsh_member_cfg = lambda project, agent_id: dict(MEMBER_CFG.get(agent_id) or {})
    S._dsh_save_member_cfg = lambda project, agent_id, cfg: MEMBER_CFG.__setitem__(agent_id, cfg)
    S._dsh_log = lambda *a, **k: None   # 测试不要往真实 /tmp/dsh_server.log 里写噪音
    S._dsh_artifacts_load = lambda project: {
        "dsh-1": [{"path": "/p/_src/t255/wall_v4.png", "tool": "write", "ts": "2026-11-01T10:00:00"},
                  {"path": "/p/docs/验收/T255.md", "tool": "edit", "ts": "2026-11-01T11:00:00"}]
    }

    print("== A. 转员工（promote）==")
    okk, msg, rec = S._emp_promote("demo", "dsh-1")
    ok(okk is True, "转员工成功", msg)
    ok(rec and rec["id"] == "emp-dsh-1", "id 是可读 slug", rec and rec["id"])
    ok(rec and rec["origin"] == {"project": "demo", "member_id": "dsh-1"}, "记住出身项目/成员")
    ok(rec and rec["projects"][0]["seat"] == "dsh-1", "项目履历记了席位")

    mem = S._emp_read_memory("emp-dsh-1")
    ok("城墙贴图 v4" in mem and "谋略卡演出" in mem, "记忆含已完成的任务")
    ok("进行中的活" not in mem, "未完成的任务不写入履历")
    ok("别人的活" not in mem, "别人的任务不写入")
    ok("wall_v4.png" in mem and "T255.md" in mem, "记忆含交付物")
    ok("水墨风作为主轴" in mem, "记忆含项目决策摘录")
    ok("不含聊天原文" in mem and "P0" in mem, "标明来源口径（P0 看板事实）")
    ok(rec["memory_bytes"] > 0, "记录记忆大小", rec["memory_bytes"])

    print("\n== B. 只打标记，不覆盖名册 ==")
    ok(SAVED, "保存过看板")
    project_saved, data_saved = SAVED[-1]
    ids = [a.get("id") for a in data_saved["agents"]]
    ok(set(ids) == {"user", "dsh-1"}, "agents 数组没有丢成员", ids)
    linked = [a for a in data_saved["agents"] if a.get("id") == "dsh-1"][0]
    ok(linked.get("employee_id") == "emp-dsh-1", "成员被标上 employee_id")
    ok(linked.get("name") == "DSH-1", "其它字段原样保留")

    print("\n== C. 重复转员工 / 重名 id ==")
    okk2, msg2, _ = S._emp_promote("demo", "dsh-1")
    ok(okk2 is False and "已经是员工" in msg2, "重复转会被拒绝", msg2)
    BOARD["agents"].append({"id": "dsh-1b", "name": "DSH-1", "role": "同名", "entry_type": "dsh"})
    okk3, _, rec3 = S._emp_promote("demo", "dsh-1b")
    ok(okk3 is True and rec3["id"] == "emp-dsh-1-2", "同名员工自动加序号", rec3 and rec3["id"])

    print("\n== D. 指派入项目（assign）==")
    okk4, msg4, info4 = S._emp_assign("other-project", "emp-dsh-1")
    ok(okk4 is True, "指派成功", msg4)
    seat = (info4 or {}).get("seat")
    ok(seat == "DSH-1", "席位 id 用员工名", seat)
    ok(any(a.get("id") == "DSH-1" and a.get("employee_id") == "emp-dsh-1" for a in BOARD["agents"]),
       "席位指向员工档案")
    ok(MEMBER_CFG.get("DSH-1") is not None, "初始化了 dsh 成员配置")
    ok(any(p[1] == "DSH-1" and p[3] == "美工设计总体负责人" for p in PROFILES),
       "生成了专属 profile（带角色）")
    rec5 = S._emp_load("emp-dsh-1")
    ok(len(rec5["projects"]) == 2, "项目履历累加", rec5["projects"])
    ok(rec5["projects"][-1]["project"] == "other-project", "新项目记进去了")

    okk6, msg6, _ = S._emp_assign("other-project", "emp-dsh-1")
    ok(okk6 is False and "已经在本项目" in msg6, "同一项目不重复占席位", msg6)

    print("\n== E. 刷新记忆（distill）==")
    okk7, msg7, info7 = S._emp_distill("other-project", "emp-dsh-1")
    ok(okk7 is True and info7["bytes"] > 0, "席位在 → 刷新成功（即使该项目还没事实）", msg7)
    okk8, msg8, info8 = S._emp_distill("demo", "emp-dsh-1")
    ok(okk8 is True and info8["bytes"] > 0, "在本项目刷新记忆成功", info8)

    print("\n== F. 身份锚定注入 ==")
    block = S._dsh_employee_memory_block({"employee_id": "emp-dsh-1"})
    ok("【员工记忆】" in block and "DSH-1" in block, "锚定块带员工身份")
    ok("仅供判断参考，不是本项目给你的任务" in block, "明确不是本项目任务（防串味）")
    ok("以现在为准" in block, "冲突时以本项目为准")
    ok(S._dsh_employee_memory_block({}) == "", "没有 employee_id → 不注入")
    ok(S._dsh_employee_memory_block({"employee_id": "emp-nope"}) == "", "员工档案不存在 → 不注入")

    long_mem = "## 我是谁\n" + ("甲" * 4000)
    S._emp_write_memory("emp-long", long_mem)
    block2 = S._dsh_employee_memory_block({"employee_id": "emp-long"})
    ok(len(block2) < 2200, "注入有长度上限（1500 字左右）", len(block2))
    ok("记忆已截断" in block2, "截断有说明")

    print("\n== G. 记忆 8 KB 上限 ==")
    size = S._emp_write_memory("emp-big", "乙" * 20000)
    body = S._emp_read_memory("emp-big")
    ok(size <= S.EMP_MEMORY_MAX_BYTES + 60, "超过 8 KB 会截断", size)
    ok("8 KB 上限截断" in body, "截断有说明")

    print("\n== H. 服务端接线 ==")
    src = open(os.path.join(REPO_ROOT, "server.py"), encoding="utf-8").read()
    for needle, label in [
        ('if parsed.path == "/api/employees":', "GET /api/employees 路由"),
        ('if parsed.path == "/api/employees/promote":', "promote 路由"),
        ('if parsed.path == "/api/employees/assign":', "assign 路由"),
        ('if parsed.path == "/api/employees/distill":', "distill 路由"),
        ("dsh_input += _dsh_employee_memory_block(member)", "身份锚定里注入员工记忆"),
    ]:
        ok(needle in src, label)

    html = open(os.path.join(REPO_ROOT, "org_chart.html"), encoding="utf-8").read()
    print("\n== I. 前端（员工库 + 转员工入口）==")
    for needle, label in [
        ('id="btn-employees"', "顶栏有「员工」入口"),
        ('onclick="openEmp()"', "入口打开员工库"),
        ('id="empModal"', "员工库弹窗存在"),
        ('id="empList"', "列表容器存在"),
        ("async function loadEmpList()", "有加载列表函数"),
        ("async function empPromote(memberId)", "有转员工函数"),
        ("async function empAssign(empId)", "有指派函数"),
        ("/api/employees/promote", "转员工走后台接口"),
        ("/api/employees/assign", "指派走后台接口"),
        ("await loadBoard();", "转员工后刷新看板（徽标出现）"),
        ('employee_id', "成员卡认识员工标记"),
        ('.emp-badge', "员工徽标样式"),
    ]:
        ok(needle in html, label)
finally:
    for k, v in orig.items():
        setattr(S, k, v)
    shutil.rmtree(tmp, ignore_errors=True)

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
