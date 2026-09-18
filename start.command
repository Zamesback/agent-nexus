#!/bin/bash
# 多Agent协同看板 - 启动脚本
# 双击即可启动服务器并打开浏览器

cd "$(dirname "$0")"

# 检查端口是否被占用
if lsof -ti:8766 > /dev/null 2>&1; then
    echo "服务器已在运行，直接打开浏览器..."
else
    echo "启动服务器..."
    COLLAB_PROJECTS_ROOT="/Users/zames/Desktop/Project/agent-collab-projects" python3 server.py 8766 &
    sleep 2
fi

# 打开浏览器
open http://localhost:8766

echo "多Agent协同看板已启动！"
echo "访问地址: http://localhost:8766"
echo "按 Ctrl+C 可停止此脚本（服务器继续在后台运行）"
read -p "按回车键退出..."
