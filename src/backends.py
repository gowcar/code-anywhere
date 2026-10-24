"""Project backend preferences and Codex's terminal/transcript boundary.

Claude and Codex keep separate native histories. Only the selected backend is
allowed to receive input or publish events for a project.
"""

import asyncio
import json
import os
import shlex
import tomllib
from pathlib import Path

from src.config import PROJECT_ROOT

PREFERENCES_FILE = PROJECT_ROOT / ".backends.json"
SESSIONS_FILE = PROJECT_ROOT / ".codex-sessions.json"


def remember_codex(cwd: str, sid: str) -> None:
    data = json.loads(SESSIONS_FILE.read_text()) if SESSIONS_FILE.exists() else {}
    key = str(Path(cwd).resolve())
    if data.get(key) == sid:
        return
    data[key] = sid
    temp = SESSIONS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n")
    temp.replace(SESSIONS_FILE)


def codex_env_keys() -> list[str]:
    path = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    cfg = tomllib.loads(path.read_text()) if path.exists() else {}
    provider = cfg.get("model_providers", {}).get(cfg.get("model_provider"), {})
    return [provider["env_key"]] if provider.get("env_key") else []


def get_backend(cwd: str) -> str:
    if not PREFERENCES_FILE.exists():
        return "claude"
    data = json.loads(PREFERENCES_FILE.read_text())
    value = data.get(str(Path(cwd).resolve()), "claude")
    if value not in {"claude", "codex"}:
        raise ValueError(f"Invalid backend for {cwd}: {value}")
    return value


def set_backend(cwd: str, backend: str) -> None:
    if backend not in {"claude", "codex"}:
        raise ValueError("backend must be claude or codex")
    data = json.loads(PREFERENCES_FILE.read_text()) if PREFERENCES_FILE.exists() else {}
    data[str(Path(cwd).resolve())] = backend
    temp = PREFERENCES_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temp.replace(PREFERENCES_FILE)


async def infer_live_backend(cwd: str) -> None:
    """Initialize an unset project from its exact live terminal directory.

Explicit preferences win. Mixed Claude/Codex processes are ambiguous, so do
not guess. Never infer a parent project's backend from a child directory.
"""
    root = Path(cwd).resolve()
    data = json.loads(PREFERENCES_FILE.read_text()) if PREFERENCES_FILE.exists() else {}
    if str(root) in data:
        return
    aliases = {value for key, value in data.items() if Path(key).resolve() == root}
    if len(aliases) == 1:
        set_backend(cwd, aliases.pop())
        return
    if aliases:
        return
    rows = await command('tmux', 'list-panes', '-aF', '#{pane_id}\t#{pane_current_path}')
    live = set()
    for row in rows.splitlines():
        pane, sep, directory = row.partition('\t')
        if not sep or not directory or Path(directory).resolve() != root:
            continue
        for backend in ('codex', 'claude'):
            if await pane_processes(pane, backend):
                live.add(backend)
    if len(live) == 1:
        # Re-read after subprocess awaits: do not overwrite a concurrent choice.
        current = json.loads(PREFERENCES_FILE.read_text()) if PREFERENCES_FILE.exists() else {}
        if str(root) not in current:
            set_backend(cwd, live.pop())


async def command(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=10)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    if proc.returncode:
        # Arguments may include gateway credentials: never include argv here.
        raise RuntimeError(f"{args[0]} failed: {err.decode(errors='replace')[:300]}")
    return out.decode(errors="replace").strip()


async def pane_processes(pane: str, backend: str) -> list[str]:
    try:
        tty = await command("tmux", "display-message", "-p", "-t", pane, "#{pane_tty}")
        if not tty:
            return []
        rows = await command("ps", "-t", tty.rsplit("/", 1)[-1], "-o", "pid=,comm=")
        return [p[0] for row in rows.splitlines()
                if len(p := row.strip().split(None, 1)) == 2
                and Path(p[1]).name == backend]
    except (OSError, RuntimeError, asyncio.TimeoutError):
        return []


async def codex_transcript(pane: str, cwd: str) -> tuple[str, str] | None:
    """Bind the actual process's open rollout, never guess by project mtime.

This also handles Codex paginated histories: the live rollout still contains
item_completed events. lsof is present on the supported macOS host.
"""
    for pid in await pane_processes(pane, "codex"):
        try:
            files = await command("lsof", "-a", "-p", pid, "-Fn")
            for line in files.splitlines():
                if not line.startswith("n") or not line.endswith(".jsonl"):
                    continue
                path = Path(line[1:])
                if not path.name.startswith("rollout-"):
                    continue
                with path.open() as f:
                    meta = json.loads(f.readline())
                payload = meta.get("payload", {})
                if meta.get("type") != "session_meta" or not payload.get("cwd"):
                    continue
                if isinstance(payload.get("source"), dict):
                    continue  # sub-agent rollouts must not replace the terminal's main thread
                recorded_cwd = Path(payload["cwd"])
                requested_cwd = Path(cwd).resolve()
                matches = recorded_cwd.resolve() == requested_cwd
                if not matches and not recorded_cwd.exists():
                    # An open process survives a directory rename, while its
                    # immutable rollout metadata still contains the old path.
                    # Only the actual owning process's cwd can prove the move.
                    try:
                        matches = Path(f"/proc/{pid}/cwd").resolve(strict=True) == requested_cwd
                    except OSError:
                        pass
                if matches:
                    sid = payload.get("id") or payload.get("session_id")
                    if sid:
                        return sid, str(path)
        except (OSError, ValueError, RuntimeError, asyncio.TimeoutError):
            continue
    return None


def codex_transcript_offsets() -> dict[str, int]:
    """Snapshot existing logs before input, even while a resumed CLI is starting."""
    from src.projects import codex_sessions_dir
    offsets = {}
    for path in codex_sessions_dir().rglob("*.jsonl"):
        try:
            offsets[str(path.resolve())] = path.stat().st_size
        except FileNotFoundError:
            continue
    return offsets


def codex_launch_args(cwd: str, resume_id: str | None = None, model: str | None = None) -> list[str]:
    from src.permissions import get_mode, MODES
    mode = get_mode(cwd)
    args = [os.getenv("CODEX_BIN", "codex"), "--no-alt-screen",
            "-a", "never", "-s", MODES[mode],
            "-c", 'project_doc_fallback_filenames=["CLAUDE.md"]',
            "-c", "check_for_update_on_startup=false"]
    if mode == "workspace":
        args += ["-c", "sandbox_workspace_write.network_access=true"]
    from src import model_prefs
    prefs = model_prefs.get(cwd, "codex")
    model = prefs.get("model") or model or os.getenv("CODEX_MODEL")
    if model:
        args += ["-m", model]
    if prefs.get("effort"):
        args += ["-c", f'model_reasoning_effort="{prefs["effort"]}"']
    if not resume_id and SESSIONS_FILE.exists():
        resume_id = json.loads(SESSIONS_FILE.read_text()).get(str(Path(cwd).resolve()))
    if resume_id:
        args += ["resume", resume_id]
    return args


def claude_transcript_offsets(cwd: str) -> dict[str, int]:
    """Sizes of a project's Claude logs before input; files created later start at 0."""
    from src import projects
    folder = projects.claude_dir(cwd)
    offsets = {}
    for path in (folder.glob("*.jsonl") if folder else []):
        try:
            offsets[str(path.resolve())] = path.stat().st_size
        except FileNotFoundError:
            continue
    return offsets


async def claude_session(pane: str, cwd: str) -> dict | None:
    """SessionStart hook 记在 pane 上的会话身份（@ca_claude）；jsonl 尚未生成时也可用，校验不符返回 None。"""
    try:
        raw = await command("tmux", "display-message", "-p", "-t", pane, "#{@ca_claude}")
        info = json.loads(raw) if raw else {}
        if not isinstance(info, dict):
            return None
        sid = info.get("session_id")
        path = Path(info.get("transcript_path") or "")
        if (not sid or not path.is_absolute() or path.name != f"{sid}.jsonl"
                or not info.get("cwd") or Path(info["cwd"]).resolve() != Path(cwd).resolve()):
            return None
        return info
    except (OSError, ValueError, RuntimeError, asyncio.TimeoutError):
        return None


async def remember_claude(pane: str, info: dict) -> None:
    metadata = {key: info.get(key, "") for key in ("session_id", "cwd", "transcript_path", "model", "permission_mode")}
    await command("tmux", "set-option", "-p", "-t", pane, "@ca_claude", json.dumps(metadata, ensure_ascii=False))


def claude_permission_mode(info: dict) -> str | None:
    native = info.get("permission_mode")
    if native in {"bypassPermissions", "full"}:
        return "full"
    return "default" if native else None


async def claude_runtime_mode(pane: str, info: dict) -> str | None:
    """部分 CLI 版本的 SessionStart 不带权限模式：看 pane 里真实 claude 进程的启动参数。"""
    if info.get("permission_mode"):
        return info["permission_mode"]
    for pid in await pane_processes(pane, "claude"):
        try:
            args = shlex.split(await command("ps", "-p", pid, "-o", "command="))
            if "--dangerously-skip-permissions" in args:
                return "bypassPermissions"
            if "--permission-mode" in args:
                return args[args.index("--permission-mode") + 1]
        except (OSError, RuntimeError, ValueError, IndexError, asyncio.TimeoutError):
            continue
    return None


CLAUDE_FULL_SETTINGS = json.dumps({"sandbox": {"enabled": False}, "skipDangerousModePermissionPrompt": True})


def claude_launch_args(cwd: str, resume_id: str | None = None) -> list[str]:
    """/model 偏好优先；未设权限时沿用项目 .claude 设置（settings 里的 bypass 必须靠 CLI 参数才生效）。"""
    from src import model_prefs
    prefs = model_prefs.get(cwd, "claude")
    args = ["claude"]
    if resume_id:
        args += ["--resume", resume_id]
    if prefs.get("model"):
        args += ["--model", prefs["model"]]
    if prefs.get("effort"):
        args += ["--effort", prefs["effort"]]
    permission = prefs.get("permission") or _claude_settings_permission(cwd)
    if permission == "full":
        # 新机器的全局设置里没有“跳过危险模式确认”，不带这个第一次启动会卡在确认框上
        args += ["--dangerously-skip-permissions", "--settings", CLAUDE_FULL_SETTINGS]
    return args


def _claude_settings_permission(cwd: str) -> str | None:
    claude_dir = Path(cwd).resolve() / ".claude"
    for name in ("settings.local.json", "settings.json"):
        try:
            mode = json.loads((claude_dir / name).read_text()).get("permissions", {}).get("defaultMode")
        except (OSError, ValueError, AttributeError):
            continue
        if mode:
            return "full" if mode == "bypassPermissions" else "default"
    return None


def launch_env_args(backend: str) -> list[str]:
    """tmux 服务端环境里通常没有凭据，拉起 CLI 时用 -e 显式传入。"""
    keys = {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
            "ANTHROPIC_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_HOME"}   # 来自 config.env
    if backend == "codex":
        keys.update(codex_env_keys())
    args = []
    for key in sorted(keys):
        if value := os.environ.get(key):
            args += ["-e", f"{key}={value}"]
    return args


def normalize_codex(event: dict) -> dict | None:
    """Convert public conversation items to the existing TG / web event schema.

Never publish reasoning, instructions, world state, or duplicate response_item
messages. Current CLI uses item_completed; older rollouts use agent_message.
"""
    payload = event.get("payload", {})
    if event.get("type") == "turn_context":
        sandbox = (payload.get("sandbox_policy") or {}).get("type")
        mode = {"danger-full-access": "full", "workspace-write": "workspace", "read-only": "read-only"}.get(sandbox)
        return {"type": "system", "model": payload.get("model", ""), "permission_mode": mode}
    if event.get("type") != "event_msg":
        return None
    kind = payload.get("type")
    from src.turn_status import lifecycle
    state = lifecycle(event)
    if state:
        return state
    item = payload.get("item", {}) if kind == "item_completed" else {}
    item_type = item.get("type")
    uid = item.get("id")
    if item_type == "ImageView" and uid:
        return {"type": "image_view", "uuid": uid, "path": item.get("path", "")}
    if item_type in {"UserMessage", "AgentMessage"}:
        text = "\n".join(c.get("text", "") for c in item.get("content", [])
                         if c.get("type") in {"text", "Text"})
        role = "user" if item_type == "UserMessage" else "assistant"
    elif kind in {"user_message", "agent_message"}:
        text = payload.get("message", "")
        role = "user" if kind == "user_message" else "assistant"
    elif item_type == "CommandExecution":
        cmd = item.get("command", [])
        return {"type": "assistant", "uuid": uid, "message": {"content": [
            {"type": "tool_use", "id": uid, "name": "Bash",
             "input": {"command": " ".join(cmd) if isinstance(cmd, list) else cmd}}]}}
    else:
        return None
    if not text:
        return None
    return {"type": role, "uuid": uid,
            "origin": {"kind": "human"} if role == "user" else {},
            "message": {"role": role, "content": text if role == "user" else [
                {"type": "text", "text": text}]}}
