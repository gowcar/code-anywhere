"""HTTP API Server — 接收 Claude Code Hook 回调

端点：
  POST /session_start  — 终端 Claude 启动
  POST /session_stop   — 终端 Claude 停止
  POST /notification   — Claude 输出通知
  POST /permission     — 工具权限审批请求
  GET  /health         — 健康检查
"""

import asyncio
import json
import logging
import uuid
from pathlib import Path

from aiohttp import web

from src.config import BOT_PORT
from src import projects
from src import state
import src.config as config

log = logging.getLogger("code-anywhere.api")

# 权限审批等待队列：request_id → {event, decision}
pending_permissions: dict[str, dict] = {}

# 终端活跃会话：session_id → {topic_id, chat_id, tmux_pane, ...}
terminal_sessions: dict[str, dict] = {}

# Bot 实例引用（启动时设置）
_bot = None
_tg_app = None


def set_bot(bot, tg_app):
    global _bot, _tg_app
    _bot = bot
    _tg_app = tg_app


async def handle_session_start(request: web.Request) -> web.Response:
    """终端 Claude 启动 → 找到对应话题 → 启动 JSONL 监听"""
    data = await request.json()
    session_id = data.get("session_id")
    cwd = data.get("cwd", "")
    source = data.get("source", "startup")
    model = data.get("model", "")
    tmux_pane = data.get("tmux_pane", "")
    transcript_path = data.get("transcript_path", "")

    log.info(f"[session_start] session={session_id} cwd={cwd} source={source} tmux={tmux_pane}")

    # 跳过 Bot 自身项目
    if cwd == str(config.PROJECT_ROOT.resolve()):
        return web.json_response({"ok": True})

    if not _bot or not config.CHAT_ID:
        return web.json_response({"ok": True})

    topic_id = projects.get_topic(cwd)
    if not topic_id:
        log.info(f"[session_start] 项目 {cwd} 未绑定话题，跳过")
        return web.json_response({"ok": True})

    # 非交互式会话（无 tmux pane）跳过 — 由其他服务自行管理（如 claude-agent）
    if not tmux_pane:
        log.info(f"[session_start] {Path(cwd).name} 无 tmux pane，跳过（非交互式）")
        return web.json_response({"ok": True})

    # 记录终端会话
    terminal_sessions[session_id] = {
        "topic_id": topic_id,
        "chat_id": config.CHAT_ID,
        "cwd": cwd,
        "tmux_pane": tmux_pane,
        "model": model,
        "transcript_path": transcript_path,
    }

    project_name = Path(cwd).name
    log.info(f"[session_start] {project_name} bound to topic {topic_id}")

    # 启动 JSONL 监听（流式同步终端输出到 TG）
    if transcript_path:
        # 取消该 topic 已有的 watcher
        old_task = _watchers.pop(topic_id, None)
        if old_task:
            old_task.cancel()
        _watchers[topic_id] = asyncio.create_task(
            _watch_transcript(session_id, transcript_path, topic_id, config.CHAT_ID)
        )

    return web.json_response({"ok": True})


# JSONL Watcher 任务
_watchers: dict[int, asyncio.Task] = {}


async def _watch_transcript(session_id: str, path: str, topic_id: int, chat_id: int):
    """监听 JSONL 文件，流式转发终端输出到 TG"""
    import html as html_lib
    from src.renderer import format_tool_use, split_message, wrap_collapsible

    seen_ids = set()
    last_pos = 0

    # 跳到文件末尾（只监听新内容）
    try:
        last_pos = Path(path).stat().st_size
    except Exception:
        pass

    log.info(f"[watcher] start session={session_id} path={path}")

    async def _pane_alive(sid):
        """检查 session 对应的 tmux pane 是否还活着"""
        info = terminal_sessions.get(sid)
        if not info:
            return False
        pane = info.get("tmux_pane")
        if not pane:
            return sid in terminal_sessions
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux", "has-session", "-t", pane,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            return proc.returncode == 0
        except Exception:
            return False

    pane_check_counter = 0
    try:
        while True:
            await asyncio.sleep(0.5)
            # 每 30 秒检查一次 pane 是否还活着
            pane_check_counter += 1
            if pane_check_counter >= 60:
                pane_check_counter = 0
                if not await _pane_alive(session_id):
                    log.info(f"[watcher] pane dead, stopping session={session_id}")
                    terminal_sessions.pop(session_id, None)
                    break
            try:
                file_size = Path(path).stat().st_size
            except Exception:
                continue
            if file_size <= last_pos:
                continue

            diff = file_size - last_pos
            log.info(f"[watcher] new data +{diff} bytes")

            with open(path, "r") as f:
                f.seek(last_pos)
                new_data = f.read()
                last_pos = f.tell()

            for line in new_data.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type")
                event_uuid = event.get("uuid")

                # 用 type+uuid 去重（file-history-snapshot 的 messageId 会和 user 的 uuid 重复）
                dedup_key = f"{event_type}:{event_uuid}" if event_uuid else None
                if dedup_key:
                    if dedup_key in seen_ids:
                        continue
                    seen_ids.add(dedup_key)

                if event_type in ("user", "assistant"):
                    log.info(f"[watcher] {event_type} event id={event_uuid}")

                try:
                    if event_type == "user":
                        # 用户在终端输入的内容 → 折叠显示
                        content = event.get("message", {}).get("content", "")
                        text = ""
                        if isinstance(content, str):
                            text = content
                        elif isinstance(content, list):
                            for c in content:
                                if c.get("type") == "text":
                                    text = c["text"]
                                    break
                        if text.strip():
                            truncated = html_lib.escape(text[:500])
                            await _bot.send_message(
                                chat_id=chat_id, message_thread_id=topic_id,
                                text=wrap_collapsible(f"👤 {truncated}"),
                                parse_mode="HTML",
                            )

                    elif event_type == "assistant":
                        content = event.get("message", {}).get("content", [])
                        for block in content:
                            if block.get("type") == "text":
                                text = block.get("text", "").strip()
                                if text:
                                    chunks = split_message(text, render=True)
                                    for rendered, raw in chunks:
                                        try:
                                            await _bot.send_message(
                                                chat_id=chat_id, message_thread_id=topic_id,
                                                text=f"🤖 {rendered}",
                                                parse_mode="HTML",
                                            )
                                        except Exception:
                                            await _bot.send_message(
                                                chat_id=chat_id, message_thread_id=topic_id,
                                                text=f"🤖 {raw}",
                                            )
                            elif block.get("type") == "tool_use":
                                desc = format_tool_use(block.get("name", "?"), block.get("input", {}))
                                await _bot.send_message(
                                    chat_id=chat_id, message_thread_id=topic_id,
                                    text=wrap_collapsible(desc),
                                    parse_mode="HTML",
                                )
                except Exception as e:
                    log.error(f"[watcher] TG 发送失败: {e}", exc_info=True)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        log.error(f"[watcher] error: {e}")
    finally:
        log.info(f"[watcher] stop session={session_id}")


async def handle_session_stop(request: web.Request) -> web.Response:
    """终端 Claude 一轮结束（不是进程退出）"""
    data = await request.json()
    session_id = data.get("session_id")
    stop_reason = data.get("stop_reason", "")

    log.info(f"[session_stop] session={session_id} reason={stop_reason}")

    # 不移除 session，不杀 watcher
    # Stop Hook 是每轮回复结束都会触发，不代表进程退出
    # Watcher 会通过文件监听继续工作

    return web.json_response({"ok": True})


async def handle_notification(request: web.Request) -> web.Response:
    """Claude 输出通知 → 转发到 TG"""
    data = await request.json()
    session_id = data.get("session_id")
    message = data.get("message", "")
    title = data.get("title", "")

    if not message:
        return web.json_response({"ok": True})

    log.info(f"[notification] session={session_id} title={title} msg={message[:80]}")

    info = terminal_sessions.get(session_id)
    if not info or not _bot:
        return web.json_response({"ok": True})

    try:
        import html
        text = html.escape(message[:4000])
        if title:
            text = f"<b>{html.escape(title)}</b>\n{text}"
        await _bot.send_message(
            chat_id=info["chat_id"],
            message_thread_id=info["topic_id"],
            text=text,
            parse_mode="HTML",
        )
    except Exception as e:
        log.warning(f"[notification] TG 发送失败: {e}")

    return web.json_response({"ok": True})


async def handle_permission(request: web.Request) -> web.Response:
    """工具权限审批 → TG 按钮 → 等用户点击 → 返回决策"""
    data = await request.json()
    session_id = data.get("session_id")
    tool_name = data.get("tool_name", "?")
    description = data.get("description", "")
    chat_id = data.get("chat_id")
    thread_id = data.get("thread_id")
    timeout = data.get("timeout", 120)
    is_terminal = not chat_id  # 没有 TELEGRAM_CHAT_ID → 终端会话

    # 终端会话从 terminal_sessions 获取 topic
    if not thread_id and session_id in terminal_sessions:
        info = terminal_sessions[session_id]
        chat_id = info["chat_id"]
        thread_id = info["topic_id"]

    if not _bot or not chat_id or not thread_id:
        # Bot 不可达 → 不干预，Claude 走默认权限
        return web.json_response({"decision": "allow"})

    request_id = str(uuid.uuid4())[:8]
    event = asyncio.Event()
    pending_permissions[request_id] = {"event": event, "decision": None, "msg": None}

    source_tag = "🖥️ 终端" if is_terminal else "🤖 Bot"
    log.info(f"[permission] req={request_id} tool={tool_name} source={source_tag} timeout={timeout}s")

    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        import html
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ 允许", callback_data=f"pa:{request_id}"),
            InlineKeyboardButton("❌ 拒绝", callback_data=f"pd:{request_id}"),
        ]])
        msg = await _bot.send_message(
            chat_id=int(chat_id),
            message_thread_id=int(thread_id),
            text=f"🔐 <b>权限请求</b> ({source_tag})\n\n{html.escape(description[:500])}",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        pending_permissions[request_id]["msg"] = msg
    except Exception as e:
        log.warning(f"[permission] TG 发送失败: {e}")
        pending_permissions.pop(request_id, None)
        return web.json_response({"decision": "allow"})

    # 等待用户决策
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        decision = pending_permissions[request_id]["decision"]
    except asyncio.TimeoutError:
        if is_terminal:
            # 终端会话超时 → fallback 到 CLI，更新 TG 消息
            decision = "fallback"
            try:
                await msg.edit_text("🔐 <b>权限请求</b> (⏰ 已超时，等待 CLI 处理)", parse_mode="HTML")
            except Exception:
                pass
        else:
            # Bot 会话超时 → deny
            decision = "deny"
            try:
                await msg.edit_text("🔐 <b>权限请求</b> (⏰ 超时自动拒绝)", parse_mode="HTML")
            except Exception:
                pass
    finally:
        pending_permissions.pop(request_id, None)

    log.info(f"[permission] req={request_id} decision={decision}")
    return web.json_response({"decision": decision})


async def handle_permission_callback(request_id: str, decision: str):
    """TG 按钮回调 → 唤醒等待的权限请求"""
    perm = pending_permissions.get(request_id)
    if perm:
        perm["decision"] = decision
        perm["event"].set()


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({
        "status": "ok",
        "terminal_sessions": len(terminal_sessions),
        "pending_permissions": len(pending_permissions),
    })


def create_api_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/session_start", handle_session_start)
    app.router.add_post("/session_stop", handle_session_stop)
    app.router.add_post("/notification", handle_notification)
    app.router.add_post("/permission", handle_permission)
    app.router.add_get("/health", handle_health)
    return app


async def start_api_server():
    app = create_api_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", BOT_PORT)
    await site.start()
    log.info(f"[api] HTTP 服务启动 http://127.0.0.1:{BOT_PORT}")

    # 扫描已经在运行的终端 Claude 会话，补建 watcher
    await _recover_active_sessions()


async def _recover_active_sessions():
    """Bot 启动时，检测已运行的 Claude 终端会话并补建 watcher"""
    if not _bot or not config.CHAT_ID:
        return

    import subprocess

    # 找所有 tmux pane 里在跑 claude 的进程
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_id} #{pane_current_command} #{pane_current_path}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return
    except Exception:
        return

    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        pane_id, cmd, cwd = parts

        # 跳过 Bot 自身项目（避免循环依赖）
        self_dir = str(config.PROJECT_ROOT.resolve())
        if cwd == self_dir:
            continue

        # 找对应的话题（只关心绑定了项目的 pane）
        topic_id = projects.get_topic(cwd)
        if not topic_id:
            continue

        # 找最新的 JSONL 文件
        dir_hash = cwd.replace("/", "-")
        proj_path = Path.home() / ".claude" / "projects" / dir_hash
        if not proj_path.is_dir():
            continue

        jsonl_files = sorted(proj_path.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True)
        if not jsonl_files:
            continue

        latest = jsonl_files[0]
        session_id = latest.stem

        # 跳过已经在监听的
        if session_id in terminal_sessions:
            continue

        terminal_sessions[session_id] = {
            "topic_id": topic_id,
            "chat_id": config.CHAT_ID,
            "cwd": cwd,
            "tmux_pane": pane_id,
            "model": "",
            "transcript_path": str(latest),
        }

        _watchers[topic_id] = asyncio.create_task(
            _watch_transcript(session_id, str(latest), topic_id, config.CHAT_ID)
        )
        log.info(f"[recover] {Path(cwd).name} → pane={pane_id} session={session_id[:12]}")
