"""Telegram Bot — 命令路由 + 消息处理"""

import asyncio
import hashlib
import html as html_lib
import json
import logging
import os
import time
from datetime import datetime
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
from src.renderer import split_message, format_tool_use, send_ephemeral
from src import state
from src import projects
from src import recon

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


# ============ /trust ============

# callback_data 限制 64 字节，路径塞不下 → 短 hash 映射
_trust_id_to_path: dict[str, str] = {}


def _path_id(p: str) -> str:
    h = hashlib.sha256(p.encode()).hexdigest()[:10]
    _trust_id_to_path[h] = p
    return h


def _trust_keyboard() -> InlineKeyboardMarkup:
    bound = [p for p in projects.discover() if p["bound"]]
    rows = []
    for p in bound:
        trusted = state.is_trusted(p["path"])
        icon = "✅" if trusted else "❌"
        rows.append([InlineKeyboardButton(
            f"{icon} {p['name']}",
            callback_data=f"tt:{_path_id(p['path'])}",
        )])
    return InlineKeyboardMarkup(rows)


async def cmd_trust(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """项目信任开关：信任后跳过 TG 审批，自动允许所有工具"""
    if not auth_check(update.effective_user.id):
        return
    bound = [p for p in projects.discover() if p["bound"]]
    if not bound:
        await update.message.reply_text("暂无已绑定的项目")
        return
    text = (
        "<b>项目信任开关</b>\n"
        "✅ 已信任 → 自动允许所有工具调用\n"
        "❌ 未信任 → 需要 TG 审批"
    )
    await update.message.reply_text(
        text,
        reply_markup=_trust_keyboard(),
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
    data = query.data
    chat_id = query.message.chat_id

    # 权限审批：先唤醒等待中的请求（最关键），再做 UI 反馈
    if data.startswith("pa:") or data.startswith("pd:"):
        from src.api_server import handle_permission_callback
        request_id = data[3:]
        decision = "allow" if data.startswith("pa:") else "deny"
        await handle_permission_callback(request_id, decision)
        icon = "✅" if decision == "allow" else "❌"
        # query.answer 和 edit 都可能因 query 过期失败 — 容错处理
        try:
            await query.answer()
        except Exception as e:
            log.warning(f"[callback] answer 失败（忽略）: {e}")
        try:
            await query.edit_message_text(
                f"{icon} 已{('允许' if decision == 'allow' else '拒绝')}",
            )
        except Exception as e:
            log.warning(f"[callback] edit 失败（忽略）: {e}")
        return

    try:
        await query.answer()
    except Exception as e:
        log.warning(f"[callback] answer 失败（忽略）: {e}")

    # 项目信任开关
    if data.startswith("tt:"):
        path_id = data[3:]
        path = _trust_id_to_path.get(path_id)
        if not path:
            try:
                await query.answer("ID 已失效，请重新 /trust", show_alert=True)
            except Exception:
                pass
            return
        new_val = not state.is_trusted(path)
        state.set_trusted(path, new_val)
        try:
            await query.answer(f"{'已信任' if new_val else '已取消信任'}: {Path(path).name}")
        except Exception:
            pass
        try:
            await query.edit_message_reply_markup(reply_markup=_trust_keyboard())
        except Exception as e:
            log.warning(f"[trust] edit markup 失败（忽略）: {e}")
        return

    if data == "new":
        topic_id = query.message.message_thread_id
        if topic_id:
            kill_session(topic_id)
            # 标记强制新建（不 resume）
            state.set_resume_id(topic_id, "__new__")
            await query.edit_message_text("新会话已就绪，发消息开始。")
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
    """通过 tmux 把文本注入到 claude REPL，再回车提交。

    多行文本走 bracketed paste（set-buffer + paste-buffer -p）：claude REPL 会
    把它识别成「一次粘贴」，内嵌换行不再被当成逐行提交 —— 否则多行脚本会在
    第一个换行处就被拆断、只提交半截（长期潜伏的老坑：单行没事、多行 heredoc
    一发就散架）。单行仍走 send-keys -l，行为完全不变、零风险。
    """
    mode = "paste" if "\n" in text else "keys"
    if mode == "paste":
        buf = "ca_inject"  # 具名 buffer，用完即删，不污染默认 buffer 栈
        proc = await asyncio.create_subprocess_exec(
            "tmux", "set-buffer", "-b", buf, "--", text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
        proc = await asyncio.create_subprocess_exec(
            "tmux", "paste-buffer", "-p", "-d", "-b", buf, "-t", pane,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
        await asyncio.sleep(0.1)  # 给 REPL 一点时间把粘贴内容落进输入框
    else:
        proc = await asyncio.create_subprocess_exec(
            "tmux", "send-keys", "-t", pane, "-l", text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
    # 发送 Enter 提交（bracketed paste 的换行不提交，只有这个 Enter 提交整块）
    proc = await asyncio.create_subprocess_exec(
        "tmux", "send-keys", "-t", pane, "Enter",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.wait()
    log.info(f"[tmux] injected to {pane} ({mode}): {text[:60]}")


_SHELL_CMDS = {"fish", "bash", "zsh", "sh", "tcsh", "csh", "ksh", "dash"}


async def _pane_has_claude(pane_id: str) -> bool:
    """检测 pane 的 tty 上是否有 claude 进程在跑

    macOS 上 `pane_current_command` 不可靠（fish→claude 可能返回 fish），
    因此走 ps 查 tty 进程列表，看是否存在名为 `claude` 的进程。
    """
    if not pane_id:
        return False
    try:
        p1 = await asyncio.create_subprocess_exec(
            "tmux", "display-message", "-p", "-t", pane_id, "#{pane_tty}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await p1.communicate()
        tty = (out.decode(errors="ignore").strip())
        if not tty:
            return False
        tty_name = tty.rsplit("/", 1)[-1]
        p2 = await asyncio.create_subprocess_exec(
            "ps", "-t", tty_name, "-o", "comm=",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out2, _ = await p2.communicate()
        for line in out2.decode(errors="ignore").splitlines():
            name = line.strip().rsplit("/", 1)[-1]
            if name == "claude":
                return True
        return False
    except Exception:
        return False


async def _ensure_pane_for_project(cwd: str, chat_id: int, topic_id: int | None) -> str | None:
    """在 tmux 中给 cwd 起一个 claude pane。复用现有 tmux session（第一个可见的），没有则建一个。

    步骤：
      1. new-window -c <cwd> -P -F '#{pane_id}' claude 拿新 pane_id
      2. 轮询 _pane_has_claude(pane_id)，最多 15s
      3. hook 触发后 pane 自带 @ca_project 标签，下轮 _find_pane_for_project 也能命中
    """
    try:
        norm_cwd = str(Path(cwd).resolve())
    except Exception:
        return None

    # 找一个已存在的 tmux session，没有就建
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux", "list-sessions", "-F", "#{session_name}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        sessions = [s for s in out.decode(errors="ignore").splitlines() if s.strip()]
    except Exception:
        sessions = []

    tmux_session = sessions[0] if sessions else "kin"
    if not sessions:
        try:
            proc = await asyncio.create_subprocess_exec(
                "tmux", "new-session", "-d", "-s", tmux_session, "-c", str(Path.home()),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except Exception as e:
            log.warning(f"[ensure_pane] tmux new-session failed: {e}")
            return None

    # 起 new-window 跑 claude；-P -F 拿新 pane id
    # 关键：tmux server env 通常无 CLAUDE_CODE_OAUTH_TOKEN，claude 会秒退 → 用 -e 从当前进程 env 显式传
    proj_name = Path(norm_cwd).name
    import os as _os
    tmux_env_args: list[str] = []
    for env_key in ("CLAUDE_CODE_OAUTH_TOKEN",):
        v = _os.environ.get(env_key)
        if v:
            tmux_env_args += ["-e", f"{env_key}={v}"]
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux", "new-window", "-t", tmux_session, "-c", norm_cwd, "-n", proj_name,
            *tmux_env_args,
            "-P", "-F", "#{pane_id}", "claude",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            log.warning(f"[ensure_pane] tmux new-window failed: {err.decode(errors='ignore')}")
            return None
        pane_id = out.decode(errors="ignore").strip()
    except Exception as e:
        log.warning(f"[ensure_pane] tmux new-window error: {e}")
        return None

    if not pane_id:
        return None
    log.info(f"[ensure_pane] created pane={pane_id} for {proj_name}")

    # 等 claude 起来（最多 15s），且要求持续存在 ≥ 2s（防秒退 fallback 到 fish 的假 ready）
    stable_since: float | None = None
    ready = False
    for _ in range(30):
        await asyncio.sleep(0.5)
        alive = await _pane_has_claude(pane_id)
        if alive:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= 2.0:
                log.info(f"[ensure_pane] pane={pane_id} claude ready & stable")
                ready = True
                break
        else:
            if stable_since is not None:
                log.warning(f"[ensure_pane] pane={pane_id} claude 秒退（可能 auth 失败），放弃")
                return None
            stable_since = None

    if not ready:
        log.warning(f"[ensure_pane] pane={pane_id} claude 未在 15s 内就绪")
        return None

    # 主动给这个 pane 注册 watcher — 不依赖 SessionStart hook 自动触发
    # （新 pane 起 claude 时 hook 不总是能触发，具体原因未知；直接注册最可靠）
    try:
        from src.api_server import register_pane_watcher
        await register_pane_watcher(pane_id, norm_cwd)
    except Exception as e:
        log.warning(f"[ensure_pane] register_pane_watcher failed: {e}")

    return pane_id


async def _find_pane_for_project(cwd: str) -> str | None:
    """实时扫 tmux 找匹配 project cwd 的 pane

    优先级：
    0. terminal_sessions 中该 cwd 对应的活跃 watcher 的 pane（与 watcher 选的同一个，保证注入与监听一致）
    1. pane 带 @ca_project 标签且解析后等于 cwd（hook 启动时打上的，明确归属）
    2. pane_current_path 等于 cwd 或在 cwd 之下（fallback：老 pane 没标签时）
    多个匹配按 pane_id 排序取第一个；pane 关闭后 tmux 自动清理，无需 GC。
    """
    try:
        norm_cwd = str(Path(cwd).resolve())
    except Exception:
        return None

    # 优先级 0：与 watcher 注册的 pane 对齐（避免一个项目多个 pane 时注入与监听走不同 pane）
    try:
        from src.api_server import terminal_sessions
        for info in terminal_sessions.values():
            info_cwd = info.get("cwd") or ""
            if not info_cwd:
                continue
            try:
                if str(Path(info_cwd).resolve()) != norm_cwd:
                    continue
            except Exception:
                continue
            pane = info.get("tmux_pane") or ""
            if pane and await _pane_has_claude(pane):
                return pane
    except Exception:
        pass

    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux", "list-panes", "-aF",
            "#{pane_id}\t#{@ca_project}\t#{pane_current_path}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
    except Exception as e:
        log.warning(f"[find_pane] tmux list-panes failed: {e}")
        return None

    # 先按标签 / cwd 收候选；alive 判定用 ps（macOS 上 pane_current_command 不可靠）
    raw_candidates: list[tuple[int, str]] = []  # (priority, pane_id)
    for line in out.decode(errors="ignore").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        pane_id = parts[0]
        ca_project = parts[1]
        current_path = parts[2]
        # 优先级 1：标签匹配
        if ca_project:
            try:
                if str(Path(ca_project).resolve()) == norm_cwd:
                    raw_candidates.append((1, pane_id))
                    continue
            except Exception:
                pass
        # 优先级 2：cwd 包含匹配
        if current_path:
            try:
                cp = str(Path(current_path).resolve())
                if cp == norm_cwd or cp.startswith(norm_cwd.rstrip("/") + "/"):
                    raw_candidates.append((2, pane_id))
            except Exception:
                pass

    # 用 ps 二次过滤：只保留 tty 上真有 claude 进程的 pane
    candidates: list[tuple[int, str]] = []
    for prio, pid in raw_candidates:
        if await _pane_has_claude(pid):
            candidates.append((prio, pid))

    if not candidates:
        return None
    candidates.sort()
    if len(candidates) > 1:
        log.warning(f"[find_pane] {cwd} 匹配到 {len(candidates)} 个 pane: {candidates} → 选 {candidates[0][1]}")
    return candidates[0][1]


async def _keep_typing(bot, chat_id: int, topic_id: int | None):
    try:
        while True:
            await bot.send_chat_action(chat_id, ChatAction.TYPING, message_thread_id=topic_id)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


# 终端注入路径的 typing 任务：topic_id → Task
# 由 bot 注入 tmux 时启动，由 api_server watcher 收到 assistant 文本块或 session_stop 时停止
pending_typing: dict[int, asyncio.Task] = {}


def start_typing(bot, chat_id: int, topic_id: int):
    """为终端注入路径启动 typing。若已有任务，先取消再启新的（重置计时）。"""
    old = pending_typing.pop(topic_id, None)
    if old and not old.done():
        old.cancel()
    pending_typing[topic_id] = asyncio.create_task(
        _keep_typing(bot, chat_id, topic_id)
    )


def stop_typing(topic_id: int):
    """停止指定 topic 的 typing 任务（由 watcher / session_stop 调用）。"""
    task = pending_typing.pop(topic_id, None)
    if task and not task.done():
        task.cancel()


async def _send_tg(message, text: str):
    """发送消息到 TG，Markdown 渲染 + 自动分段 + fallback"""
    chunks = split_message(text, render=True)
    for rendered, raw in chunks:
        try:
            await message.reply_text(rendered, parse_mode="HTML")
        except Exception:
            await message.reply_text(raw)


MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# 文件类型分类
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}
_PDF_EXT = {".pdf"}
_TEXT_EXT = {
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".toml", ".xml", ".html",
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java", ".c", ".cpp",
    ".h", ".hpp", ".rb", ".php", ".sh", ".bash", ".fish", ".zsh",
    ".sql", ".r", ".swift", ".kt", ".scala", ".lua", ".vim",
    ".css", ".scss", ".less", ".svg", ".ini", ".cfg", ".conf", ".env",
    ".gitignore", ".dockerignore", ".editorconfig",
    ".log", ".diff", ".patch",
}
_OFFICE_EXT = {".docx", ".xlsx", ".pptx", ".doc", ".xls", ".ppt", ".odt", ".ods", ".odp"}


async def _download_attachment(message, context, cwd: str) -> tuple[str | None, str | None, str]:
    """下载 TG 附件到项目 .attachments/ 目录

    Returns: (local_path, file_ext, error_msg)
    """
    # 确定文件对象和文件名
    file_obj = None
    file_name = None

    if message.photo:
        # TG 图片：取最大尺寸
        file_obj = message.photo[-1]
        file_name = f"photo_{file_obj.file_unique_id}.jpg"
    elif message.document:
        doc = message.document
        if doc.file_size and doc.file_size > MAX_FILE_SIZE:
            return None, None, f"文件太大（{doc.file_size // 1024 // 1024}MB），限制 10MB"
        file_obj = doc
        file_name = doc.file_name or f"file_{doc.file_unique_id}"
    elif message.video:
        return None, None, "暂不支持视频文件"
    elif message.audio or message.voice:
        return None, None, "暂不支持音频文件"
    elif message.sticker:
        return None, None, ""  # 贴纸静默忽略

    if not file_obj:
        return None, None, ""

    # 获取文件信息（带重试，防止偶发超时）
    tg_file = None
    for attempt in range(3):
        try:
            tg_file = await file_obj.get_file(read_timeout=30, connect_timeout=15)
            break
        except Exception as e:
            if attempt < 2:
                log.warning(f"[attachment] get_file 第{attempt+1}次失败: {e}, 重试...")
                await asyncio.sleep(1)
            else:
                return None, None, f"获取文件失败（重试3次）: {e}"

    if tg_file.file_size and tg_file.file_size > MAX_FILE_SIZE:
        return None, None, f"文件太大（{tg_file.file_size // 1024 // 1024}MB），限制 10MB"

    # 构建本地路径
    attachments_dir = Path(cwd) / ".attachments"
    attachments_dir.mkdir(exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = file_name.replace("/", "_").replace(" ", "_")
    local_name = f"{ts}_{safe_name}"
    local_path = attachments_dir / local_name

    try:
        await tg_file.download_to_drive(str(local_path), read_timeout=30, connect_timeout=15)
    except Exception as e:
        return None, None, f"下载文件失败: {e}"

    ext = Path(file_name).suffix.lower()
    rel_path = f".attachments/{local_name}"
    log.info(f"[attachment] saved {rel_path} ({tg_file.file_size or '?'} bytes)")
    # 控上下文：清理旧附件，避免 .attachments 膨胀（图的价值应已由 claude 落盘成文字）
    n = recon.cleanup_attachments(cwd)
    if n:
        log.info(f"[attachment] 清理 {n} 个旧附件")
    return rel_path, ext, ""


def _build_file_prompt(rel_path: str, ext: str, caption: str) -> str:
    """用 @path 引用文件。Claude CLI 2.x 会自动 attach：图片→vision、文本/PDF→inline。

    不再走 Read 工具，省一跳 tool call + 免 permission 弹窗。
    caption 为空时用默认询问。
    """
    ref = f"@{rel_path}"
    if caption:
        return f"{ref}\n{caption}"
    return f"{ref}\n看看这个"


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update.effective_user.id):
        user = update.effective_user
        await update.message.reply_text(
            f"未授权。User ID: <code>{user.id}</code>", parse_mode="HTML",
        )
        return

    message = update.message
    if not message:
        return

    topic_id = message.message_thread_id
    chat_id = message.chat_id
    is_private = message.chat.type == "private"
    has_file = bool(message.photo or message.document or message.video
                    or message.audio or message.voice or message.sticker)

    if not message.text and not has_file:
        return

    text = message.text or message.caption or ""

    # 提取被引用消息的内容（用户回复某条消息时）
    quoted_text = ""
    if message.reply_to_message and message.reply_to_message.message_id != topic_id:
        # 排除回复话题创建消息（message_id == topic_id）
        reply = message.reply_to_message
        if reply.text:
            quoted_text = reply.text
        elif reply.caption:
            quoted_text = reply.caption

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

    # 处理文件附件
    if has_file:
        rel_path, ext, err = await _download_attachment(message, context, cwd)
        if err:
            await message.reply_text(err)
            return
        if not rel_path:
            # 静默忽略（如贴纸）
            return
        text = _build_file_prompt(rel_path, ext, text)
        # 给用户反馈：文件已收到，正在处理
        await message.reply_text("📎 文件已接收，正在读取分析...")

    # 拼接引用内容
    if quoted_text:
        truncated_quote = quoted_text[:2000]
        if len(quoted_text) > 2000:
            truncated_quote += "..."
        if text:
            text = f"[引用消息]\n{truncated_quote}\n[/引用消息]\n\n{text}"
        else:
            text = f"用户引用了以下消息，请分析：\n\n{truncated_quote}"

    if not text:
        return

    log.info(f"[msg] key={session_key} proj={Path(cwd).name} text={text[:80]}")

    # 找/建 tmux pane 并注入。定位约束：TG/web 是远控入口，不自己 spawn claude subprocess
    target_pane = await _find_pane_for_project(cwd)
    if not target_pane:
        target_pane = await _ensure_pane_for_project(cwd, chat_id, message.message_thread_id)
    if not target_pane:
        await message.reply_text(
            "⚠️ 起 claude pane 失败。请手动 tmux new-window 到此项目跑 claude。"
        )
        return

    # 标记：来源=TG（watcher 跳过 user event ephemeral 避免重复）+ 本轮首条 assistant text 应 quote-reply 到这条用户消息
    if not is_private and topic_id:
        from src.api_server import mark_tg_input, mark_tg_reply_target
        mark_tg_input(topic_id, text)
        mark_tg_reply_target(topic_id, message.message_id)
    await _inject_tmux(target_pane, text)
    start_typing(context.bot, chat_id, session_key)


# ============ 构建 App ============

async def _send_topic_welcome(bot, chat_id: int, topic_id: int, name: str, path: str):
    """话题欢迎消息 + 最近对话上下文"""

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


async def _purge_topic_state(topic_id: int, path: str | None):
    """彻底清理 topic 相关的所有持久化与运行时状态"""
    projects.unbind(topic_id)
    state.topic_dirs.pop(topic_id, None)
    if path:
        try:
            state.trusted_dirs.discard(state._norm(path))
        except Exception:
            pass
        state.trusted_dirs.discard(path)
    try:
        state.save()
    except Exception as e:
        log.warning(f"[purge] state.save 失败: {e}")
    from src.claude_runner import active_sessions, kill_session
    if topic_id in active_sessions:
        kill_session(topic_id)
    from src.api_server import _watchers, terminal_sessions
    task = _watchers.pop(topic_id, None)
    if task:
        task.cancel()
    for sid in list(terminal_sessions.keys()):
        if terminal_sessions[sid].get("topic_id") == topic_id:
            terminal_sessions.pop(sid, None)


async def sync_all_projects(bot, chat_id: int):
    """自动为所有未绑定的近期活跃项目创建 Topic（去重 + 清理无效绑定）"""
    import time
    cutoff = time.time() - 30 * 86400

    from telegram.error import BadRequest, TimedOut, NetworkError, RetryAfter

    # 工作目录已删除 → 删 TG topic + 清 state（保持与磁盘一致）
    for topic_id in list(projects.all_bindings().keys()):
        path = projects.get_path(topic_id)
        if not path or Path(path).is_dir():
            continue
        topic_name = Path(path).name
        log.info(f"[sync] 工作目录消失 → 清理 topic {topic_id} ({topic_name}): {path}")
        deleted_ok = True
        try:
            await bot.delete_forum_topic(chat_id=chat_id, message_thread_id=topic_id)
        except BadRequest as e:
            err = str(e).lower()
            if "thread" in err and ("not found" in err or "invalid" in err):
                pass  # 已经被手动删了，照样清 state
            else:
                log.warning(f"[sync] delete_forum_topic BadRequest，仍清理 state: {e}")
        except (TimedOut, NetworkError, RetryAfter) as e:
            log.warning(f"[sync] delete_forum_topic 瞬态错误，下次再试: {e}")
            deleted_ok = False
        except Exception as e:
            log.warning(f"[sync] delete_forum_topic 失败，仍清理 state: {e}")
        if deleted_ok:
            await _purge_topic_state(topic_id, path)

    # 再验证 TG 端 topic 是否还存在（用户可能手动删了 topic 但磁盘还在）
    # 策略：unpinAllForumTopicMessages — 完全静默无副作用，话题不存在时抛 BadRequest
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

    # 外脑：为所有项目确保 recon/ 骨架（幂等，绝不覆盖已有文件）
    for _p in all_projects:
        if recon.ensure_skeleton(_p["path"]):
            log.info(f"[recon] 新建外脑骨架: {_p['path']}")

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

    # 用已绑定的项目路径去重（不是名字，同名不同路径的项目都应该被绑定）
    bound_paths = set()
    bound_names = {}  # name → count，用于检测同名项目
    for tid, path in projects.all_bindings().items():
        bound_paths.add(path)
        name = Path(path).name
        bound_names[name] = bound_names.get(name, 0) + 1

    log.info(f"[sync] 发现 {len(unbound)} 个未绑定项目，已绑定路径数: {len(bound_paths)}")
    for p in unbound:
        if p["path"] in bound_paths:
            log.info(f"[sync] 跳过 {p['name']}（路径已绑定）")
            continue
        # 同名项目用父目录区分话题名
        topic_name = p["name"]
        name_count = bound_names.get(topic_name, 0)
        # 检查未绑定列表中是否还有同名项目
        same_name_unbound = [x for x in unbound if x["name"] == topic_name and x["path"] != p["path"]]
        if name_count > 0 or same_name_unbound:
            parent = Path(p["path"]).parent.name
            topic_name = f"{p['name']} ({parent})"
        try:
            topic = await bot.create_forum_topic(chat_id=chat_id, name=topic_name)
            projects.bind(topic.message_thread_id, p["path"])
            state.set_cwd(topic.message_thread_id, p["path"])
            # 发送欢迎消息 + 历史会话列表
            await _send_topic_welcome(bot, chat_id, topic.message_thread_id, topic_name, p["path"])
            bound_names[p["name"]] = name_count + 1
            bound_paths.add(p["path"])
            log.info(f"[sync] {topic_name} → topic {topic.message_thread_id}")
            # 间隔避免 flood control
            await asyncio.sleep(2)
        except RetryAfter as e:
            log.warning(f"[sync] {topic_name} 限频，等待 {e.retry_after}s")
            await asyncio.sleep(e.retry_after + 1)
            # 重试一次
            try:
                topic = await bot.create_forum_topic(chat_id=chat_id, name=topic_name)
                projects.bind(topic.message_thread_id, p["path"])
                state.set_cwd(topic.message_thread_id, p["path"])
                await _send_topic_welcome(bot, chat_id, topic.message_thread_id, topic_name, p["path"])
                bound_names[p["name"]] = name_count + 1
                bound_paths.add(p["path"])
                log.info(f"[sync] {p['name']} → topic {topic.message_thread_id} (重试成功)")
            except Exception as e2:
                log.warning(f"[sync] {p['name']} 重试也失败: {e2}")
        except Exception as e:
            log.warning(f"[sync] {p['name']} 创建话题失败: {e}")


def create_app() -> Application:
    # 网络健壮化：
    # 1. 显式 timeout 让单次请求不会无限挂
    # 2. 禁用 httpx keepalive 池 → 每次请求新建 TCP，避免"死连接被无限复用"导致 getUpdates 假死
    # getUpdates 是 long-poll，用独立 client（跟普通 sendMessage 隔离，互不踩连接池）
    import httpx
    from telegram.request import HTTPXRequest

    _no_keepalive = {"limits": httpx.Limits(max_keepalive_connections=0, max_connections=10)}

    updates_req = HTTPXRequest(
        read_timeout=60, connect_timeout=15, pool_timeout=10,
        httpx_kwargs=_no_keepalive,
    )
    default_req = HTTPXRequest(
        read_timeout=30, connect_timeout=15, pool_timeout=10,
        httpx_kwargs=_no_keepalive,
    )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .request(default_req)
        .get_updates_request(updates_req)
        .build()
    )

    async def post_init(application):
        await application.bot.set_my_commands([
            BotCommand("projects", "项目列表"),
            BotCommand("sessions", "历史会话"),
            BotCommand("status", "活跃会话"),
            BotCommand("trust", "项目信任开关"),
            BotCommand("whoami", "你的 ID"),
        ])
        # 如果已有 CHAT_ID，启动时同步 + 定时同步
        if config.CHAT_ID:
            await sync_all_projects(application.bot, config.CHAT_ID)
            # sync 完成后再 recover，确保新绑定的项目也能恢复终端会话
            from src.api_server import _recover_active_sessions
            await _recover_active_sessions()

        # 每 5 分钟扫描新项目 + recover
        async def periodic_sync():
            while True:
                await asyncio.sleep(300)
                if config.CHAT_ID:
                    await sync_all_projects(application.bot, config.CHAT_ID)
                    from src.api_server import _recover_active_sessions
                    await _recover_active_sessions()

        asyncio.create_task(periodic_sync())
    app.post_init = post_init

    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("sessions", cmd_sessions))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("trust", cmd_trust))
    app.add_handler(CallbackQueryHandler(handle_callback))
    # 文本消息
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & (filters.ChatType.SUPERGROUP | filters.ChatType.PRIVATE),
            handle_message,
        )
    )
    # 文件附件（图片、文档等）
    app.add_handler(
        MessageHandler(
            (filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO
             | filters.VOICE | filters.Sticker.ALL)
            & (filters.ChatType.SUPERGROUP | filters.ChatType.PRIVATE),
            handle_message,
        )
    )

    return app
