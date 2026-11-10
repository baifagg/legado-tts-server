#!/usr/bin/env bash
# 启动脚本（Linux / macOS）
# 用法： ./scripts/start.sh            默认端口 18760
#        ./scripts/start.sh 18860      指定端口
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    echo "❌ 找不到 Python。先运行： python3 install.py"
    exit 1
fi

PORT="${1:-18760}"

echo "使用解释器： $PY"
echo "端口：       $PORT"
exec "$PY" run.py --port "$PORT"
