"""Claude CLI 长连接封装

每个会话维护一个长驻 claude 进程，通过 --input-format stream-json 双向通信。
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field

from src.config import STATE_FILE

log = logging.getLogger("code-anywhere.runner")


@dataclass
class Session:
    """一个长驻 Claude 会话"""
    key: int
    cwd: str
    proc: asyncio.subprocess.Process | None = None
    session_id: str | None = None
    ready: bool = False
    busy: bool = False
    last_active: float = field(default_factory=time.time)
    _stdout_reader: asyncio.Task | None = field(default=None, repr=False)
    _response_queue: asyncio.Queue = field(default_factory=asyncio.Queue, repr=False)


# session_key → Session
active_sessions: dict[int, Session] = {}


async def spawn_claude(session: Session, resume_id: str | None = None) -> None:
    """启动一个长驻 claude 进程"""
    cmd = [
        "claude", "-p",
        "--output-format", "stream-json",
        "--input-format", "stream-json",
        "--verbose",
        "--strict-mcp-config",
    ]
    if resume_id:
        cmd.extend(["--resume", resume_id])

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", None)
    env.pop("TMUX_PANE", None)              # 防止 hook 误认为终端会话
    env["CODE_ANYWHERE_SKIP"] = "1"         # Bot 发起的 Claude 不触发 code-anywhere hook

    log.info(f"[spawn] key={session.key} cwd={session.cwd} resume={resume_id or 'new'}")

    session.proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=session.cwd,
        env=env,
    )
    session._stdout_reader = asyncio.create_task(_read_stdout(session))


async def _read_stdout(session: Session) -> None:
    """持续读取 stdout，事件推入 queue"""
    try:
        async for raw_line in session.proc.stdout:
            line = raw_line.decode().strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type")
            log.debug(f"[event] key={session.key} type={event_type}")

            if event_type == "system":
                sid = event.get("session_id")
                if sid:
                    session.session_id = sid
                session.ready = True
                log.info(f"[ready] key={session.key} session={sid} model={event.get('model')}")

            await session._response_queue.put(event)

    except Exception as e:
        log.error(f"[reader-error] key={session.key}: {e}")
    finally:
        await session._response_queue.put(None)
        session.ready = False
        log.info(f"[reader-done] key={session.key}")


async def send_message(session: Session, text: str) -> None:
    """通过 stdin 发送用户消息"""
    msg = json.dumps({
        "type": "user",
        "message": {"role": "user", "content": text}
    }, ensure_ascii=False) + "\n"
    session.proc.stdin.write(msg.encode())
    await session.proc.stdin.drain()
    session.last_active = time.time()


async def get_or_create_session(key: int, cwd: str, first_message: str | None = None) -> Session:
    """获取已有长连接或创建新的"""
    session = active_sessions.get(key)

    if session and session.proc and session.proc.returncode is not None:
        log.info(f"[dead] key={key} 进程已退出，重新创建")
        active_sessions.pop(key, None)
        session = None

    if session and session.ready:
        return session

    session = Session(key=key, cwd=cwd)
    active_sessions[key] = session

    # 检查是否有手动指定的 session（/sessions 切换或 __new__ 强制新建）
    from src.state import get_latest_session_id, get_resume_id
    manual_id = get_resume_id(key)
    if manual_id == "__new__":
        resume_id = None
    elif manual_id:
        resume_id = manual_id
    else:
        # 默认：自动 resume 该项目最新 session（和电脑端同步）
        resume_id = get_latest_session_id(cwd)

    await spawn_claude(session, resume_id)

    if first_message:
        await send_message(session, first_message)

    # 等待 system init
    try:
        while not session.ready:
            event = await asyncio.wait_for(session._response_queue.get(), timeout=60)
            if event is None:
                stderr = ""
                if session.proc and session.proc.stderr:
                    stderr = (await session.proc.stderr.read()).decode()[:300]
                raise RuntimeError(f"Claude 进程启动失败: {stderr}")
    except asyncio.TimeoutError:
        stderr = ""
        if session.proc and session.proc.stderr:
            try:
                stderr = (await asyncio.wait_for(session.proc.stderr.read(), timeout=2)).decode()[:300]
            except Exception:
                pass
        raise RuntimeError(f"Claude 启动超时: {stderr}")

    return session


def kill_session(key: int) -> None:
    """终止会话进程"""
    session = active_sessions.pop(key, None)
    if session and session.proc and session.proc.returncode is None:
        session.proc.terminate()
        log.info(f"[kill] key={key}")
