# -*- coding: utf-8 -*-
"""T19：v2.20.25「托管成员的工作目录 = 沙箱唯一可写根」——工作目录解析规则。

起因（2026-10-11 用户需求）：三国指挥官的三名托管成员的权限文件夹只在工具数据目录
（agent-collab-projects/三国指挥官），写不到桌面 Project 下的游戏目录，于是每一次写入
都要零碎地向用户申请临时提权（而 dsh 的审批只给 allowed-once，所以每次都要再问）。

dsh 侧的事实（见 dsh 仓库）：
  - packages/sandbox/sandbox/src/roots.ts 的 writableRoots()：workspace-write 的可写范围
    就是 [policy.workspaceRoot, '/tmp', os.tmpdir()] 三个（canonical 去重），**没有额外可写根**；
  - packages/sandbox/sandbox-policy/src/index.ts 的 resolve()：
    workspaceRoot = session.header.cwd ?? 配置的 workspaceRoot —— 会话 cwd 就是边界。

所以唯一的做法是让成员的 cwd 落在项目自己的外部工作区（auto），本测试锁住这套解析规则。

跑法：python3 tests/t19_workdir_test.py
"""
import importlib.util
import os
import shutil
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
    spec = importlib.util.spec_from_file_location("srv_t19", os.path.join(REPO, "server.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ROOT = tempfile.mkdtemp(prefix="t19_")
# 必须在加载 server.py 之前指好日志与数据根，免得污染真实的 /tmp/dsh_server.log 与数据仓库
os.environ["AGENTNEXUS_DSH_LOG"] = os.path.join(ROOT, "dsh.log")
os.environ["COLLAB_PROJECTS_ROOT"] = ROOT
M = load_server()
M.PROJECTS_ROOT = ROOT
M.DSH_LOG_FILE = os.path.join(ROOT, "dsh.log")
# 别让 load_board 的自动 git 提交动到临时目录之外
M._auto_git_snapshot = lambda *a, **k: None
M._auto_git_commit = lambda *a, **k: None

PROJ = "T19Proj"
PDIR = os.path.join(ROOT, PROJ)
OUTSIDE = os.path.join(ROOT, "game-repo")            # 模拟「桌面 Project/三国指挥官」
INSIDE = os.path.join(PDIR, "workspace")             # 模拟自动创建的项目内 workspace/
MISSING = os.path.join(ROOT, "not-created-yet")


def write_board(workspace=None, extra_info=None):
    os.makedirs(PDIR, exist_ok=True)
    info = {"name": PROJ, "description": "", "created": "2026-10-11T00:00:00+08:00", "version": "1.0"}
    if extra_info:
        info.update(extra_info)
    if workspace is not None:
        info["workspace"] = workspace
    board = {
        "project_info": info,
        "agents": [{"id": "user", "name": "Zames", "role": "项目主导方"},
                   {"id": "dsh-a", "name": "甲", "role": "终端执行", "entry": {"type": "dsh"}}],
        "tasks": [], "messages": [], "decisions": [],
        "protocol": {"mode": "working", "updated_by": "user", "updated_at": ""},
    }
    import json
    with open(os.path.join(PDIR, "collab_board.json"), "w", encoding="utf-8") as f:
        json.dump(board, f, ensure_ascii=False, indent=2)


def write_cfg(workdir=None):
    import json
    cfg = {"dsh": {"model": "deepseek-v4.1-flash", "members": {}}}
    if workdir is not None:
        cfg["dsh"]["workdir"] = workdir
    with open(os.path.join(PDIR, "project_config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


os.makedirs(OUTSIDE, exist_ok=True)
os.makedirs(INSIDE, exist_ok=True)

print("== A. dsh.workdir 缺省与读取 ==")
write_board(workspace=OUTSIDE)
write_cfg()
ck("A1 无 dsh.workdir 时缺省 auto", M._dsh_workdir_mode(PROJ) == "auto", M._dsh_workdir_mode(PROJ))
write_cfg(workdir="data")
ck("A2 读到 data", M._dsh_workdir_mode(PROJ) == "data")
write_cfg(workdir="")
ck("A3 空串仍回 auto", M._dsh_workdir_mode(PROJ) == "auto")
write_cfg(workdir="  ")
ck("A4 纯空白仍回 auto", M._dsh_workdir_mode(PROJ) == "auto")
ck("A5 项目不存在不炸", M._dsh_workdir_mode("NoSuchProject") == "auto")

print("== B. _dsh_run_cwd 解析规则 ==")
write_cfg()
write_board(workspace=OUTSIDE)
ck("B1 auto + 外部工作区 → 用工作区", M._dsh_run_cwd(PROJ) == os.path.realpath(OUTSIDE), M._dsh_run_cwd(PROJ))

write_board(workspace=INSIDE)
ck("B2 auto + 工作区就在数据目录里 → 退回数据目录", M._dsh_run_cwd(PROJ) == os.path.realpath(PDIR), M._dsh_run_cwd(PROJ))

write_cfg(workdir="workspace")
ck("B3 显式 workspace → 即使用数据目录内的子目录也用它", M._dsh_run_cwd(PROJ) == os.path.realpath(INSIDE), M._dsh_run_cwd(PROJ))

write_cfg(workdir="data")
write_board(workspace=OUTSIDE)
ck("B4 显式 data → 忽略外部工作区", M._dsh_run_cwd(PROJ) == os.path.realpath(PDIR), M._dsh_run_cwd(PROJ))

write_cfg(workdir=OUTSIDE)
ck("B5 显式绝对路径 → 用它", M._dsh_run_cwd(PROJ) == os.path.realpath(OUTSIDE))
write_cfg(workdir=MISSING)
ck("B6 显式路径不存在 → 退回数据目录", M._dsh_run_cwd(PROJ) == os.path.realpath(PDIR), M._dsh_run_cwd(PROJ))

write_cfg()
write_board()                                   # 没有 workspace 键
ck("B7 没有 workspace 键 → 数据目录", M._dsh_run_cwd(PROJ) == os.path.realpath(PDIR))
write_board(workspace=MISSING)                  # 声明了但不存在
ck("B8 workspace 指向不存在的目录 → 数据目录", M._dsh_run_cwd(PROJ) == os.path.realpath(PDIR))
write_board(workspace="   ")
ck("B9 workspace 是空白 → 数据目录", M._dsh_run_cwd(PROJ) == os.path.realpath(PDIR))
ck("B10 项目不存在 → None", M._dsh_run_cwd("NoSuchProject") is None)

print("== C. 目录一变，旧会话必须被判为不可用（v2.20.11 机制）==")
SESS = os.path.join(ROOT, "sessions")
M.DSH_SESSIONS_ROOT = SESS
sid = "session-t19-aaaa"
old_home = os.path.join(SESS, M._dsh_cwd_slug(PDIR), sid)
os.makedirs(old_home, exist_ok=True)
write_cfg()
write_board(workspace=OUTSIDE)
ck("C1 录在数据目录的会话在数据目录下可用", M._dsh_session_usable(sid, PDIR) is True)
ck("C2 同一个会话在新工作目录下不可用", M._dsh_session_usable(sid, OUTSIDE) is False)

print("== D. 源码契约 ==")
src = open(os.path.join(REPO, "server.py"), encoding="utf-8").read()
ck("D1 默认值是 auto", 'DSH_WORKDIR_DEFAULT = "auto"' in src)
ck("D2 _dsh_run 仍把 _run_cwd 传给 Popen", "cwd=_run_cwd," in src)
ck("D3 工作目录日志带 workdir 模式", 'dsh 工作目录: %s (workdir=%s)' in src)
ck("D4 文档说明「唯一可写根 / 没有额外可写根」", "没有「额外可写根」" in src)
ck("D5 显式路径不存在时会记日志", '不是已存在目录，退回项目数据目录' in src)

print("== E. 真实项目声明的路径（只读检查，不碰数据仓库）==")
real_board = "/Users/zames/Desktop/Project/agent-collab-projects/三国指挥官/collab_board.json"
if os.path.isfile(real_board):
    import json
    try:
        b = json.load(open(real_board, encoding="utf-8"))
        ws = ((b.get("project_info") or {}).get("workspace") or "").strip()
        ck("E1 三国指挥官声明的工作区是游戏目录", ws == "/Users/zames/Desktop/Project/三国指挥官", ws)
        ck("E2 该目录存在", os.path.isdir(ws))
        ck("E3 它不在工具数据目录内",
           not os.path.realpath(ws).startswith(os.path.realpath("/Users/zames/Desktop/Project/agent-collab-projects") + os.sep))
    except Exception as e:
        ck("E 读真实看板", False, str(e))
else:
    print("  (跳过：读不到真实看板，沙箱限制)")

shutil.rmtree(ROOT, ignore_errors=True)
print("\n%d PASS / %d FAIL" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
