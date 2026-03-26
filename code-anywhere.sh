#!/usr/bin/env bash
# ──────────────────────────────────────────────
#  Code Anywhere — 服务管理
#  Usage: ./code-anywhere.sh start|stop|restart|debug
# ──────────────────────────────────────────────
set -e

APP_NAME="code-anywhere"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$APP_DIR/$APP_NAME.pid"
LOG_DIR="$APP_DIR/logs"
LOG_FILE="$LOG_DIR/$APP_NAME.log"

# ── 颜色 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

_banner() {
    echo -e "${CYAN}"
    cat <<'EOF'
   ___          _        _                _
  / __\___   __| | ___  / \   _ __  _   _| |    | |__   ___ _ __ ___
 / /  / _ \ / _` |/ _ \/  /\ | '_ \| | | \ \ /\ / / '_ \ / _ \ '__/ _ \
/ /__| (_) | (_| |  __/\_/ \\ | | | | |_| |\ V  V /| | | |  __/ | |  __/
\____/\___/ \__,_|\___\/   \/ |_| |_|\__, | \_/\_/ |_| |_|\___|_|  \___|
                                      |___/
EOF
    echo -e "${NC}"
}

_setup() {
    # 首次设置
    if [ ! -f "$APP_DIR/.env" ]; then
        cp "$APP_DIR/.env.example" "$APP_DIR/.env"
        echo ""
        echo -e "${YELLOW}══════════════════════════════════════════════${NC}"
        echo -e "  ${BOLD}Code Anywhere — 首次设置${NC}"
        echo -e "${YELLOW}══════════════════════════════════════════════${NC}"
        echo ""
        echo "  已生成 .env，请编辑填入 BOT_TOKEN："
        echo "  vim $APP_DIR/.env"
        echo ""
        echo "  群组话题设置："
        echo "  a) 创建 Telegram 群组"
        echo "  b) 将 Bot 加入并设为管理员"
        echo "  c) 群组设置 → 开启「话题 / Topics」"
        echo ""
        echo -e "  填好后: ${GREEN}./code-anywhere.sh start${NC}"
        echo -e "${YELLOW}══════════════════════════════════════════════${NC}"
        exit 1
    fi

    # 检查 BOT_TOKEN
    BOT_TOKEN=$(grep -E '^BOT_TOKEN=' "$APP_DIR/.env" | cut -d= -f2- | xargs)
    if [ -z "$BOT_TOKEN" ] || [ "$BOT_TOKEN" = "your_bot_token_here" ]; then
        echo -e "${RED}错误: 请在 .env 中填入 BOT_TOKEN${NC}"
        exit 1
    fi

    # venv
    if [ ! -d "$APP_DIR/.venv" ]; then
        echo "创建虚拟环境..."
        cd "$APP_DIR" && uv venv
    fi

    # 依赖
    cd "$APP_DIR" && uv pip install -q -r requirements.txt

    # 安装 Hook（幂等）
    cd "$APP_DIR" && uv run python hooks/install.py
}

_is_running() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        rm -f "$PID_FILE"
    fi
    return 1
}

_status_line() {
    if _is_running; then
        local pid
        pid=$(cat "$PID_FILE")
        echo -e "  状态:    ${GREEN}运行中${NC} (PID $pid)"
    else
        echo -e "  状态:    ${YELLOW}未运行${NC}"
    fi
}

do_start() {
    if _is_running; then
        local pid
        pid=$(cat "$PID_FILE")
        echo -e "${YELLOW}$APP_NAME 已在运行 (PID $pid)${NC}"
        echo -e "使用 ${CYAN}./code-anywhere.sh restart${NC} 重启"
        exit 0
    fi

    _setup
    _banner

    mkdir -p "$LOG_DIR"

    echo -e "  模式:    ${GREEN}后台运行${NC}"
    echo -e "  日志:    $LOG_FILE"

    cd "$APP_DIR"
    nohup uv run python -m src.main >> "$LOG_FILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$PID_FILE"

    # 等待一小会儿确认进程存活
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
        echo -e "  PID:     ${GREEN}$pid${NC}"
        echo ""
        echo -e "  ${CYAN}tail -f $LOG_FILE${NC}  查看日志"
        echo -e "  ${CYAN}./code-anywhere.sh stop${NC}     停止服务"
        echo ""
    else
        rm -f "$PID_FILE"
        echo -e "  ${RED}启动失败，请查看日志:${NC}"
        tail -20 "$LOG_FILE" 2>/dev/null
        exit 1
    fi
}

do_stop() {
    if ! _is_running; then
        echo -e "${YELLOW}$APP_NAME 未在运行${NC}"
        return 0
    fi

    local pid
    pid=$(cat "$PID_FILE")
    echo -ne "停止 $APP_NAME (PID $pid)... "

    kill "$pid" 2>/dev/null

    # 等待进程退出（最多 10 秒）
    local count=0
    while kill -0 "$pid" 2>/dev/null && [ $count -lt 10 ]; do
        sleep 1
        count=$((count + 1))
    done

    if kill -0 "$pid" 2>/dev/null; then
        echo -e "${YELLOW}强制终止${NC}"
        kill -9 "$pid" 2>/dev/null
    fi

    rm -f "$PID_FILE"
    echo -e "${GREEN}已停止${NC}"
}

do_restart() {
    echo -e "${CYAN}重启 $APP_NAME ...${NC}"
    do_stop
    sleep 1
    do_start
}

do_debug() {
    if _is_running; then
        local pid
        pid=$(cat "$PID_FILE")
        echo -e "${YELLOW}$APP_NAME 正在后台运行 (PID $pid)${NC}"
        echo -e "请先 ${CYAN}./code-anywhere.sh stop${NC} 再进入调试模式"
        exit 1
    fi

    _setup
    _banner

    echo -e "  模式:    ${YELLOW}前台调试${NC} (Ctrl+C 退出)"
    echo ""

    cd "$APP_DIR"
    uv run python -m src.main
}

# ── 入口 ──
case "${1:-}" in
    start)   do_start   ;;
    stop)    do_stop    ;;
    restart) do_restart ;;
    debug)   do_debug   ;;
    *)
        echo -e "${BOLD}Code Anywhere${NC} — 服务管理"
        echo ""
        echo -e "  ${GREEN}./code-anywhere.sh start${NC}     后台启动"
        echo -e "  ${GREEN}./code-anywhere.sh stop${NC}      停止服务"
        echo -e "  ${GREEN}./code-anywhere.sh restart${NC}   重启服务"
        echo -e "  ${GREEN}./code-anywhere.sh debug${NC}     前台调试模式"
        echo ""
        _status_line
        ;;
esac
