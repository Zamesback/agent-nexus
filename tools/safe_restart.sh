#!/bin/bash
# 安全重启 AgentNexus 服务：有成员正在跑就拒绝（v2.20.35 事故之后加的）。
# 事故：2026-11-07 23:10 我重启时没拦住一个跑了 11 分钟的回合，把它打断了、回复丢失。
# 用法：tools/safe_restart.sh [端口，默认 8766] [--force]
PORT="${1:-8766}"
FORCE="$2"

BUSY=$(curl -s --max-time 5 "http://localhost:${PORT}/api/dsh/running" 2>/dev/null \
  | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
    rs = d.get("running") or []
    print(len(rs))
    for r in rs:
        sys.stderr.write("  - %s (%s，已跑 %d 秒)\n" % (r.get("agentId"), r.get("label"), int(r.get("elapsed") or 0)))
except Exception:
    print(0)' 2>/tmp/_busy_detail)

if [ "$BUSY" != "0" ] && [ "$FORCE" != "--force" ]; then
  echo "拒绝重启：还有 ${BUSY} 个成员在跑——等它们跑完，或加 --force 强行重启（会丢它们的回复）"
  cat /tmp/_busy_detail 2>/dev/null
  exit 1
fi

PID=$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | head -1)
if [ -z "$PID" ]; then
  echo "端口 ${PORT} 没有进程在听——可能已经停了"
  exit 0
fi
echo "重启：旧 pid=${PID}（在跑的成员：${BUSY}）"
kill "$PID" 2>/dev/null
for i in $(seq 1 15); do
  sleep 1
  NEW=$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null | head -1)
  if [ -n "$NEW" ] && [ "$NEW" != "$PID" ]; then
    echo "已重启：新 pid=${NEW}（第 ${i} 秒）"
    curl -s -o /dev/null -w "健康检查 http=%{http_code}\n" --max-time 5 "http://localhost:${PORT}/" || true
    exit 0
  fi
done
echo "旧进程已停，但没有自动拉起——请手动启动：COLLAB_PROJECTS_ROOT=... python3 server.py ${PORT}"
exit 2
