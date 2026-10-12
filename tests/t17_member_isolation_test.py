#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T17 — 托管成员之间不许串话（v2.20.23 回归）

背景（2026-10-11 用户报障）：跟 A 说完话、切到 B，B 会接着 A 的话题回答。
根因全在前端：聊天历史缓存是一个全局数组、流式回复写的是全局的 #dshMsgs，
切卡片时 A 还在流的那一轮就把回复画进了 B 的卡，并且被 push 进 B 的「上文」。

本测试盯两件事：
  1) 服务端不再采用客户端传来的 context，改由 _dsh_context_from_history()
     按成员自己的历史生成；
  2) 前端把「这一轮属于哪个成员」钉死，并在切卡片时断开流。

跑法：python3 tests/t17_member_isolation_test.py
"""
import importlib.util
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SERVER = ROOT / "server.py"
ORG = ROOT / "org_chart.html"
NODE = "/Users/zames/Library/Application Support/DoubaoWork/sandbox_runtime/bases/98670218a5f0d8bc9b9ebf3f70881304/bin/node"

PASS = 0
FAIL = 0


def ok(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✓ %s" % name)
    else:
        FAIL += 1
        print("  ✗ %s %s" % (name, extra))


def load_server(tmp_root):
    os.environ["AGENTNEXUS_DSH_LOG"] = os.path.join(tmp_root, "dsh.log")
    spec = importlib.util.spec_from_file_location("srv_t17", str(SERVER))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.PROJECTS_ROOT = tmp_root
    m.DSH_LOG_FILE = os.path.join(tmp_root, "dsh.log")
    return m


def write_hist(m, project, agent_id, entries):
    p = m._dsh_history_path(project, agent_id)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)


def main():
    tmp = tempfile.mkdtemp(prefix="t17_")
    try:
        m = load_server(tmp)
        proj = "IsoProj"
        os.makedirs(os.path.join(tmp, proj), exist_ok=True)

        print("A. 服务端上下文只取本成员的历史")
        write_hist(m, proj, "A", [
            {"role": "user", "content": "A的私事一"},
            {"role": "assistant", "content": "A的回答一"},
        ])
        write_hist(m, proj, "B", [
            {"role": "user", "content": "B的私事一"},
            {"role": "assistant", "content": "B的回答一"},
        ])
        ctx_a = m._dsh_context_from_history(proj, "A")
        ctx_b = m._dsh_context_from_history(proj, "B")
        ok("A 的上文含 A 的内容", "A的私事一" in ctx_a and "A的回答一" in ctx_a, ctx_a)
        ok("A 的上文不含 B 的内容", "B的私事一" not in ctx_a)
        ok("B 的上文不含 A 的内容", "A的私事一" not in ctx_b and "B的私事一" in ctx_b)
        ok("角色标签为 你 / 终端 Agent", ctx_a.startswith("你：A的私事一") and "终端 Agent：A的回答一" in ctx_a, ctx_a)

        print("B. 上限、截断与脏数据")
        write_hist(m, proj, "C", [
            {"role": "user", "content": "第%d条" % i} for i in range(1, 9)
        ])
        ctx_c = m._dsh_context_from_history(proj, "C")
        ok("只保留最近 4 条", ctx_c.count("\n") == 3 and "第8条" in ctx_c and "第1条" not in ctx_c, ctx_c)
        write_hist(m, proj, "D", [
            {"role": "user", "content": "x" * 900},
            {"role": "system", "content": "系统提示不该进上文"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "短句"},
        ])
        ctx_d = m._dsh_context_from_history(proj, "D")
        ok("超长内容被截断到 400 字", len(ctx_d.split("：", 1)[1].split("\n")[0]) <= 401, ctx_d[:60])
        ok("system 角色不进上文", "系统提示不该进上文" not in ctx_d)
        ok("空内容不进上文", ctx_d.count("：") == 2, ctx_d)
        ok("没有历史时返回空串", m._dsh_context_from_history(proj, "NOPE") == "")
        ok("成员 id 不存在也不崩", m._dsh_context_from_history("NoSuchProj", "A") == "")

        print("C. 服务端真的不再用客户端 context")
        src = SERVER.read_text(encoding="utf-8")
        ok("_handle_dsh_chat 调用 _dsh_context_from_history",
           "context = _dsh_context_from_history(project, agent_id)" in src)
        ok("客户端 context 被记日志忽略",
           "忽略客户端 context（%d 字），改用服务端历史" in src)
        ok("不再把客户端 context 拼进 dsh_input（只有服务端结果参与拼接）",
           'dsh_input = (context + "\\n\\n【现在的新消息】" + message) if context else message' in src)
        ok("审批事件带上成员 id", 'ev["agentId"] = agent_id' in src)

        print("D. 前端把「这一轮属于谁」钉死")
        html = ORG.read_text(encoding="utf-8")
        ok("历史缓存按成员分开（dshCacheByAgent）", "const dshCacheByAgent = {};" in html)
        ok("全局 dshMsgsCache 已彻底移除", "dshMsgsCache" not in html)
        ok("dshHistory() 按 currentDshAgent 取", "return dshCacheByAgent[currentDshAgent] || [];" in html)
        ok("loadDshHistory 有陈旧响应守卫", "if (who !== currentDshAgent) return;" in html)
        ok("dshAppend 支持成员参数并拦截他人回复",
           "function dshAppend(role, content, agentId)" in html and "if (agentId && agentId !== currentDshAgent) return;" in html)
        ok("appendThinking 同样拦截", "function appendThinking(text, agentId)" in html)
        ok("sendToTerminal 固定 targetAgent", "const targetAgent = currentDshAgent || 'terminal';" in html)
        # v2.20.24：actFor 被秒表助手取代（dshActivityShow/Hide 自带成员守卫），
        # 只保留 boxFor 做「回复只写进发出请求的那张卡」的守卫。
        ok("boxFor 只在同一张卡上写", "const boxFor = () =>" in html)
        ok("活动行助手也带成员守卫",
           "function dshActivityShow(text, agentId)" in html
           and "if (agentId && agentId !== currentDshAgent) return;" in html)
        ok("请求带 AbortController 信号", "signal: dshChatCtrl.signal" in html)
        ok("切卡片/关卡片会断开流",
           "function dshAbortStream()" in html and "dshAbortStream();   // v2.20.23：换卡片就断掉" in html
           and "function closePerson() { dshAbortStream();" in html)
        ok("AbortError 不当故障显示", "if (e && e.name === 'AbortError') return;" in html)
        ok("回复只进自己那份缓存",
           "(dshCacheByAgent[targetAgent] = dshCacheByAgent[targetAgent] || []).push({ role: 'assistant'" in html)
        ok("审批卡不许画到别人卡里", "if (ev.agentId && currentDshAgent && ev.agentId !== currentDshAgent) return;" in html)

        print("E. dshAppend 行为（假 DOM，跑真实源码）")
        m_ap = re.search(r"function dshAppend\(role, content, agentId\) \{.*?\n\}", html, re.S)
        ok("抽到 dshAppend 真实源码", bool(m_ap))
        if m_ap and os.path.exists(NODE):
            js = """
const inserts = [];
const box = { insertAdjacentHTML: (p, h) => inserts.push(h), scrollTop: 0, scrollHeight: 10 };
const document = { getElementById: (id) => (id === 'dshMsgs' ? box : null) };
function dshMsgHtml(role, content) { return role + ':' + content; }
let currentDshAgent = 'A';
%s
dshAppend('assistant', '给A的', 'A');
dshAppend('assistant', '给B的', 'B');   // 当前卡是 A，必须被丢弃
dshAppend('assistant', '无归属');
currentDshAgent = 'B';
dshAppend('assistant', '给B的2', 'B');
console.log(JSON.stringify(inserts));
""" % m_ap.group(0)
            path = os.path.join(tmp, "append_probe.js")
            with open(path, "w", encoding="utf-8") as f:
                f.write(js)
            out = subprocess.run([NODE, path], capture_output=True, text=True)
            try:
                got = json.loads((out.stdout or "").strip().splitlines()[-1])
            except Exception:
                got = None
            ok("A 的回复画在 A 的卡上", got is not None and "assistant:给A的" in got, str(got) + out.stderr[:200])
            ok("B 的回复在 A 的卡上被丢弃", got is not None and "assistant:给B的" not in got)
            ok("不带成员的调用保持原行为", got is not None and "assistant:无归属" in got)
            ok("切到 B 后 B 的回复正常显示", got is not None and "assistant:给B的2" in got)

        print("\nT17 结果：%d 通过 / %d 失败" % (PASS, FAIL))
        return 1 if FAIL else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
