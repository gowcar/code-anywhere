#!/usr/bin/env python3
"""PreToolUse Hook — 工具权限审批

策略：
- 所有会话都先发 TG 审批请求
- Bot 发起的会话：TG 审批，超时 → deny
- 终端会话：TG 优先审批，超时 → fallback 到 CLI 弹窗
"""

import json
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

tool_name = data.get("tool_name", "?")
tool_input = data.get("tool_input", {})

# 构建工具描述
if tool_name == "Bash":
    desc = f"⚡ Bash: {tool_input.get('command', '')[:200]}"
elif tool_name in ("Read", "Edit", "Write"):
    icon = "📖" if tool_name == "Read" else "✏️" if tool_name == "Edit" else "📝"
    desc = f"{icon} {tool_name}: {tool_input.get('file_path', '')}"
elif tool_name in ("Glob", "Grep"):
    desc = f"🔍 {tool_name}: {tool_input.get('pattern', '')[:100]}"
else:
    desc = f"🔧 {tool_name}: {json.dumps(tool_input, ensure_ascii=False)[:150]}"

# CLI 权限模式检测：yolo/auto 等模式不需要审批，直接跳过
permission_mode = data.get("permission_mode", "default")
AUTO_MODES = {"bypassPermissions", "auto", "dontAsk", "acceptEdits"}
if permission_mode in AUTO_MODES:
    sys.exit(0)

# 判断来源：Bot 会话 vs 终端会话
chat_id = os.environ.get("TELEGRAM_CHAT_ID")
thread_id = os.environ.get("TELEGRAM_THREAD_ID")
is_bot_session = bool(chat_id)

# 终端会话用较短超时（TG 优先，超时 fallback 到 CLI）
# Bot 会话用长超时（TG 是唯一审批渠道）
timeout = 120 if is_bot_session else 30

resp = post_to_bot("/permission", {
    "session_id": data.get("session_id"),
    "tool_name": tool_name,
    "description": desc,
    "chat_id": chat_id,
    "thread_id": thread_id,
    "timeout": timeout,
}, timeout=timeout + 10)  # HTTP 超时比审批超时多留 10s

if resp and resp.get("decision") == "deny":
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "User denied via Telegram",
    }}))
elif resp and resp.get("decision") == "allow":
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
    }}))
# else: 超时或 Bot 不可达 → 不输出 → Claude 走 CLI 默认权限流程
