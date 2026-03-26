#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

# 检查 .env
if [ ! -f .env ]; then
    cp .env.example .env
    echo ""
    echo "══════════════════════════════════════════════════════"
    echo "  Code Anywhere PoC — 首次设置"
    echo "══════════════════════════════════════════════════════"
    echo ""
    echo "  已生成配置文件 .env，请编辑填入 BOT_TOKEN："
    echo "  vim $(pwd)/.env"
    echo ""
    echo "  设置群组话题功能（每个话题 = 一个独立 Claude 会话）："
    echo "  a) 创建一个 Telegram 群组"
    echo "  b) 将 Bot 加入群组并设为管理员（全部权限）"
    echo "  c) 群组设置 → 开启「话题 / Topics」"
    echo "  d) 在话题内给 Bot 发消息即可"
    echo ""
    echo "  填好后重新运行: ./start.sh"
    echo ""
    echo "══════════════════════════════════════════════════════"
    exit 1
fi

# 检查 BOT_TOKEN
BOT_TOKEN=$(grep -E '^BOT_TOKEN=' .env | cut -d= -f2- | xargs)
if [ -z "$BOT_TOKEN" ] || [ "$BOT_TOKEN" = "your_bot_token_here" ]; then
    echo "错误: 请在 .env 中填入 BOT_TOKEN"
    exit 1
fi

# 检查/创建 venv
if [ ! -d .venv ]; then
    echo "创建虚拟环境..."
    uv venv
fi

# 安装依赖
uv pip install -q -r requirements.txt

# 启动
uv run python bot.py
