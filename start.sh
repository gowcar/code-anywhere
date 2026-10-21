#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# 首次设置：交给 ./setup（交互式，自动识别群组、装服务）
if [ ! -f .env ]; then
    echo "首次使用请运行 ./setup（交互式安装，约 10 分钟）。"
    echo "日常由 systemd/launchd 常驻运行，一般不需要手动 ./start.sh。"
    exit 1
fi

# 检查 BOT_TOKEN
BOT_TOKEN=$(grep -E '^BOT_TOKEN=' .env | cut -d= -f2- | xargs)
if [ -z "$BOT_TOKEN" ] || [ "$BOT_TOKEN" = "your_bot_token_here" ]; then
    echo "错误: 请在 .env 中填入 BOT_TOKEN"
    exit 1
fi

# venv
if [ ! -d .venv ]; then
    echo "创建虚拟环境..."
    uv venv
fi

# 依赖
uv pip install -q -r requirements.txt

# 安装 Hook（幂等）
uv run python hooks/install.py

echo ""

# 启动
uv run python -m src.main
