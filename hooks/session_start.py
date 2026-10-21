#!/usr/bin/env python3
"""SessionStart Hook — 终端 Claude 启动时通知 Bot 建立 watcher

这个 Hook 在终端和 Bot 模式下都需要触发（用于建立 JSONL 监听）
"""

import json
import os
import subprocess
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

# 给当前 tmux pane 打项目标签 — 注入时可实时反查，免去存 pane id
_tmux_pane = os.environ.get("TMUX_PANE", "")
if _tmux_pane and _cwd:
    try:
        subprocess.run(
            ["tmux", "set", "-p", "-t", _tmux_pane, "@ca_project", _cwd],
            check=False, timeout=2,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        # 会话身份记在 pane 上：桥接离线或 jsonl 尚未生成时，恢复流程也能找到准确会话
        metadata = {key: data.get(key, "") for key in ("session_id", "cwd", "transcript_path", "model", "permission_mode")}
        subprocess.run(
            ["tmux", "set", "-p", "-t", _tmux_pane, "@ca_claude", json.dumps(metadata)],
            check=False, timeout=2,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

post_to_bot("/session_start", {
    "session_id": data.get("session_id"),
    "cwd": data.get("cwd"),
    "source": data.get("source"),
    "model": data.get("model", ""),
    "transcript_path": data.get("transcript_path"),
    "permission_mode": data.get("permission_mode"),
    "tmux_pane": os.environ.get("TMUX_PANE", ""),
}, timeout=5)

# 外脑：确保 recon/ 骨架，并把「当前位置」摘要注入本次 session 上下文
# （防上下文压缩失忆——resume / compact 续接时 SessionStart 会重跑，自动重注入）
try:
    sys.path.insert(0, _self_dir)
    from src import recon
    if _cwd:
        recon.ensure_skeleton(_cwd)
        ctx = recon.build_session_context(_cwd)
        if ctx:
            # stdout 输出的 additionalContext 会被 claude 加进上下文
            print(json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": ctx,
                }
            }))
except Exception:
    pass
