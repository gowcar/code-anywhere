#!/usr/bin/env python3
"""Notification Hook — Claude 通知转发

策略：
- 终端交互模式：不转发（watcher 已经在同步 JSONL，这里不重复）
- Bot 发起的会话（有 TELEGRAM_CHAT_ID 环境变量）：转发通知到 TG
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import read_stdin, post_to_bot

data = read_stdin()

# Skip bot's own project
import pathlib
_cwd = data.get("cwd", "")
_self_dir = str(pathlib.Path(__file__).parent.parent.resolve())
if _cwd == _self_dir:
    sys.exit(0)

# 只有 Bot 发起的会话才转发通知
if not os.environ.get("TELEGRAM_CHAT_ID"):
    sys.exit(0)

post_to_bot("/notification", {
    "session_id": data.get("session_id"),
    "message": data.get("message", ""),
    "title": data.get("title", ""),
}, timeout=5)
