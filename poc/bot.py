"""
Code Anywhere PoC — 长连接双向流式验证

架构：每个会话（topic/私聊）维护一个长驻 claude 进程
通过 --input-format stream-json 双向通信，避免每条消息重启进程

用法：
  1. cp .env.example .env && 填写配置
  2. ./start.sh
  3. 在 Telegram 私聊或 Forum Topic 里给 Bot 发消息
"""

import os
import json
import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
_raw_users = os.getenv("ALLOWED_USERS", "0").strip()
ALLOWED_USERS = set(map(int, _raw_users.split(","))) if _raw_users else {0}
ALLOW_ALL = 0 in ALLOWED_USERS
PROJECT_ROOTS = [os.path.expanduser(r.strip()) for r in os.getenv("PROJECT_ROOTS", "~/workspace").split(",")]

STATE_FILE = Path(__file__).parent / ".state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("poc_debug.log")],
)
log = logging.getLogger("poc")


# ============ 会话管理 ============

@dataclass
class Session:
    """一个长驻 Claude 进程"""
    key: int                    # session_key (chat_id 或 topic_id)
    cwd: str                    # 工作目录
    proc: asyncio.subprocess.Process | None = None
    session_id: str | None = None
    ready: bool = False         # system init 完成
    busy: bool = False          # 正在处理消息
    last_active: float = field(default_factory=time.time)
    _stdout_reader: asyncio.Task | None = field(default=None, repr=False)
    _response_queue: asyncio.Queue = field(default_factory=asyncio.Queue, repr=False)


# 全局状态
active_sessions: dict[int, Session] = {}    # session_key → Session
topic_dirs: dict[int, str] = {}             # session_key → cwd
last_dir: str | None = None


def _load_state():
    global last_dir, topic_dirs
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            last_dir = state.get("last_dir")
            topic_dirs = {int(k): v for k, v in state.get("topic_dirs", {}).items()}
        except Exception:
            pass


def _save_state():
    # 保存 session_id 映射用于 --resume
    session_ids = {str(k): s.session_id for k, s in active_sessions.items() if s.session_id}
    STATE_FILE.write_text(json.dumps({
        "last_dir": last_dir,
        "topic_dirs": {str(k): v for k, v in topic_dirs.items()},
        "session_ids": session_ids,
    }, ensure_ascii=False))


def get_cwd(session_key: int) -> str | None:
    return topic_dirs.get(session_key, last_dir)


_load_state()

# 隐藏 httpx 的成功日志（TG polling），只显示错误
logging.getLogger("httpx").setLevel(logging.WARNING)


# ============ Claude 长连接进程管理 ============

async def _spawn_claude(session: Session, resume_id: str | None = None) -> None:
    """启动一个长驻 claude 进程"""
    cmd = [
        "claude", "-p",
        "--output-format", "stream-json",
        "--input-format", "stream-json",
        "--verbose",
        "--model", "sonnet",
        "--strict-mcp-config",
    ]
    if resume_id:
        cmd.extend(["--resume", resume_id])

    env = os.environ.copy()
    # 移除可能干扰的环境变量
    env.pop("CLAUDECODE", None)
    env.pop("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS", None)

    log.info(f"[spawn] key={session.key} cwd={session.cwd} resume={resume_id or 'new'}")

    session.proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=session.cwd,
        env=env,
    )

    # 启动 stdout 读取协程
    session._stdout_reader = asyncio.create_task(_read_stdout(session))


async def _read_stdout(session: Session) -> None:
    """持续读取 claude stdout，将事件放入 queue"""
    try:
        async for raw_line in session.proc.stdout:
            line = raw_line.decode().strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            log.info(f"[event] key={session.key} type={event.get('type')}: {json.dumps(event, ensure_ascii=False)[:150]}")

            event_type = event.get("type")

            # system init → 标记就绪
            if event_type == "system":
                sid = event.get("session_id")
                if sid:
                    session.session_id = sid
                    _save_state()
                session.ready = True
                log.info(f"[ready] key={session.key} session={sid} model={event.get('model')}")

            # 所有事件都推入 queue 供 handle_message 消费
            await session._response_queue.put(event)

    except Exception as e:
        log.error(f"[reader-error] key={session.key}: {e}")
    finally:
        # 进程结束，推入哨兵
        await session._response_queue.put(None)
        session.ready = False
        log.info(f"[reader-done] key={session.key}")


async def _send_message(session: Session, text: str) -> None:
    """通过 stdin 发送用户消息"""
    msg = json.dumps({
        "type": "user",
        "message": {"role": "user", "content": text}
    }, ensure_ascii=False) + "\n"
    session.proc.stdin.write(msg.encode())
    await session.proc.stdin.drain()
    session.last_active = time.time()


async def get_or_create_session(key: int, cwd: str, first_message: str | None = None) -> Session:
    """获取已有长连接或创建新的。first_message 用于首次启动时触发 system init。"""
    session = active_sessions.get(key)

    # 检查进程是否还活着
    if session and session.proc and session.proc.returncode is not None:
        log.info(f"[dead] key={key} 进程已退出，重新创建")
        active_sessions.pop(key, None)
        session = None

    if session and session.ready:
        return session

    # 创建新会话
    session = Session(key=key, cwd=cwd)
    active_sessions[key] = session

    # 检查是否有可恢复的 session_id
    resume_id = None
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
            resume_id = state.get("session_ids", {}).get(str(key))
        except Exception:
            pass

    await _spawn_claude(session, resume_id)

    # stream-json 模式下，发送第一条消息触发 system init
    if first_message:
        await _send_message(session, first_message)

    # 等待 system init（最多 60 秒）
    try:
        while not session.ready:
            event = await asyncio.wait_for(session._response_queue.get(), timeout=60)
            if event is None:
                # 检查 stderr
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
        raise RuntimeError(f"Claude 进程启动超时（60s）: {stderr}")

    return session


# ============ Telegram 命令 ============

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ALLOW_ALL and update.effective_user.id not in ALLOWED_USERS:
        return
    lines = ["<b>活跃会话</b>\n"]
    if not active_sessions:
        lines.append("（暂无）")
    for key, s in active_sessions.items():
        alive = s.proc and s.proc.returncode is None
        status = "running" if alive else "dead"
        sid = s.session_id[:12] + "..." if s.session_id else "N/A"
        lines.append(f"<code>{key}</code> → {sid} [{status}]")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_cd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ALLOW_ALL and update.effective_user.id not in ALLOWED_USERS:
        return
    topic_id = update.message.message_thread_id
    cwd = get_cwd(topic_id) if topic_id else last_dir

    projects = []
    for root in PROJECT_ROOTS:
        root_path = Path(root.strip())
        if not root_path.is_dir():
            continue
        for child in sorted(root_path.iterdir()):
            if child.is_dir() and (child / ".git").exists():
                projects.append(child)

    if not projects:
        await update.message.reply_text(
            f"当前目录: <code>{cwd or '未设置'}</code>\n\n未找到 git 项目。",
            parse_mode="HTML",
        )
        return

    buttons = []
    for p in projects[:20]:
        label = f"{'> ' if str(p) == cwd else ''}{p.name}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"cd:{p}")])
    await update.message.reply_text(
        f"当前目录: <code>{cwd or '未设置'}</code>\n\n选择项目目录：",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


# 临时缓存：用户选中的项目目录（callback_data 长度限制 64 字节，存不下完整路径）
_pending_project: dict[int, str] = {}   # user_id → project_dir


def _scan_project_sessions(project_dir: str, limit: int = 10) -> list[dict]:
    """扫描项目的 Claude Code 历史会话"""
    # 项目目录 → 哈希路径
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
        # 读第一条 user message 作为摘要
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


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not ALLOW_ALL and query.from_user.id not in ALLOWED_USERS:
        return
    await query.answer()
    data = query.data

    if data.startswith("cd:"):
        # 选了项目 → 缓存路径 → 列出历史会话 + 新建选项
        project_dir = data[3:]
        project_name = Path(project_dir).name
        user_id = query.from_user.id

        # 缓存选中的项目
        _pending_project[user_id] = project_dir
        global last_dir
        last_dir = project_dir
        _save_state()

        # 扫描历史会话
        history = _scan_project_sessions(project_dir)

        buttons = [[InlineKeyboardButton("+ 新建会话", callback_data="new")]]
        for s in history:
            label = s["summary"]
            # callback_data 最多 64 字节，session_id 是 UUID (36字节)，前缀 "r:" = 38字节，安全
            buttons.append([InlineKeyboardButton(label, callback_data=f"r:{s['session_id']}")])

        await query.edit_message_text(
            f"<b>{project_name}</b>\n\n选择历史会话或新建：",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML",
        )

    elif data == "new" or data.startswith("r:"):
        # 新建或恢复会话 → 创建话题
        user_id = query.from_user.id
        project_dir = _pending_project.get(user_id)
        if not project_dir:
            await query.edit_message_text("会话已过期，请重新 /cd")
            return
        is_resume = data.startswith("r:")
        session_id = data[2:] if is_resume else None

        chat_id = query.message.chat_id
        is_private = query.message.chat.type == "private"
        project_name = Path(project_dir).name

        if is_private:
            key = chat_id
            topic_dirs[key] = project_dir
            # 保存 resume session_id
            if session_id:
                # 写入 state 供 get_or_create_session 读取
                if STATE_FILE.exists():
                    state = json.loads(STATE_FILE.read_text())
                else:
                    state = {}
                state.setdefault("session_ids", {})[str(key)] = session_id
                STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))
            _save_state()
            action = f"恢复会话 {session_id[:8]}..." if session_id else "新会话"
            await query.edit_message_text(
                f"<code>{project_name}</code> — {action}\n直接发消息开始对话。",
                parse_mode="HTML",
            )
        else:
            # 群组：创建 Forum Topic
            topic_label = project_name
            await query.edit_message_text(f"正在创建话题 {topic_label}...")
            try:
                topic = await context.bot.create_forum_topic(
                    chat_id=chat_id,
                    name=topic_label,
                )
                key = topic.message_thread_id
                topic_dirs[key] = project_dir
                # 保存 resume session_id
                if session_id:
                    if STATE_FILE.exists():
                        state = json.loads(STATE_FILE.read_text())
                    else:
                        state = {}
                    state.setdefault("session_ids", {})[str(key)] = session_id
                    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False))
                _save_state()
                action = f"恢复会话 {session_id[:8]}..." if session_id else "新会话"
                await context.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=key,
                    text=f"<b>{project_name}</b> — {action}\n直接发消息开始对话。",
                    parse_mode="HTML",
                )
                await query.edit_message_text(f"已创建话题 [{topic_label}]")
            except Exception as e:
                await query.edit_message_text(f"创建话题失败: {e}")
                return

        # 清理旧的会话进程
        old_session = active_sessions.pop(key, None)
        if old_session and old_session.proc and old_session.proc.returncode is None:
            old_session.proc.terminate()
            log.info(f"[kill] key={key} cwd changed")

        log.info(f"[cd] key={key} → {project_dir} session={session_id or 'new'}")


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"你的 Telegram 信息：\n\n"
        f"  User ID:  <code>{user.id}</code>\n"
        f"  用户名:   @{user.username or '无'}\n"
        f"  昵称:     {user.full_name}\n\n"
        f"将 User ID 添加到 <code>.env</code> 的 ALLOWED_USERS 即可授权。",
        parse_mode="HTML",
    )


# ============ 核心消息处理 ============

async def _keep_typing(bot, chat_id: int, topic_id: int | None):
    try:
        while True:
            await bot.send_chat_action(chat_id, ChatAction.TYPING, message_thread_id=topic_id)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ALLOW_ALL and update.effective_user.id not in ALLOWED_USERS:
        user = update.effective_user
        await update.message.reply_text(
            f"未授权。你的 User ID: <code>{user.id}</code>\n"
            f"请将此 ID 添加到 .env 的 ALLOWED_USERS 中，然后重启 Bot。",
            parse_mode="HTML",
        )
        return

    message = update.message
    if not message or not message.text:
        return

    topic_id = message.message_thread_id
    chat_id = message.chat_id
    text = message.text
    is_private = message.chat.type == "private"

    if not is_private and not topic_id:
        await message.reply_text("请在 Forum Topic 内发送消息")
        return

    session_key = chat_id if is_private else topic_id
    log.info(f"[收到] key={session_key} private={is_private} text={text[:80]}")

    cwd = get_cwd(session_key)
    if not cwd:
        await message.reply_text("请先使用 /cd 选择项目目录")
        return

    # 获取或创建长连接
    typing_task = asyncio.create_task(_keep_typing(context.bot, chat_id, topic_id))

    try:
        session = active_sessions.get(session_key)
        is_new = session is None or session.proc is None or session.proc.returncode is not None or not session.ready

        if is_new:
            # 新进程：第一条消息和 spawn 一起，避免等待 system init 后再发
            session = await get_or_create_session(session_key, cwd, first_message=text)
        else:
            # 已有进程：直接发消息
            # 如果有其他消息正在处理，排队等待
            while session.busy:
                await asyncio.sleep(0.5)
            await _send_message(session, text)

        session.busy = True

        # 读取响应直到收到 result 事件
        turn_count = 0
        while True:
            try:
                event = await asyncio.wait_for(session._response_queue.get(), timeout=300)
            except asyncio.TimeoutError:
                await message.reply_text("Claude 响应超时（5分钟）")
                break

            if event is None:
                # 进程退出了
                active_sessions.pop(session_key, None)
                await message.reply_text("Claude 进程意外退出，请重新发送消息")
                break

            event_type = event.get("type")

            if event_type == "assistant":
                content_blocks = event.get("message", {}).get("content", [])
                for block in content_blocks:
                    if block.get("type") == "text":
                        reply_text = block.get("text", "").strip()
                        if reply_text:
                            for i in range(0, len(reply_text), 4096):
                                await message.reply_text(reply_text[i : i + 4096])
                    elif block.get("type") == "tool_use":
                        tool_name = block.get("name", "?")
                        tool_input = block.get("input", {})
                        turn_count += 1
                        desc = f"{tool_name}: {json.dumps(tool_input, ensure_ascii=False)[:100]}"
                        await message.reply_text(f"[{turn_count}] {desc}")

            elif event_type == "result":
                sid = event.get("session_id")
                if sid:
                    session.session_id = sid
                    _save_state()
                if event.get("is_error"):
                    await message.reply_text(f"出错了: {event.get('result', '未知错误')}")
                log.info(f"[完成] key={session_key} session={sid[:12] if sid else 'N/A'} turns={event.get('num_turns')}")
                break

        session.busy = False

    except Exception as e:
        log.error(f"[错误] key={session_key}: {e}", exc_info=True)
        await message.reply_text(f"出错了: {e}")
        # 清理坏掉的会话
        bad = active_sessions.pop(session_key, None)
        if bad and bad.proc and bad.proc.returncode is None:
            bad.proc.terminate()
    finally:
        typing_task.cancel()


# ============ 启动 ============

def main():
    if not BOT_TOKEN:
        print("错误: 请在 .env 中设置 BOT_TOKEN")
        return

    roots_str = ", ".join(PROJECT_ROOTS)
    auth_str = "所有人（PoC 模式）" if ALLOW_ALL else str(ALLOWED_USERS)
    print(
        f"""
╔══════════════════════════════════════╗
║  Code Anywhere PoC - 长连接模式      ║
╠══════════════════════════════════════╣
║  项目根:   {roots_str[:26]:<26s} ║
║  授权:     {auth_str[:26]:<26s} ║
╚══════════════════════════════════════╝

  命令: /whoami  - 查看你的 User ID
        /cd      - 选择项目目录
        /status  - 查看活跃会话
"""
    )

    # long-poll 显式超时：避 TCP 半死连接导致 getUpdates 永远卡住（PTB 默认漏）
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .get_updates_read_timeout(60)
        .get_updates_connect_timeout(15)
        .get_updates_pool_timeout(10)
        .build()
    )

    async def post_init(application):
        await application.bot.set_my_commands([
            BotCommand("cd", "选择项目目录"),
            BotCommand("status", "查看活跃会话"),
            BotCommand("whoami", "查看你的 User ID"),
        ])
    app.post_init = post_init

    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("cd", cmd_cd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & (filters.ChatType.SUPERGROUP | filters.ChatType.PRIVATE),
            handle_message,
        )
    )

    log.info("Bot 启动（长连接模式），等待消息...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
