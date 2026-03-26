"""状态持久化

保存/加载 session 映射、topic 目录绑定、最近选择的目录
"""

import json
import logging
from pathlib import Path

from src.config import STATE_FILE

log = logging.getLogger("code-anywhere.state")

# 全局状态
topic_dirs: dict[int, str] = {}     # session_key → 工作目录
last_dir: str | None = None         # 最近一次 /cd 选择


def load():
    global last_dir, topic_dirs
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            last_dir = state.get("last_dir")
            topic_dirs = {int(k): v for k, v in state.get("topic_dirs", {}).items()}
            log.info(f"[state] loaded: last_dir={last_dir}, {len(topic_dirs)} topics")
        except Exception as e:
            log.warning(f"[state] load failed: {e}")


def save():
    from src.claude_runner import active_sessions
    session_ids = {str(k): s.session_id for k, s in active_sessions.items() if s.session_id}
    STATE_FILE.write_text(json.dumps({
        "last_dir": last_dir,
        "topic_dirs": {str(k): v for k, v in topic_dirs.items()},
        "session_ids": session_ids,
    }, ensure_ascii=False))


def get_cwd(session_key: int) -> str | None:
    return topic_dirs.get(session_key, last_dir)


def set_cwd(session_key: int, cwd: str):
    global last_dir
    topic_dirs[session_key] = cwd
    last_dir = cwd
    save()


def get_resume_id(key: int) -> str | None:
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            return state.get("session_ids", {}).get(str(key))
        except Exception:
            return None
    return None


def set_resume_id(key: int, session_id: str):
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
    else:
        state = {}
    state.setdefault("session_ids", {})[str(key)] = session_id
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))


def scan_project_sessions(project_dir: str, limit: int = 10) -> list[dict]:
    """扫描项目的 Claude Code 历史会话"""
    claude_projects = Path.home() / ".claude" / "projects"
    dir_hash = project_dir.replace("/", "-")
    proj_path = claude_projects / dir_hash
    if not proj_path.is_dir():
        return []

    sessions = []
    for f in sorted(proj_path.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.stat().st_size < 100:
            continue
        info = {"session_id": f.stem, "mtime": f.stat().st_mtime, "summary": ""}
        try:
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    d = json.loads(line)
                    if d.get("type") == "user":
                        content = d.get("message", {}).get("content", "")
                        if isinstance(content, str):
                            info["summary"] = content[:60]
                        elif isinstance(content, list):
                            for c in content:
                                if c.get("type") == "text":
                                    info["summary"] = c["text"][:60]
                                    break
                        break
        except Exception:
            pass
        if info["summary"]:
            sessions.append(info)
        if len(sessions) >= limit:
            break
    return sessions


def get_recent_history(project_dir: str, max_turns: int = 5) -> list[dict]:
    """读取项目最新 session 的最近几轮对话"""
    claude_projects = Path.home() / ".claude" / "projects"
    dir_hash = project_dir.replace("/", "-")
    proj_path = claude_projects / dir_hash
    if not proj_path.is_dir():
        return []

    jsonl_files = sorted(proj_path.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True)
    if not jsonl_files or jsonl_files[0].stat().st_size < 100:
        return []

    # 读所有 user/assistant 消息
    messages = []
    try:
        with open(jsonl_files[0]) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                msg_type = d.get("type")

                if msg_type == "user":
                    content = d.get("message", {}).get("content", "")
                    text = ""
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        for c in content:
                            if c.get("type") == "text":
                                text = c["text"]
                                break
                    if text.strip():
                        messages.append({"role": "user", "text": text.strip()})

                elif msg_type == "assistant":
                    content = d.get("message", {}).get("content", [])
                    for block in content:
                        if block.get("type") == "text" and block.get("text", "").strip():
                            messages.append({"role": "assistant", "text": block["text"].strip()})
                            break
    except Exception:
        pass

    # 返回最后 N 轮
    return messages[-max_turns * 2:]


def get_latest_session_id(project_dir: str) -> str | None:
    """获取项目最新的 session_id（按 mtime 排序）"""
    claude_projects = Path.home() / ".claude" / "projects"
    dir_hash = project_dir.replace("/", "-")
    proj_path = claude_projects / dir_hash
    if not proj_path.is_dir():
        return None

    jsonl_files = sorted(proj_path.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True)
    for f in jsonl_files:
        if f.stat().st_size > 100:
            return f.stem
    return None


load()
