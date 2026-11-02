# -*- coding: utf-8 -*-
"""T25（Python 侧）：v2.20.33 模型选择不能「列了但没配」

起因（真实报障）：三国指挥官里把成员模型切到 DeepSeek V4 Flash Vision，它就再也不回话了。
日志给出真相：
  dsh: UNKNOWN_MODEL: pi-ai provider "opencode-go-extra" has no configured model
       "deepseek-v4-flash-vision"
也就是说 AgentNexus 的下拉框列了 provider 里根本不存在的模型，选中后进程空转到
watchdog（180 秒）才死——用户看到的就是「它不回我了」。

这里锁住三件事：
  ① 白名单里不能再出现「profile 没配」的模型（本次删掉的三个不许回来）
  ② _dsh_profile_model_ids 能真的从 profile 读出模型目录
  ③ UNKNOWN_MODEL 在 stderr 里要被立刻认出来并报错，而不是静默等超时
跑法：python3 tests/t25_model_guard_test.py
"""
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


print("== A. 白名单只留真实配置过的模型 ==")
STALE = {"deepseek-v4-flash", "deepseek-v4-flash-vision", "deepseek-v4-flash-vision-experience"}
ok(not (S.DSH_MODEL_WHITELIST & STALE),
   "本次删掉的三个「provider 没配」的模型不许回到白名单", sorted(S.DSH_MODEL_WHITELIST & STALE))
ok("deepseek-v4.1-flash" in S.DSH_MODEL_WHITELIST, "支持看图的 v4.1-flash 仍在白名单")
ok("space-bunny-free" in S.DSH_MODEL_WHITELIST, "免费档仍在白名单")
ok(set(S.DSH_MODEL_LABELS) == S.DSH_MODEL_WHITELIST, "标签与白名单一一对应",
   sorted(set(S.DSH_MODEL_LABELS) ^ S.DSH_MODEL_WHITELIST))

print("\n== B. 真的能从 profile 读出模型目录 ==")
tmp = tempfile.mkdtemp(prefix="t25prof-")
try:
    prof_dir = os.path.join(tmp, ".dsh", "profiles", "testprof")
    os.makedirs(prof_dir)
    with open(os.path.join(prof_dir, "cordis.patch.yml"), "w", encoding="utf-8") as fh:
        fh.write(
            "- id: llm-pi-ai\n"
            "  config:\n"
            "    providers:\n"
            "      go-extra:\n"
            "        models:\n"
            "          - id: alpha-model\n"
            "            name: Alpha\n"
            "          - id: beta-model\n"
            "            name: Beta\n"
            "- id: agent-default-model\n"
            "  config:\n"
            "    provider: go-extra\n"
            "    model: alpha-model\n"
        )
    orig_expand = S.os.path.expanduser
    S.os.path.expanduser = lambda p: p.replace("~", tmp, 1) if p.startswith("~") else p
    try:
        ids = S._dsh_profile_model_ids("testprof")
        ok("alpha-model" in ids and "beta-model" in ids, "抽到两个模型 id", sorted(ids))
        ok("agent-default-model" in ids, "（顺带也会带上非模型 id，无妨——后面还要和白名单取交集）")

        # 交叉验证：白名单里的模型这个 profile 没有 → 下拉框不能显示
        usable = S._dsh_usable_models("testprof")
        ok(usable == set(), "profile 里没有白名单模型时，下拉框为空（而不是照抄白名单）", sorted(usable))

        # 不存在的 profile：退回白名单，不能把界面搞空
        ok(S._dsh_usable_models("no-such-profile") == set(S.DSH_MODEL_WHITELIST),
           "读不到 profile 时退回白名单（不误伤）")
    finally:
        S.os.path.expanduser = orig_expand
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n== C. UNKNOWN_MODEL 要立刻报错，不许静默等超时 ==")
src = open(os.path.join(REPO_ROOT, "server.py"), encoding="utf-8").read()
ok('"unknown_model" in low or "has no configured model" in low' in src,
   "stderr 里认得出 UNKNOWN_MODEL")
ok("这个模型在该 provider 里没有配置" in src, "报错文案说清楚原因和怎么办")
ok("_dsh_profile_model_ids(profile_name)" in src, "启动前按 profile 校验（不是只看白名单）")
ok("provider %s 没有配置模型 %s" in src, "校验失败时给出可用模型列表")
ok('"whitelist": sorted(usable or DSH_MODEL_WHITELIST)' in src, "下拉框只返回真实可用的模型")

print("\n== D. 本机 profile 的实际情况（有就检查，没有就跳过）==")
real = S._dsh_profile_model_ids(S.DSH_PROFILE_TEMPLATE)
if real:
    missing = S.DSH_MODEL_WHITELIST - real
    ok(not missing, "模板 profile 里，白名单的每个模型都真的配了", sorted(missing))
    print("    模板 profile 可用模型：" + "、".join(sorted(S.DSH_MODEL_WHITELIST & real)))
else:
    print("    （本机没有模板 profile，跳过）")

print("\n%d passed, %d failed" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)
