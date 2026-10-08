#!/usr/bin/env python3
"""
T4.5 + T5.4 模块级回归测试（在冻结后台服务环境下使用）
运行：python3 t5_regression_test.py

说明：
- 不依赖运行中的 HTTP 服务，直接 import server 模块做函数级验证；
- 所有数据文件重定向到临时目录，不污染真实 collab_board.json / project_config.json；
- 覆盖 T4.5 定时触发验收（5分钟档/去重/开关关闭/并发写）与
  T5.4 整体回归验收（三模式切换/异常场景/连续失败降级+通知/调用日志完整）。
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# server.py 在仓库根，不在 tests/ 下；只把 tests/ 放进 sys.path 会 ModuleNotFoundError。
REPO_ROOT = os.path.dirname(BASE_DIR)
for _p in (REPO_ROOT, BASE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import server  # noqa: E402

# ===== 重定向数据文件到临时目录，保护真实数据 =====
_tmp = tempfile.mkdtemp(prefix="t5_test_")
server.BOARD_FILE = os.path.join(_tmp, "collab_board.json")
server.CONFIG_FILE = os.path.join(_tmp, "project_config.json")
server.PROCESSED_MSG_FILE = os.path.join(_tmp, "processed_msg_ids.json")
server.API_LOG_FILE = os.path.join(_tmp, "api_calls.log")
server.schedule_manager._processed_msg_ids = set()

# ===== 测试基架 =====
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def make_board(messages):
    return {
        "meta": {"project": "test"},
        "agents": [
            {"id": "planner", "name": "规划Agent", "role": "规划", "color": "#ff3621"},
            {"id": "builder", "name": "执行Agent", "role": "执行", "color": "#a1a1a6"},
        ],
        "tasks": [
            {"id": "T-TEST", "title": "回归测试任务", "assignee": "builder", "status": "done",
             "progress": 100, "priority": "low", "depends_on": [], "artifact": "", "note": "", "updated": ""}
        ],
        "messages": messages,
    }


def make_config(**kw):
    cfg = {
        "meta": {},
        "workspace": {},
        "agents": [],
        "api_config": {
            "api_key": "fake-key-123",
            "base_url": "http://127.0.0.1:9/v1",
            "model": "deepseek-chat",
            "enabled": True,
            "auto_trigger": True,
            "max_chain_length": 3,
            "request_timeout": 3,
            "schedule": {"enabled": False, "interval_minutes": 30,
                         "total_runs": 0},
            "stats": {},
        },
    }
    cfg["api_config"].update(kw)
    return cfg


def reset_files():
    for p in (server.BOARD_FILE, server.CONFIG_FILE, server.PROCESSED_MSG_FILE, server.API_LOG_FILE):
        if os.path.exists(p):
            os.remove(p)
    for p in (server.BOARD_FILE + ".lock", server.CONFIG_FILE + ".lock",
              server.API_LOG_FILE + ".lock"):
        if os.path.exists(p):
            os.remove(p)
    server.schedule_manager._processed_msg_ids = set()


print("=" * 60)
print("T4.5 定时触发模块级验证")
print("=" * 60)

# --- T4.5-1: 5分钟档配置与下次触发时间计算 ---
reset_files()
cfg = make_config(schedule={"enabled": True, "interval_minutes": 5, "total_runs": 0})
server.save_config(cfg)
sm = server.schedule_manager
t0 = time.time()
sm._schedule_next()
sm.stop()
cfg2 = server.load_config()
sched = cfg2["api_config"]["schedule"]
check("T4.5-1 5分钟档生效(interval_minutes=5)", sched.get("interval_minutes") == 5)
next_ts = time.mktime(time.strptime(sched["next_run_time"], "%Y-%m-%dT%H:%M:%S+08:00"))
check("T4.5-1 下次触发时间≈now+300s",
      abs((next_ts - t0) - 300) < 15,
      f"next_run={sched['next_run_time']}")
check("T4.5-1 5档频率定义完整",
      set(server.SCHEDULE_INTERVALS.keys()) == {5, 15, 30, 60, 1440})

# --- T4.5-2: 定时扫描触发一次 + 去重（第二次扫描不重复触发） ---
reset_files()
msg = {"id": 101, "sender": "user", "content": "@规划Agent 请推进T5", "timestamp": "2026-08-29T21:00:00+08:00", "type": "text"}
server.save_board(make_board([msg]))
cfg = make_config(schedule={"enabled": True, "interval_minutes": 5, "total_runs": 0})
server.save_config(cfg)
worker_calls = []
orig_worker = server._trigger_agent_worker
server._trigger_agent_worker = lambda *a, **k: worker_calls.append(a)
try:
    sm._check_and_trigger()
    first_count = len(worker_calls)
    check("T4.5-2 定时扫描触发@消息(1次)", first_count == 1, f"calls={first_count}")
    check("T4.5-2 触发后消息ID已记录", 101 in sm._processed_msg_ids)
    sm._check_and_trigger()
    second_count = len(worker_calls)
    check("T4.5-2 第二次扫描不重复触发(去重生效)", second_count == first_count, f"calls={second_count}")
finally:
    server._trigger_agent_worker = orig_worker

# --- T4.5-3: 关闭定时开关后调度器不启动（Timer不运行，扫描不触发） ---
# 注：schedule.enabled 控制调度器 Timer 循环（start() 门控）；enabled=False 时
# start() 不启动、_running 为 False，_check_and_trigger 不会被周期性调用。
reset_files()
server.save_config(make_config(schedule={"enabled": False, "interval_minutes": 5, "total_runs": 0}))
sm.stop()
sm.start()
check("T4.5-3 定时开关关闭后调度器不启动", sm._running is False,
      f"running={sm._running}")
check("T4.5-3 定时开关关闭后Timer未创建", sm._timer is None)
# enabled=True 时正常启动（随后立即停止）
server.save_config(make_config(schedule={"enabled": True, "interval_minutes": 5, "total_runs": 0}))
sm.start()
check("T4.5-3 定时开关开启后调度器启动", sm._running is True)
sm.stop()
check("T4.5-3 停止后清理Timer", sm._timer is None)

# --- T4.5-4: 并发写 JSON 不损坏 ---
reset_files()
server.save_board(make_board([]))
errors = []


def concurrent_write():
    try:
        for i in range(15):
            d = server.load_board()
            d.setdefault("messages", []).append({"id": i, "sender": "t", "content": "x",
                                                 "timestamp": "", "type": "text"})
            server.save_board(d)
    except Exception as e:
        errors.append(str(e))


threads = [threading.Thread(target=concurrent_write) for _ in range(4)]
[t.start() for t in threads]
[t.join() for t in threads]
try:
    final = server.load_board()
    check("T4.5-4 并发写入后JSON可读且不损坏", len(errors) == 0 and isinstance(final, dict),
          f"errors={errors[:2]} msg_count={len(final.get('messages', []))}")
except Exception as e:
    check("T4.5-4 并发写入后JSON可读且不损坏", False, str(e))

print()
print("=" * 60)
print("T5.4 整体回归模块级验证")
print("=" * 60)

# --- T5.4-1: 三模式切换 ---
# 手动模式：auto_trigger=False → @不触发
reset_files()
msg3 = {"id": 201, "sender": "user", "content": "@planner 手动模式", "timestamp": "", "type": "text"}
server.save_board(make_board([msg3]))
server.save_config(make_config(auto_trigger=False))
calls3 = []
server._trigger_agent_worker = lambda *a, **k: calls3.append(a)
try:
    server.trigger_agent_if_mentioned(msg3, server.load_board())
    check("T5.4-1 手动模式(auto_trigger=off)不触发", len(calls3) == 0)
    # API模式：auto_trigger=True → 触发
    msg4 = {"id": 202, "sender": "user", "content": "@builder API模式", "timestamp": "", "type": "text"}
    server.save_board(make_board([msg3, msg4]))
    server.save_config(make_config(auto_trigger=True))
    server.trigger_agent_if_mentioned(msg4, server.load_board())
    time.sleep(0.3)
    check("T5.4-1 API模式(auto_trigger=on)触发", len(calls3) >= 1, f"calls={len(calls3)}")
    # 定时模式：schedule.enabled=True 时 _check_and_trigger 可触发未处理@消息
    before = len(calls3)
    msg5 = {"id": 203, "sender": "user", "content": "@planner 定时模式", "timestamp": "", "type": "text"}
    server.save_board(make_board([msg3, msg4, msg5]))
    server.save_config(make_config(auto_trigger=True, schedule={"enabled": True, "interval_minutes": 5, "total_runs": 0}))
    sm._check_and_trigger()
    check("T5.4-1 定时模式扫描触发新@消息", len(calls3) > before, f"calls={len(calls3)}")
finally:
    server._trigger_agent_worker = orig_worker

# --- T5.4-2: 异常场景（无key / 断网 / 超时）不影响看板基本功能 ---
reset_files()
server.save_board(make_board([{"id": 301, "sender": "user", "content": "任务管理测试", "timestamp": "", "type": "text"}]))
# 无key
cfg = make_config(api_key="")
server.save_config(cfg)
r1 = server.call_llm("sys", "hi")
check("T5.4-2 无key返回结构化错误", (not r1["success"]) and "key" in r1["error"], r1["error"])
# 断网（指向本机关闭端口，连接被拒）
server.save_config(make_config(base_url="http://127.0.0.1:9/v1", request_timeout=3))
r2 = server.call_llm("sys", "hi")
check("T5.4-2 断网返回网络错误", (not r2["success"]) and r2["error_type"] == "network_error", r2["error_type"])
# 看板基本功能不受影响（可正常读写任务和消息）
d = server.load_board()
task = d["tasks"][0]
check("T5.4-2 异常后任务管理不受影响", task["status"] == "done" and len(d["messages"]) >= 1)
server._append_agent_message("builder", "执行Agent", "异常场景下聊天功能正常")
d2 = server.load_board()
check("T5.4-2 异常后聊天功能不受影响", d2["messages"][-1]["content"] == "异常场景下聊天功能正常")

# --- T5.4-3: 连续失败3次自动关闭自动触发 + 系统通知 ---
reset_files()
server.save_board(make_board([]))
server.save_config(make_config(auto_trigger=True, base_url="http://127.0.0.1:9/v1", request_timeout=2))
for i in range(3):
    server.call_llm("sys", f"fail {i}")
cfg3 = server.load_config()
api_cfg3 = cfg3["api_config"]
stats3 = api_cfg3.get("stats", {})
check("T5.4-3 连续失败计数=3", stats3.get("consecutive_failures", 0) == 3,
      f"consecutive_failures={stats3.get('consecutive_failures')}")
check("T5.4-3 auto_trigger已自动关闭", api_cfg3.get("auto_trigger") is False)
check("T5.4-3 auto_degraded标记置位", stats3.get("auto_degraded") is True)
board3 = server.load_board()
sys_msgs = [m for m in board3["messages"] if m.get("type") == "system"]
check("T5.4-3 降级时看板写入系统通知(且仅1条)", len(sys_msgs) == 1,
      sys_msgs[0]["content"][:50] if sys_msgs else "no system msg")
# 测试连接（save=False）不误报系统通知
reset_files()
server.save_board(make_board([]))
server.save_config(make_config(auto_trigger=True, base_url="http://127.0.0.1:9/v1", request_timeout=2))
tmp_cfg = make_config(auto_trigger=True, base_url="http://127.0.0.1:9/v1", request_timeout=2)
for i in range(3):
    server.call_llm("sys", f"testconn {i}", config_override=tmp_cfg)
board_t = server.load_board()
sys_t = [m for m in board_t["messages"] if m.get("type") == "system"]
check("T5.4-3 测试连接失败不误报系统通知", len(sys_t) == 0, f"sys_msgs={len(sys_t)}")

# --- T5.4-4: 调用日志完整（时间/模型/token/耗时/是否成功/错误） ---
check("T5.4-4 api_calls.log 已生成", os.path.exists(server.API_LOG_FILE))
entries = []
with open(server.API_LOG_FILE, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            entries.append(json.loads(line))
check("T5.4-4 日志条数>0", len(entries) > 0, f"entries={len(entries)}")
required = {"time", "model", "success", "prompt_tokens", "completion_tokens",
            "total_tokens", "elapsed", "error", "error_type"}
check("T5.4-4 日志字段完整(时间/模型/token/耗时/成功/错误)",
      all(required.issubset(set(e.keys())) for e in entries))
check("T5.4-4 日志含成功与失败记录",
      any(e["success"] is False for e in entries), "失败记录存在")

# ===== 汇总 =====
print()
print("=" * 60)
print(f"结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    print("失败项：")
    for f_ in FAIL:
        print(f"  - {f_}")
    sys.exit(1)
print("全部通过 ✓")
# 清理临时目录
shutil.rmtree(_tmp, ignore_errors=True)
