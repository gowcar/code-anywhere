#!/usr/bin/env python3
"""SessionStart Hook — 终端 Claude 启动时通知 Bot 建立 watcher

这个 Hook 在终端和 Bot 模式下都需要触发（用于建立 JSONL 监听）
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

# Skip sessions marked by other services (e.g. claude-agent)
if os.environ.get("CODE_ANYWHERE_SKIP"):
    sys.exit(0)

post_to_bot("/session_start", {
    "session_id": data.get("session_id"),
    "cwd": data.get("cwd"),
    "source": data.get("source"),
    "model": data.get("model", ""),
    "transcript_path": data.get("transcript_path"),
    "tmux_pane": os.environ.get("TMUX_PANE", ""),
}, timeout=5)
