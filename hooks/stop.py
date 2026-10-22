#!/usr/bin/env python3
"""Stop Hook — 会话一轮结束通知

策略：
- 终端交互模式：不通知（watcher 监听进程状态）
- Bot 发起的会话：通知 Bot
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import read_stdin, post_to_bot

data = read_stdin()

# 只有 Bot 发起的会话才通知
if not os.environ.get("TELEGRAM_CHAT_ID"):
    sys.exit(0)

post_to_bot("/session_stop", {
    "session_id": data.get("session_id"),
    "stop_reason": data.get("stop_reason", ""),
}, timeout=5)
