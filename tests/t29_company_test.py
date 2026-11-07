# -*- coding: utf-8 -*-
"""T29：v2.20.37 公司视图 —— 员工的公司归属

设计：层级由「谁向谁汇报」表达（reports_to）；部门是扁平标签（一人一个、不嵌套、
不设负责人字段）；没有汇报关系时前端平铺卡片。

这里锁住服务端模型与校验：
  · company.json 的读写与默认值（文件不存在时给默认，不炸）
  · 部门整表提交：自动补 id、去空名、上限、改名保持 id
  · 员工归属：部门必须存在、不能汇报给自己、不能成环、可清空
  · 删部门后员工身上的 department_id 自动清掉（不留悬空引用）
跑法：python3 tests/t29_company_test.py
"""
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


tmp = tempfile.mkdtemp(prefix="t29company-")
orig_root = S.PROJECTS_ROOT
orig_log = S._dsh_log
orig_body = S._emp_read_json_body
try:
    S.PROJECTS_ROOT = tmp
    S._dsh_log = lambda *a, **k: None

    print("== A. 默认值与读写 ==")
    c = S._company_load()
    ok(c == {"name": "我的公司", "departments": []}, "没有 company.json → 默认公司名 + 空部门", c)
    ok(S._company_save({"name": "烽禾升", "departments": [{"id": "dept-1", "name": "设计部"}]}), "保存成功")
    c = S._company_load()
    ok(c["name"] == "烽禾升" and c["departments"][0]["name"] == "设计部", "读回来一致", c)

    print("\n== B. 部门整表校验 ==")
    depts, err = S._company_normalize_departments(
        [{"id": "", "name": "技术部"}, {"name": "   "}, {"id": "dept-9", "name": "美术部"},
         {"id": "dept-9", "name": "重名 id"}, "垃圾"])
    ok(err is None, "没有报错")
    ok(len(depts) == 3, "空名/非对象被丢掉，剩 3 个", depts)
    ok(depts[0]["id"].startswith("dept-"), "缺 id 自动补", depts[0])
    ok(depts[2]["id"].startswith("dept-"), "重复 id 重新分配", depts[2])
    ok(depts[1]["id"] == "dept-9" and depts[1]["name"] == "美术部", "显式 id 保留", depts[1])
    too_many = [{"name": "部%d" % i} for i in range(S.COMPANY_MAX_DEPTS + 5)]
    _, err2 = S._company_normalize_departments(too_many)
    ok(bool(err2), "超过上限会拒绝", err2)

    print("\n== C. 员工归属校验 ==")
    S._company_save({"name": "烽禾升", "departments": [
        {"id": "dept-1", "name": "设计部"}, {"id": "dept-2", "name": "技术部"}]})
    for eid, name in [("emp-a", "甲"), ("emp-b", "乙"), ("emp-c", "丙")]:
        S._emp_save({"id": eid, "name": name, "role": "员工", "projects": []})

    ok(S._company_update_employee("emp-a", department_id="dept-1")[0], "分到设计部")
    ok(S._emp_load("emp-a")["department_id"] == "dept-1", "部门落盘")
    ok(not S._company_update_employee("emp-a", department_id="dept-nope")[0], "不存在的部门被拒绝")
    ok(not S._company_update_employee("emp-zzz", department_id="dept-1")[0], "不存在的员工被拒绝")

    ok(S._company_update_employee("emp-b", reports_to="emp-a")[0], "乙向甲汇报")
    ok(S._company_update_employee("emp-c", reports_to="emp-b")[0], "丙向乙汇报")
    ok(not S._company_update_employee("emp-a", reports_to="emp-c")[0], "成环被拒绝（甲→丙→乙→甲）")
    ok(not S._company_update_employee("emp-a", reports_to="emp-a")[0], "不能汇报给自己")
    ok(not S._company_update_employee("emp-a", reports_to="emp-zzz")[0], "汇报对象必须是员工")

    ok(S._company_update_employee("emp-b", reports_to="")[0], "可以取消汇报（回到顶层）")
    ok("reports_to" not in S._emp_load("emp-b"), "取消后字段被清掉")

    print("\n== D. 一个部门 / 部门必填与否 ==")
    ok(S._company_update_employee("emp-a", department_id="")[0], "可以退回「未分部门」")
    ok("department_id" not in S._emp_load("emp-a"), "清空后字段被清掉")

    print("\n== E. 删部门后清掉悬空引用 ==")
    S._company_update_employee("emp-a", department_id="dept-1")
    S._company_update_employee("emp-c", department_id="dept-1")
    ok(S._emp_load("emp-c")["department_id"] == "dept-1", "先确认两人在设计部")

    class FakeHandler:
        command = "POST"

        def __init__(self, body):
            self.body = body
            self.out = None

        def _send_json(self, obj, status=200):
            self.out = (obj, status)

    S._emp_read_json_body = lambda h: h.body

    h = FakeHandler({"departments": [{"id": "dept-2", "name": "技术部"}]})
    S._handle_company(h)
    ok(h.out and h.out[0].get("ok"), "提交新部门表成功", h.out)
    ok("department_id" not in S._emp_load("emp-a"), "甲在设计部被删后自动清空")
    ok("department_id" not in S._emp_load("emp-c"), "丙同样被清空")
    ok([d["id"] for d in S._company_load()["departments"]] == ["dept-2"], "只剩技术部")

    print("\n== F. GET /api/company 输出 ==")
    S._emp_save({"id": "emp-d", "name": "丁", "role": "员工", "department_id": "dept-2",
                 "reports_to": "", "projects": [{"project": "demo", "seat": "丁"}]})
    h2 = FakeHandler({})
    h2.command = "GET"
    S._handle_company(h2)
    out = (h2.out or ({}, 0))[0]
    ok(out.get("ok"), "GET 成功")
    ok(out["company"]["departments"][0]["name"] == "技术部", "带公司/部门")
    ids = [e["id"] for e in out.get("employees") or []]
    ok("emp-d" in ids, "带员工列表", ids)
    d = [e for e in out["employees"] if e["id"] == "emp-d"][0]
    ok(d["department_id"] == "dept-2", "带部门归属")
    ok("memory_bytes" in d, "带记忆大小（公司卡片要显示）")

    print("\n== G. 服务端接线 ==")
    src = open(os.path.join(REPO_ROOT, "server.py"), encoding="utf-8").read()
    for needle, label in [
        ('if parsed.path == "/api/company":', "GET/POST /api/company 路由"),
        ('if parsed.path == "/api/company/employee":', "员工归属路由"),
        ("def _company_reports_cycle(", "有防环检查"),
        ("def _company_normalize_departments(", "有部门校验"),
    ]:
        ok(needle in src, label)
finally:
    S.PROJECTS_ROOT = orig_root
    S._dsh_log = orig_log
    S._emp_read_json_body = orig_body
    shutil.rmtree(tmp, ignore_errors=True)

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
