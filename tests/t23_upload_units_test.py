# -*- coding: utf-8 -*-
"""T23（Python 侧）：v2.20.31 附件落盘 / 本轮交付物的纯逻辑

JS 那边（t23_attachments_test.js）验的是接线与渲染；这里跑真实函数：
  · _dsh_safe_upload_name —— 路径穿越、无扩展名按 mime 补、超长截断
  · _dsh_turn_attachments —— time window 过滤、去重、跳过已删除文件
跑法：python3 tests/t23_upload_units_test.py
"""
import os
import sys
import tempfile
import time

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


print("== A. 文件名清洗（防路径穿越）==")
n = S._dsh_safe_upload_name("../../etc/passwd", "")
ok("/" not in n and "\\" not in n, "去掉路径分隔符", n)
ok(n.startswith("passwd"), "保留可读主体（无扩展名时补 .bin）", n)
ok(not n.startswith("."), "不以点开头", n)

n = S._dsh_safe_upload_name("", "image/png")
ok(n.endswith(".png"), "空名字按 mime 补扩展名", n)

n = S._dsh_safe_upload_name("shot", "image/jpeg")
ok(n.endswith(".jpg"), "jpeg → .jpg", n)

n = S._dsh_safe_upload_name("设计稿 v2.PNG", "")
ok("设计稿" in n and n.lower().endswith(".png"), "中文名保留、扩展名保留", n)

long_name = "a" * 300 + ".png"
n = S._dsh_safe_upload_name(long_name, "")
ok(len(n) <= 120 and n.endswith(".png"), "超长截断但仍保留扩展名", len(n))

n = S._dsh_safe_upload_name("a b/c:d*e?.md", "")
ok(":" not in n and "*" not in n and "?" not in n and "/" not in n, "非法字符被替换", n)

print("\n== B. 本轮交付物（ts 窗口 / 去重 / 缺文件）==")
tmp = tempfile.mkdtemp(prefix="t23att-")
f_old = os.path.join(tmp, "old.png")
f_new = os.path.join(tmp, "new.png")
f_gone = os.path.join(tmp, "gone.png")
for p in (f_old, f_new):
    with open(p, "wb") as fh:
        fh.write(b"x" * 10)

fake = {
    "A": [
        {"path": f_old, "tool": "write", "ts": "2026-01-01T10:00:00", "count": 1},
        {"path": f_new, "tool": "edit", "ts": "2026-01-01T12:00:00", "count": 1},
        {"path": f_new, "tool": "edit", "ts": "2026-01-01T12:00:01", "count": 2},   # 同路径去重
        {"path": f_gone, "tool": "write", "ts": "2026-01-01T12:05:00", "count": 1},  # 文件已删
    ]
}
orig = S._dsh_artifacts_load
S._dsh_artifacts_load = lambda project: fake
try:
    got = S._dsh_turn_attachments("proj", "A", "2026-01-01T11:00:00")
    ok(len(got) == 1, "只留本轮写过的（旧的/删掉的都排除）", [g["path"] for g in got])
    ok(got and got[0]["path"] == f_new, "留下的正是新写的那个")
    ok(got and got[0]["tool"] == "edit", "带上是哪个工具写的")
    ok(got and got[0]["size"] == 10, "带文件大小（前端显示用）")

    got2 = S._dsh_turn_attachments("proj", "A", "2026-01-01T13:00:00")
    ok(got2 == [], "窗口之后没有产出 → 空列表")

    ok(S._dsh_turn_attachments("proj", "nobody", "2026-01-01T00:00:00") == [],
       "没有登记的成员 → 空列表")
    ok(S._dsh_turn_attachments("proj", "A", "") == [], "没有时间窗 → 空列表")
finally:
    S._dsh_artifacts_load = orig

print("\n== C. 上限 ===")
ok(S.DSH_UPLOAD_MAX_BYTES == 15 * 1024 * 1024, "上传上限 15 MB", S.DSH_UPLOAD_MAX_BYTES)

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
