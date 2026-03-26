#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# 首次设置
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "══════════════════════════════════════════════"
    echo "  Code Anywhere — 首次设置"
    echo "══════════════════════════════════════════════"
    echo ""
    echo "  已生成 .env，请编辑填入 BOT_TOKEN："
    echo "  vim $(pwd)/.env"
    echo ""
    echo "  群组话题设置："
    echo "  a) 创建 Telegram 群组"
    echo "  b) 将 Bot 加入并设为管理员"
    echo "  c) 群组设置 → 开启「话题 / Topics」"
    echo ""
    echo "  填好后: ./start.sh"
    echo "══════════════════════════════════════════════"
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
