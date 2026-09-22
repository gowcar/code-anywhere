"""Telegram-selected Codex permissions, persisted per project.

Changes restart the same tmux pane with the exact native session ID. The caller
holds the project input lock throughout, including transcript reattachment.
"""

import asyncio
import json
import os
from pathlib import Path

from src.config import PROJECT_ROOT

PERMISSIONS_FILE = PROJECT_ROOT / ".permissions.json"
MODES = {"full": "danger-full-access", "workspace": "workspace-write", "read-only": "read-only"}
LABELS = {"full": "Full（文件与网络完全访问）", "workspace": "工作区写入 + 联网", "read-only": "只读，网络受限"}


def get_override(cwd: str) -> str | None:
    data = json.loads(PERMISSIONS_FILE.read_text()) if PERMISSIONS_FILE.exists() else {}
    value = data.get(str(Path(cwd).resolve()))
    if value is not None and value not in MODES:
        raise ValueError("Invalid saved Codex permissions")
    return value


def get_mode(cwd: str) -> str:
    from src import state
    return get_override(cwd) or ("workspace" if state.is_trusted(cwd) else "read-only")


def set_mode(cwd: str, mode: str | None) -> None:
    if mode is not None and mode not in MODES:
        raise ValueError("Invalid Codex permission mode")
    data = json.loads(PERMISSIONS_FILE.read_text()) if PERMISSIONS_FILE.exists() else {}
    key = str(Path(cwd).resolve())
    if mode is None:
        data.pop(key, None)
    else:
        data[key] = mode
    temp = PERMISSIONS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temp.replace(PERMISSIONS_FILE)


async def apply_mode(cwd: str, mode: str, chat_id: int, topic_id: int) -> str:
    """Apply now, retaining the pane and native conversation. No automatic task replay."""
    from src import backends, bot, api_server
    if mode not in MODES:
        raise ValueError("Invalid Codex permission mode")
    if backends.get_backend(cwd) != "codex":
        raise ValueError("请先 /model codex，再切换 Codex 权限。")
    pane = await bot._find_pane_for_project(cwd)
    found = await backends.codex_transcript(pane, cwd) if pane else None
    if pane and not found:
        raise RuntimeError("无法确认当前会话 ID，未重启终端。请先在 Codex 中发一条消息后重试。")
    previous = get_override(cwd)
    # Resolve and validate everything before interrupting the old native turn.
    sid = found[0] if found else None
    model = (api_server.terminal_sessions.get(sid) or {}).get("model")
    env_args = []
    for key in set(backends.codex_env_keys()) | {"CODEX_HOME", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"}:
        if value := os.getenv(key):
            env_args += ["-e", f"{key}={value}"]
    set_mode(cwd, mode)
    try:
        if pane:
            backends.remember_codex(cwd, sid)
            argv = backends.codex_launch_args(cwd, resume_id=sid, model=model)
            await backends.command("tmux", "send-keys", "-t", pane, "Escape")
            await asyncio.sleep(0.5)
            bot.stop_typing(topic_id)
            await backends.command("tmux", "respawn-pane", "-k", "-t", pane, "-c", cwd, *env_args, *argv)
            # Require the exact old thread to be open before reporting success.
            for _ in range(30):
                await asyncio.sleep(0.5)
                resumed = await backends.codex_transcript(pane, cwd)
                if resumed and resumed[0] == sid:
                    if await api_server.register_pane_watcher(pane, cwd):
                        break
            else:
                raise RuntimeError("Codex 未能恢复原会话；已保留会话 ID，请检查终端后重试。")
        else:
            pane = await bot._ensure_pane_for_project(cwd, chat_id, topic_id)
            if not pane:
                raise RuntimeError("Codex 终端启动失败，请检查网关或登录配置。")
        await backends.command("tmux", "set-option", "-p", "-t", pane, "@ca_permission", mode)
        if sid in api_server.terminal_sessions:
            api_server.terminal_sessions[sid]["permission_mode"] = mode
        return pane
    except BaseException:
        # Never silently retain a requested elevated default after a failed switch.
        set_mode(cwd, previous)
        raise
