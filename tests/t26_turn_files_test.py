# -*- coding: utf-8 -*-
"""T26：v2.20.34 本轮产出扫描（补 bash 生成的文件）

背景（真实数据）：交付物登记只认 write/edit/present，而美术资产是「bash 跑 node 脚本」
生成的——三国指挥官登记表 146 条、0 张图片，于是「回复下面挂图」永远看不到图。

这里锁住兜底扫描的行为：
  · 只认本轮时间窗（mtime >= since_epoch）
  · 只认交付类扩展名（图片/文档/音视频），代码与日志不列
  · 跳过 .git / node_modules / _inbox 等噪音目录
  · 有上限（默认 8），按 mtime 新→旧
  · 和交付物登记去重（同一个文件只出现一次）
跑法：python3 tests/t26_turn_files_test.py
"""
import os
import shutil
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


tmp = tempfile.mkdtemp(prefix="t26scan-")
now = time.time()


def mk(rel, mtime_delta=0.0, content=b"x"):
    p = os.path.join(tmp, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True) if os.path.dirname(p) else None
    with open(p, "wb") as fh:
        fh.write(content)
    t = now - mtime_delta
    os.utime(p, (t, t))
    return p


try:
    recent_img = mk("art/new_shot.png", 5)
    recent_md = mk("docs/report.md", 0.2)   # 故意放最新，否则会被 12 张批量图挤出 top-8
    old_img = mk("art/old_shot.png", 3600)          # 一小时前 → 不属于本轮
    recent_code = mk("engine/main.js", 5)            # 代码：不列（登记表本来就有）
    recent_log = mk("logs/run.txt", 5)               # 文本日志：不列
    mk(".git/objects/xx.png", 5)                     # 版本库噪音
    mk("node_modules/pkg/banner.png", 5)             # 依赖噪音
    mk("_inbox/uploaded.png", 5)                     # 用户传进来的，不算本轮产出
    for i in range(12):                              # 12 张新图，用来验上限
        mk("art/batch_%02d.png" % i, 1 + i * 0.1)

    orig_cwd = S._dsh_run_cwd
    S._dsh_run_cwd = lambda project: tmp
    try:
        print("== A. 时间窗与扩展名 ==")
        got = S._dsh_turn_files("proj", now - 60, limit=50)
        names = [os.path.basename(f["path"]) for f in got]
        ok("new_shot.png" in names, "本轮新图被找到")
        ok("report.md" in names, "本轮文档被找到")
        ok("old_shot.png" not in names, "一小时前的旧图不算本轮")
        ok("main.js" not in names, "代码文件不列（登记表负责）")
        ok("run.txt" not in names, "文本日志不列")
        ok(not any(".git" in f["path"] for f in got), "跳过 .git")
        ok(not any("node_modules" in f["path"] for f in got), "跳过 node_modules")
        ok(not any("_inbox" in f["path"] for f in got), "跳过 _inbox（用户上传的不算产出）")
        ok(names == sorted(names, key=lambda n: 0) or True, "（排序后面单独验）")

        print("\n== B. 排序与上限 ==")
        ok(got == sorted(got, key=lambda f: f["mtime"], reverse=True), "按 mtime 新→旧")
        capped = S._dsh_turn_files("proj", now - 60)
        ok(len(capped) == S.DSH_TURN_SCAN_MAX_FILES,
           "默认上限 %d 个" % S.DSH_TURN_SCAN_MAX_FILES, len(capped))
        ok(all(f["size"] > 0 for f in capped), "带文件大小（前端显示用）")

        print("\n== C. 和交付物登记合并去重 ==")
        orig_load = S._dsh_artifacts_load
        S._dsh_artifacts_load = lambda project: {
            "A": [{"path": recent_img, "tool": "write",
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 5)), "count": 1}]
        }
        try:
            since_s = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 60))
            merged = S._dsh_turn_attachments("proj", "A", since_s, now - 60)
            same = [a for a in merged if os.path.realpath(a["path"]) == os.path.realpath(recent_img)]
            ok(len(same) == 1, "登记过的文件只出现一次（扫描不重复挂）", len(same))
            ok(same and same[0]["tool"] == "write", "保留登记表的来源（write），不被「生成」覆盖")
            ok(any(os.path.basename(a["path"]) == "report.md" for a in merged),
               "未登记的文档靠扫描补上")

            print("\n== D. 没有 epoch 时退回只看登记表 ==")
            only_reg = S._dsh_turn_attachments("proj", "A", since_s)
            ok(len(only_reg) == 1 and only_reg[0]["tool"] == "write",
               "不传 since_epoch 就只返回登记条目（向后兼容）", len(only_reg))
        finally:
            S._dsh_artifacts_load = orig_load

        print("\n== E. 边界 ==")
        ok(S._dsh_turn_files("proj", 0) == [], "没有时间窗 → 空")
        ok(S._dsh_turn_files("", now) == [], "没有项目 → 空")
        S._dsh_run_cwd = lambda project: "/nonexistent-dir-xyz"
        ok(S._dsh_turn_files("proj", now) == [], "工作目录不存在 → 空，不抛错")
    finally:
        S._dsh_run_cwd = orig_cwd

    print("\n== F. 服务端接线 ==")
    src = open(os.path.join(REPO_ROOT, "server.py"), encoding="utf-8").read()
    ok("_turn_since_epoch = time.time() - 1" in src, "聊天路径记了 epoch 时间窗")
    ok("_dsh_turn_attachments(project, agent_id, _turn_since, _turn_since_epoch)" in src,
       "聊天三处都带上 epoch")
    ok("_aw_since_epoch" in src, "自动唤醒路径也记了（唤醒回复同样挂产出）")
    ok("DSH_TURN_SCAN_EXT" in src and "DSH_TURN_SCAN_PRUNE" in src, "白名单/黑名单可维护")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
