# -*- coding: utf-8 -*-
"""T28：v2.20.36 两个老功能的修复

① **删除成员 / 改名 点了没反应**（真实报障）
   看板里混进了字符串 id（12 个）和 None id（3 个），而旧代码用
   `max([m.get("id", 0) ...])` 直接比较 → str 与 int 混比较抛 TypeError
   → 请求处理器崩掉 → 连接被重置 → 前端 `await resp.json()` 炸掉且不弹提示。
   修法：统一走 `_next_message_id()`（只取能转 int 的 id）。

② **交付物点开预览是一片空白**（真实报障）
   附件里的文件是「本轮扫描」找出来的，没进交付物登记表；而预览接口只放行
   登记过的路径 → 403 JSON → 新标签页看着就是空的。
   修法：扫描到的文件顺手登记 + 前端改成页内预览（iframe），不再开新标签页。

跑法：python3 tests/t28_fixes_test.py
"""
import os
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


print("== A. 先复现事故现场：旧写法确实会炸 ==")
board = {"messages": [
    {"id": 1}, {"id": "747"}, {"id": None}, {"id": "1319"}, {"id": 5}, {"id": "x"},
]}
threw = False
try:
    max([m.get("id", 0) for m in board.get("messages", [])], default=0)
except TypeError as e:
    threw = True
    why = str(e)
ok(threw, "旧写法 max() 在混合 id 上抛 TypeError（这就是连接被重置的原因）", why if threw else "")

print("\n== B. 新的 _next_message_id ==")
ok(S._next_message_id(board) == 1320,
   "混合 id（1,'747',None,'1319',5,'x'）→ 取有效最大值 1319 + 1", S._next_message_id(board))
ok(S._next_message_id({"messages": []}) == 1, "空看板 → 1", S._next_message_id({"messages": []}))
ok(S._next_message_id({"messages": [{"id": "3"}, {"id": "9"}]}) == 10,
   "纯字符串数字 id 也能算对 → 10", S._next_message_id({"messages": [{"id": "3"}, {"id": "9"}]}))
ok(S._next_message_id({"messages": ["垃圾", None, {"id": 2}]}) == 3,
   "非 dict / 无效条目被跳过", S._next_message_id({"messages": ["垃圾", None, {"id": 2}]}))
ok(S._next_message_id({}) == 1, "没有 messages 字段也不炸")

print("\n== C. 两处裸 max 已清掉 ==")
src = open(os.path.join(REPO_ROOT, "server.py"), encoding="utf-8").read()
ok('max([m.get("id", 0) for m in board.get("messages", [])], default=0)' not in src,
   "源码里不再有裸 max（会炸的那种）")
ok(src.count('"id": _next_message_id(board),') >= 2, "移除成员/改名两条系统消息都走新函数",
   src.count('"id": _next_message_id(board),'))

print("\n== D. 扫描到的交付物要登记（否则预览 403）==")
ok('_dsh_artifact_record(project, agent_id, "生成", scanned)' in src,
   "本轮扫描出的文件写进交付物登记")
ok("if scanned:" in src, "只在实际扫到东西时登记")

print("\n== E. 前端改成页内预览 ==")
html = open(os.path.join(REPO_ROOT, "org_chart.html"), encoding="utf-8").read()
for needle, label in [
    ('id="attModal"', "有预览弹窗"),
    ('id="attFrame"', "有 iframe 容器"),
    ('id="attOpenNew"', "保留「新窗口打开」兜底"),
    ("function dshOpenAtt(url, name)", "有打开预览函数"),
    ("function dshCloseAtt()", "有关闭函数"),
    ('onclick="dshOpenAtt(this.href, this.dataset.name); return false;"', "附件点击走页内预览"),
]:
    ok(needle in html, label)
ok(html.count('onclick="dshOpenAtt(this.href, this.dataset.name); return false;"') == 2,
   "图片与文件条两种附件都改了", html.count('onclick="dshOpenAtt(this.href, this.dataset.name); return false;"'))
ok('class="dsh-att img" href="' + "' + url + '" + '" target="_blank"' not in html,
   "附件不再默认新标签页打开")

print("\n== F. 版本号 ==")
ok(bool(__import__("re").search(r"const BUILD_VERSION = 'v2\.\d+\.\d+'", html)), "页面版本号格式正确")

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
