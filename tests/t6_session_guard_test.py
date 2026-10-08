#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T6 回归测试：托管会话与工作目录的绑定（v2.20.11）

背景
----
dsh 拒绝恢复「在 A 目录录制、却从 B 目录启动」的会话：

    dsh: session "..." was recorded in "A", not "B"

v2.20.10 把托管 Agent 的 cwd 从 AgentNexus 源码仓库改成了各自项目的数据目录，
于是所有在改造之前录制的会话都变成「目录对不上」。dsh 拿到这种会话会报错退出、
一个字都不产出；旧代码还把它当成「超时但有回复」（ok=True），于是一路静默——
用户看到的现象是「这个 Agent 不理我了」，还要白等 180 秒 watchdog。

本测试锁住两个函数：
  * _dsh_cwd_slug      —— 复刻 dsh 的会话目录命名（路径 → '--a-b--'，非 ASCII → '~XXXX'）
  * _dsh_session_usable —— 起进程之前的预检：这个会话能在当前 cwd 下恢复吗？

用法：python3 tests/t6_session_guard_test.py
"""

import importlib.util
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location("agentnexus_server_t6", os.path.join(ROOT, "server.py"))
server = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = server
_spec.loader.exec_module(server)

PASS = 0
FAIL = 0


def check(name, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print("  [PASS] %s" % name)
    else:
        FAIL += 1
        print("  [FAIL] %s\n         got  = %r\n         want = %r" % (name, got, want))


def main():
    # 用临时会话仓库，测试不依赖、也不污染用户真实的 ~/.dsh/sessions
    tmp = tempfile.mkdtemp(prefix="anx-sid-guard-")
    real_root = server.DSH_SESSIONS_ROOT
    server.DSH_SESSIONS_ROOT = tmp
    try:
        print("=== _dsh_cwd_slug：目录名复刻 ===")
        check("纯 ASCII 路径", server._dsh_cwd_slug("/Users/zames/x/proj"),
              "--Users-zames-x-proj--")
        check("结尾没有多余分隔符", server._dsh_cwd_slug("/a/b/"), "--a-b---")
        check("中文 → ~XXXX（虚拟团队）",
              server._dsh_cwd_slug("/Users/zames/Desktop/Project/虚拟团队"),
              "--Users-zames-Desktop-Project-~865A~62DF~56E2~961F--")
        check("中文混英（Mentor社区）",
              server._dsh_cwd_slug("/Users/zames/Desktop/Project/agent-collab-projects/Mentor社区"),
              "--Users-zames-Desktop-Project-agent-collab-projects-Mentor~793E~533A--")

        print("=== _dsh_session_usable：核心回归 ===")
        OLD = "/Users/zames/Desktop/Project/agent-collab-dev"
        NEW = "/Users/zames/Desktop/Project/agent-collab-projects/三国指挥官"
        SID = "session-aaaa-bbbb"
        os.makedirs(os.path.join(tmp, server._dsh_cwd_slug(OLD), SID), exist_ok=True)
        check("在录制目录下 → 可恢复", server._dsh_session_usable(SID, OLD), True)
        check("换了工作目录 → 不可恢复（v2.20.10 事故的根因）",
              server._dsh_session_usable(SID, NEW), False)

        print("=== 边界：不该误伤 ===")
        check("会话不存在 → 放行（dsh 会新建）",
              server._dsh_session_usable("session-nonexistent", NEW), True)
        check("空 session_id → 放行", server._dsh_session_usable("", NEW), True)
        check("cwd 为 None → 放行（不干预）", server._dsh_session_usable(SID, None), True)
        check("换到无关目录 → 不可恢复", server._dsh_session_usable(SID, "/some/other/dir"), False)
        other = os.path.join(tmp, server._dsh_cwd_slug("/some/other/dir"), "session-cccc")
        os.makedirs(other, exist_ok=True)
        check("各自目录下的不同会话各自有效",
              server._dsh_session_usable("session-cccc", "/some/other/dir"), True)

        print("=== 真实会话仓库复刻（存在才跑）===")
        if os.path.isdir(real_root):
            dirs = [d for d in os.listdir(real_root) if d.startswith("--")]
            bad = []
            for d in dirs:
                inner = d[2:-2]
                if "~" in inner:
                    continue  # 非 ASCII 无法从目录名反推 cwd，跳过
                if server._dsh_cwd_slug("/" + inner.replace("-", "/")) != d:
                    bad.append(d)
            check("真实 ASCII 会话目录全部可复刻（共 %d 个）" % len(dirs), bad, [])
        else:
            print("  [SKIP] %s 不存在" % real_root)
    finally:
        server.DSH_SESSIONS_ROOT = real_root
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    print("结果：%d 通过 / %d 失败" % (PASS, FAIL))
    if FAIL:
        print("存在失败用例 ✗")
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
