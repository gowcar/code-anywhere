"""Telegram Bot — 命令路由 + 消息处理"""

import asyncio
import json
import logging
import time
from pathlib import Path

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

import src.config as config
from src.config import BOT_TOKEN, ALLOWED_USERS, ALLOW_ALL
from src.claude_runner import (
    active_sessions, get_or_create_session, send_message, kill_session,
)
from src.renderer import split_message, format_tool_use
from src import state
from src import projects

log = logging.getLogger("code-anywhere.bot")


def auth_check(user_id: int) -> bool:
    return ALLOW_ALL or user_id in ALLOWED_USERS


# ============ /whoami ============

async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    text = (
        f"User ID:  <code>{user.id}</code>\n"
        f"用户名:   @{user.username or '无'}\n"
        f"昵称:     {user.full_name}\n"
        f"Chat ID:  <code>{chat.id}</code>\n"
        f"Chat 类型: {chat.type}"
    )
    await update.message.reply_text(text, parse_mode="HTML")


# ============ /projects ============

async def _auto_detect_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """检测群组并自动同步。返回 True 表示是首次检测，调用方应 return。"""
    chat = update.effective_chat
    if chat.type in ("group", "supergroup") and not config.CHAT_ID:
        _save_chat_id(chat.id)
        await update.message.reply_text(
            f"群组已绑定\n正在同步项目...",
        )
        await sync_all_projects(context.bot, chat.id)
        await update.message.reply_text("项目同步完成！")
        return True
    return False



async def cmd_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """列出所有 Claude 项目"""
    if not auth_check(update.effective_user.id):
        return
    if await _auto_detect_group(update, context):
        return

    all_projects = projects.discover()
    if not all_projects:
        await update.message.reply_text("未发现 Claude 项目")
        return

    lines = ["<b>Claude 项目</b>\n"]
    for p in all_projects:
        icon = "✓" if p["bound"] else "○"
        lines.append(f"  {icon} <b>{p['name']}</b> ({p['sessions']} sessions)")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ============ /sessions ============

async def cmd_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """在话题内查看/切换历史会话"""
    if not auth_check(update.effective_user.id):
        return

    topic_id = update.message.message_thread_id
    if not topic_id:
        await update.message.reply_text("请在项目话题内使用")
        return

    project_path = projects.get_path(topic_id)
    if not project_path:
        await update.message.reply_text("此话题未绑定项目")
        return

    history = state.scan_project_sessions(project_path)
    if not history:
        await update.message.reply_text("暂无历史会话")
        return

    current = active_sessions.get(topic_id)
    current_sid = current.session_id if current else None

    buttons = []
    for s in history:
        prefix = "▸ " if s["session_id"] == current_sid else ""
        buttons.append([InlineKeyboardButton(
            f"{prefix}{s['summary']}", callback_data=f"r:{s['session_id']}"
        )])

    name = Path(project_path).name
    await update.message.reply_text(
        f"<b>{name}</b> 的历史会话：",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


# ============ /status ============

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update.effective_user.id):
        return
    lines = ["<b>活跃会话</b>\n"]
    if not active_sessions:
        lines.append("（暂无）")
    for key, s in active_sessions.items():
        alive = s.proc and s.proc.returncode is None
        icon = "🟢" if alive else "🔴"
        sid = s.session_id[:8] + "..." if s.session_id else "N/A"
        name = Path(s.cwd).name
        lines.append(f"{icon} {name} ({sid})")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ============ Callback ============

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not auth_check(query.from_user.id):
        return
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    if data == "new":
        topic_id = query.message.message_thread_id
        if topic_id:
            kill_session(topic_id)
            # 标记强制新建（不 resume）
            state.set_resume_id(topic_id, "__new__")
            await query.edit_message_text("新会话已就绪，发消息开始。")
        return

    if data.startswith("pa:") or data.startswith("pd:"):
        # 权限审批按钮
        from src.api_server import handle_permission_callback
        request_id = data[3:]
        decision = "allow" if data.startswith("pa:") else "deny"
        await handle_permission_callback(request_id, decision)
        icon = "✅" if decision == "allow" else "❌"
        await query.edit_message_text(
            f"{icon} 已{('允许' if decision == 'allow' else '拒绝')}",
        )
        return

    if data.startswith("r:"):
        # 恢复历史会话
        session_id = data[2:]
        topic_id = query.message.message_thread_id
        if topic_id:
            state.set_resume_id(topic_id, session_id)
            kill_session(topic_id)
            await query.edit_message_text(
                f"已切换到会话 <code>{session_id[:8]}...</code>\n发消息继续。",
                parse_mode="HTML",
            )


# ============ 消息处理 ============

async def _inject_tmux(pane: str, text: str):
    """通过 tmux send-keys 注入文本到终端"""
    proc = await asyncio.create_subprocess_exec(
        "tmux", "send-keys", "-t", pane, "-l", text,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.wait()
    # 发送 Enter
    proc = await asyncio.create_subprocess_exec(
        "tmux", "send-keys", "-t", pane, "Enter",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.wait()
    log.info(f"[tmux] injected to {pane}: {text[:60]}")


async def _keep_typing(bot, chat_id: int, topic_id: int | None):
    try:
        while True:
            await bot.send_chat_action(chat_id, ChatAction.TYPING, message_thread_id=topic_id)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


async def _send_tg(message, text: str):
    """发送消息到 TG，Markdown 渲染 + 自动分段 + fallback"""
    chunks = split_message(text, render=True)
    for rendered, raw in chunks:
        try:
            await message.reply_text(rendered, parse_mode="HTML")
        except Exception:
            await message.reply_text(raw)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update.effective_user.id):
        user = update.effective_user
        await update.message.reply_text(
            f"未授权。User ID: <code>{user.id}</code>", parse_mode="HTML",
        )
        return

    message = update.message
    if not message or not message.text:
        return

    topic_id = message.message_thread_id
    chat_id = message.chat_id
    text = message.text
    is_private = message.chat.type == "private"

    # 自动检测群组 CHAT_ID
    if not is_private and not config.CHAT_ID:
        _save_chat_id(chat_id)
        await message.reply_text(
            f"群组已绑定 (Chat ID: <code>{chat_id}</code>)\n正在同步项目...",
            parse_mode="HTML",
        )
        await sync_all_projects(context.bot, chat_id)
        await message.reply_text("项目同步完成，请在项目话题内发消息。")
        return

    if not is_private and not topic_id:
        await message.reply_text("请在项目话题内发消息")
        return

    session_key = chat_id if is_private else topic_id

    # 查找工作目录
    cwd = projects.get_path(session_key) if not is_private else state.get_cwd(session_key)
    if not cwd:
        await message.reply_text("此话题未绑定项目。请用 /projects 添加。")
        return

    log.info(f"[msg] key={session_key} proj={Path(cwd).name} text={text[:60]}")

    # 如果有活跃的终端会话，通过 tmux 注入
    from src.api_server import terminal_sessions
    for sid, info in terminal_sessions.items():
        if info.get("topic_id") == session_key and info.get("tmux_pane"):
            await _inject_tmux(info["tmux_pane"], text)
            await message.reply_text("📤 已发送到终端")
            return

    typing_task = asyncio.create_task(_keep_typing(context.bot, chat_id, topic_id))

    try:
        session = active_sessions.get(session_key)
        is_new = (
            session is None
            or session.proc is None
            or session.proc.returncode is not None
            or not session.ready
        )

        if is_new:
            session = await get_or_create_session(session_key, cwd, first_message=text)
        else:
            while session.busy:
                await asyncio.sleep(0.3)
            await send_message(session, text)

        session.busy = True
        turn_count = 0

        # 流式消息状态
        streaming_msg = None       # 当前正在流式更新的 TG 消息
        streaming_text = ""        # 已累积的文本
        last_edit_time = 0         # 上次编辑 TG 消息的时间
        EDIT_INTERVAL = 1.0        # 编辑间隔（秒），避免 TG 限频
        streaming_block_type = None  # 当前 content_block 类型

        async def _flush_streaming():
            """将累积的流式文本刷新到 TG 消息"""
            nonlocal streaming_msg, streaming_text, last_edit_time
            if not streaming_text or not streaming_msg:
                return
            try:
                display = streaming_text[:4000] + ("..." if len(streaming_text) > 4000 else "")
                await streaming_msg.edit_text(f"🤖 {display}")
                last_edit_time = time.time()
            except Exception as e:
                log.debug(f"[stream] edit failed: {e}")

        while True:
            try:
                event = await asyncio.wait_for(session._response_queue.get(), timeout=300)
            except asyncio.TimeoutError:
                await _flush_streaming()
                await message.reply_text("Claude 响应超时")
                break

            if event is None:
                await _flush_streaming()
                active_sessions.pop(session_key, None)
                await message.reply_text("Claude 进程退出，请重新发消息")
                break

            event_type = event.get("type")

            if event_type == "content_block_start":
                block = event.get("content_block", {})
                streaming_block_type = block.get("type")
                if streaming_block_type == "text":
                    # 新的文本块开始 → 发一条占位消息
                    streaming_text = ""
                    streaming_msg = await message.reply_text("🤖 ...")
                    last_edit_time = time.time()
                elif streaming_block_type == "tool_use":
                    turn_count += 1

            elif event_type == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    streaming_text += delta.get("text", "")
                    # 节流：间隔 >= EDIT_INTERVAL 才更新
                    if time.time() - last_edit_time >= EDIT_INTERVAL and streaming_msg:
                        await _flush_streaming()

            elif event_type == "content_block_stop":
                if streaming_block_type == "text" and streaming_msg:
                    # 文本块结束 → 最后一次刷新
                    await _flush_streaming()
                    streaming_msg = None
                streaming_block_type = None

            elif event_type == "assistant":
                # 完整消息事件（兜底：处理非流式场景 + tool_use 汇总）
                for block in event.get("message", {}).get("content", []):
                    block_type = block.get("type")
                    if block_type == "text":
                        # 如果流式已处理过，跳过；否则兜底发送
                        if not streaming_text:
                            reply_text = block.get("text", "").strip()
                            if reply_text:
                                await _send_tg(message, reply_text)
                        streaming_text = ""
                    elif block_type == "tool_use":
                        desc = format_tool_use(block.get("name", "?"), block.get("input", {}))
                        await message.reply_text(f"[{turn_count}] {desc}", parse_mode="HTML")

            elif event_type == "result":
                await _flush_streaming()
                sid = event.get("session_id")
                if sid:
                    session.session_id = sid
                    state.save()
                if event.get("is_error"):
                    await message.reply_text(f"错误: {event.get('result', '?')}")
                log.info(f"[done] key={session_key} turns={event.get('num_turns')}")
                break

        session.busy = False

    except Exception as e:
        log.error(f"[error] key={session_key}: {e}", exc_info=True)
        await message.reply_text(f"出错: {e}")
        bad = active_sessions.pop(session_key, None)
        if bad and bad.proc and bad.proc.returncode is None:
            bad.proc.terminate()
    finally:
        typing_task.cancel()


# ============ 构建 App ============

async def _send_topic_welcome(bot, chat_id: int, topic_id: int, name: str, path: str):
    """话题欢迎消息 + 最近对话上下文"""
    import html as html_lib

    history = state.get_recent_history(path, max_turns=3)

    if not history:
        await bot.send_message(
            chat_id=chat_id, message_thread_id=topic_id,
            text=f"<b>{name}</b>\n直接发消息开始对话。",
            parse_mode="HTML",
        )
        return

    # 渲染最近对话
    lines = [f"<b>{name}</b> — 最近对话：\n"]
    for msg in history:
        role = "👤" if msg["role"] == "user" else "🤖"
        text = html_lib.escape(msg["text"][:200])
        if len(msg["text"]) > 200:
            text += "..."
        lines.append(f"{role} {text}\n")
    lines.append("\n直接发消息继续对话。")

    full_text = "\n".join(lines)
    # 截断避免超 4096
    if len(full_text) > 4000:
        full_text = full_text[:4000] + "\n..."

    await bot.send_message(
        chat_id=chat_id, message_thread_id=topic_id,
        text=full_text,
        parse_mode="HTML",
    )


def _save_chat_id(chat_id: int):
    """将 CHAT_ID 写入 .env"""
    env_file = config.PROJECT_ROOT / ".env"
    if env_file.exists():
        content = env_file.read_text()
        # 替换或追加
        import re
        if re.search(r'^CHAT_ID=', content, re.MULTILINE):
            content = re.sub(r'^CHAT_ID=.*$', f'CHAT_ID={chat_id}', content, flags=re.MULTILINE)
        elif re.search(r'^#\s*CHAT_ID=', content, re.MULTILINE):
            content = re.sub(r'^#\s*CHAT_ID=.*$', f'CHAT_ID={chat_id}', content, flags=re.MULTILINE)
        else:
            content += f"\nCHAT_ID={chat_id}\n"
        env_file.write_text(content)
    config.CHAT_ID = chat_id
    log.info(f"[config] CHAT_ID={chat_id} saved to .env")


async def sync_all_projects(bot, chat_id: int):
    """自动为所有未绑定的近期活跃项目创建 Topic（去重 + 清理无效绑定）"""
    import time
    cutoff = time.time() - 30 * 86400

    # 先验证已有绑定的话题是否还存在，清理无效的
    # 策略：unpinAllForumTopicMessages — 完全静默无副作用，话题不存在时抛 BadRequest
    from telegram.error import BadRequest, TimedOut, NetworkError, RetryAfter
    for topic_id in list(projects.all_bindings().keys()):
        path = projects.get_path(topic_id)
        topic_name = Path(path).name if path else "unknown"
        try:
            await bot.unpin_all_forum_topic_messages(chat_id=chat_id, message_thread_id=topic_id)
        except BadRequest as e:
            err = str(e).lower()
            if "thread" in err and ("not found" in err or "invalid" in err):
                log.info(f"[sync] 话题 {topic_id} 已不存在，清理绑定 ({topic_name}): {e}")
                projects.unbind(topic_id)
            else:
                log.warning(f"[sync] 话题 {topic_id} 验证 BadRequest 但保留绑定: {e} ({topic_name})")
        except (TimedOut, NetworkError, RetryAfter) as e:
            log.warning(f"[sync] 话题 {topic_id} 验证瞬态错误，保留绑定: {e} ({topic_name})")
        except Exception as e:
            log.warning(f"[sync] 话题 {topic_id} 验证未知错误，保留绑定: {e} ({topic_name})")

    all_projects = projects.discover()
    unbound = [p for p in all_projects if not p["bound"] and p.get("mtime", 0) > cutoff]
    if not unbound:
        log.info("[sync] 无新项目需要绑定")
        return

    # 获取群组已有的话题名，用于去重
    existing_topics = {}
    try:
        # TG Bot API 没有 list_forum_topics，但我们可以用 getForumTopicIconStickers 检查
        # 换个思路：直接尝试发消息到已知的 topic，如果失败说明不存在
        # 更简单：检查 bindings 里是否已有同名项目绑定过
        pass
    except Exception:
        pass

    # 用已绑定的项目名去重
    bound_names = set()
    for tid, path in projects.all_bindings().items():
        bound_names.add(Path(path).name)

    log.info(f"[sync] 发现 {len(unbound)} 个未绑定项目，已绑定名: {bound_names}")
    for p in unbound:
        if p["name"] in bound_names:
            log.info(f"[sync] 跳过 {p['name']}（同名话题已存在）")
            continue
        try:
            topic = await bot.create_forum_topic(chat_id=chat_id, name=p["name"])
            projects.bind(topic.message_thread_id, p["path"])
            state.set_cwd(topic.message_thread_id, p["path"])
            # 发送欢迎消息 + 历史会话列表
            await _send_topic_welcome(bot, chat_id, topic.message_thread_id, p["name"], p["path"])
            bound_names.add(p["name"])
            log.info(f"[sync] {p['name']} → topic {topic.message_thread_id}")
            # 间隔避免 flood control
            await asyncio.sleep(2)
        except RetryAfter as e:
            log.warning(f"[sync] {p['name']} 限频，等待 {e.retry_after}s")
            await asyncio.sleep(e.retry_after + 1)
            # 重试一次
            try:
                topic = await bot.create_forum_topic(chat_id=chat_id, name=p["name"])
                projects.bind(topic.message_thread_id, p["path"])
                state.set_cwd(topic.message_thread_id, p["path"])
                await _send_topic_welcome(bot, chat_id, topic.message_thread_id, p["name"], p["path"])
                bound_names.add(p["name"])
                log.info(f"[sync] {p['name']} → topic {topic.message_thread_id} (重试成功)")
            except Exception as e2:
                log.warning(f"[sync] {p['name']} 重试也失败: {e2}")
        except Exception as e:
            log.warning(f"[sync] {p['name']} 创建话题失败: {e}")


def create_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    async def post_init(application):
        await application.bot.set_my_commands([
            BotCommand("projects", "项目列表"),
            BotCommand("sessions", "历史会话"),
            BotCommand("status", "活跃会话"),
            BotCommand("whoami", "你的 ID"),
        ])
        # 如果已有 CHAT_ID，启动时同步 + 定时同步
        if config.CHAT_ID:
            await sync_all_projects(application.bot, config.CHAT_ID)

        # 每 5 分钟扫描新项目
        async def periodic_sync():
            while True:
                await asyncio.sleep(300)
                if config.CHAT_ID:
                    await sync_all_projects(application.bot, config.CHAT_ID)

        asyncio.create_task(periodic_sync())
    app.post_init = post_init

    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("sessions", cmd_sessions))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & (filters.ChatType.SUPERGROUP | filters.ChatType.PRIVATE),
            handle_message,
        )
    )

    return app
