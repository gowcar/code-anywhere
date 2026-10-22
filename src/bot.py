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
    TypeHandler,
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
from src import backends

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


# ============ /model ============

async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/model 查看 · /model list · /model [claude|codex] [模型] [effort] [权限]（顺序不限，未写的不变）"""
    if not auth_check(update.effective_user.id):
        return
    cwd = _update_cwd(update)
    if not cwd:
        await update.message.reply_text("请在已绑定的项目话题内使用 /model")
        return
    from src import model_prefs, permissions
    args = context.args or []
    topic_id = update.message.message_thread_id or update.message.chat_id
    if args and args[0].lower() in {"list", "ls", "列表"}:
        catalog = await asyncio.to_thread(model_prefs.codex_models)
        await update.message.reply_text(model_prefs.format_list(catalog), parse_mode="HTML")
        return
    if not args:
        await update.message.reply_text(_model_status(cwd), parse_mode="HTML")
        return
    catalog = await asyncio.to_thread(model_prefs.codex_models)
    async with _project_locks.setdefault(cwd, asyncio.Lock()):
        current = backends.get_backend(cwd)
        try:
            req = model_prefs.parse(args, current, catalog)
            target = req.engine or current
            if target == "codex" and req.effort and not req.model:
                # 没指定模型时，按项目将使用的模型校验 effort
                slug = model_prefs.get(cwd, "codex").get("model") or _running_model(cwd, "codex")
                efforts = next((m["efforts"] for m in catalog if m["slug"] == slug), None)
                if efforts and req.effort not in efforts:
                    raise ValueError(f"{slug} 不支持 effort「{req.effort}」，可选：{' / '.join(efforts)}")
        except ValueError as exc:
            await update.message.reply_text(f"⚠️ {exc}")
            return
        if target != current:
            old_pane = await _find_pane_for_project(cwd)
            if old_pane:
                await backends.command("tmux", "send-keys", "-t", old_pane, "Escape")
            backends.set_backend(cwd, target)
            from src.api_server import retire_project_watchers
            retire_project_watchers(cwd, target)
            stop_typing(topic_id)
        changed = any((req.model, req.effort, req.permission))
        if target == "codex":
            model_prefs.update(cwd, "codex", model=req.model, effort=req.effort)
            if req.permission:
                permissions.set_mode(cwd, req.permission)
        else:
            model_prefs.update(cwd, "claude", model=req.model, effort=req.effort, permission=req.permission)
        note = ""
        pane = await _find_pane_for_project(cwd)
        if changed and pane:
            await update.message.reply_text("正在按新设置重启终端，当前回合会中断，随后接回原会话…")
            try:
                from src import model_switch
                note = await model_switch.restart(cwd, topic_id)
            except Exception as exc:
                log.exception("[model] restart failed project=%s", cwd)
                note = f"⚠️ 设置已保存，但重启未完成：{exc}"
        elif pane:
            from src.api_server import register_pane_watcher
            await register_pane_watcher(pane, cwd)
        else:
            note = "下一条消息启动终端时生效。"
    await update.message.reply_text(_model_status(cwd) + (f"\n\n{note}" if note else ""), parse_mode="HTML")


def _running_model(cwd: str, engine: str) -> str:
    from src.api_server import terminal_sessions
    return next((v.get("model") or "" for v in terminal_sessions.values()
                 if v.get("cwd") == cwd and v.get("backend", "claude") == engine), "")


def _model_status(cwd: str) -> str:
    from src import model_prefs, permissions
    engine = backends.get_backend(cwd)
    prefs = model_prefs.get(cwd, engine)
    if engine == "codex":
        model = prefs.get("model") or _running_model(cwd, "codex") or os.getenv("CODEX_MODEL") or "Codex 配置默认"
        perm = permissions.LABELS[permissions.get_mode(cwd)]
    else:
        model = next((n for _, mid, n in model_prefs.CLAUDE_MODELS if mid == prefs.get("model")), None) \
            or prefs.get("model") or _running_model(cwd, "claude") or "Claude 配置默认"
        perm_key = prefs.get("permission") or backends._claude_settings_permission(cwd) or "default"
        perm = model_prefs.CLAUDE_PERMISSIONS[perm_key]
    return (f"<b>{html_lib.escape(Path(cwd).name)}</b>\n"
            f"引擎：{'Codex' if engine == 'codex' else 'Claude'}\n"
            f"模型：{html_lib.escape(model)}\n"
            f"effort：{prefs.get('effort') or '默认'}\n"
            f"权限：{perm}\n\n"
            "切换示例：<code>/model claude sonnet high</code>　<code>/model gpt-6-sol xhigh full</code>\n"
            "全部选项：<code>/model list</code>")


WORKSPACE_ROOT = Path(os.getenv("WORKSPACE_ROOT", str(Path.home() / "workspace")))


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/new <名字> [claude|codex]：建目录 + 建话题 + 启动 CLI，全程不用登录服务器。"""
    if not auth_check(update.effective_user.id):
        return
    import re
    args = context.args or []
    engine = {"gpt": "codex"}.get(args[1].lower(), args[1].lower()) if len(args) > 1 else "claude"
    if not args or len(args) > 2 or engine not in {"claude", "codex"} \
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args[0]):
        await update.message.reply_text(
            f"用法：/new <名字> [claude|codex]\n名字只能用字母、数字、. _ -，目录建在 {WORKSPACE_ROOT}/ 下。")
        return
    chat_id = update.effective_chat.id
    path = (WORKSPACE_ROOT / args[0]).resolve()
    def link(tid):  # 群话题可给 t.me/c 链接；私聊话题没有公开链接，直接报名字
        return f"https://t.me/c/{str(chat_id).removeprefix('-100')}/{tid}" if chat_id < 0 else f"「{args[0]}」话题"
    if existing := projects.get_topic(str(path)):
        await update.message.reply_text(f"项目已存在：{link(existing)}")
        return
    created = not path.exists()
    path.mkdir(parents=True, exist_ok=True)
    topic = await context.bot.create_forum_topic(chat_id=chat_id, name=args[0])
    tid = topic.message_thread_id
    projects.bind(tid, str(path))
    from src import liveness
    liveness.remember_topic_name(tid, args[0])
    state.set_cwd(tid, str(path))
    backends.set_backend(str(path), engine)
    log.info(f"[new] {path} → topic {tid} engine={engine} created={created}")
    await context.bot.send_message(
        chat_id=chat_id, message_thread_id=tid,
        text=f"✅ 新项目 {args[0]}\n目录：{path}{'（新建）' if created else '（已有目录）'}\n"
             f"引擎：{'Codex' if engine == 'codex' else 'Claude'}，正在启动…\n\n"
             "直接在这里说话开始。/model 切换模型、effort 和权限。")
    if chat_id > 0:  # 私聊 Thread：输入框下方的快捷键盘
        try:
            await ensure_quick_keyboard(context.bot, chat_id, tid)
        except Exception as exc:
            log.warning("[keyboard] 发送快捷键盘失败 thread=%s: %s", tid, exc)
    async with _project_locks.setdefault(str(path), asyncio.Lock()):
        pane = await _ensure_pane_for_project(str(path), chat_id, tid)
    await context.bot.send_message(chat_id=chat_id, message_thread_id=tid,
                                   text="🟢 终端已就绪" if pane else "⚠️ 终端启动失败，发第一条消息时会再试一次")
    if update.message.message_thread_id != tid:
        await update.message.reply_text(f"已创建：{link(tid)}")


async def _send_screen(message, cwd: str):
    """把项目终端画成图片发回（保留排版和颜色，手机上可放大看）；失败退回文本。"""
    pane = await _find_pane_for_project(cwd)
    if not pane:
        await message.reply_text("终端已经关闭")
        return
    try:
        from src import term_image
        ansi = await backends.command("tmux", "capture-pane", "-e", "-p", "-t", pane)
        png = await asyncio.to_thread(term_image.render, ansi)
        await message.reply_photo(photo=png, caption=f"{Path(cwd).name} 终端（{datetime.now():%H:%M:%S}）")
    except Exception as exc:
        log.warning("[liveness] 终端截图失败，改发文本: %s", exc)
        from src import liveness
        await message.reply_text(liveness.screen_excerpt(await _capture_pane(pane)), parse_mode="HTML")


async def cmd_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/screen：看当前项目终端的截图。"""
    if not auth_check(update.effective_user.id):
        return
    cwd = _update_cwd(update)
    if not cwd:
        await update.message.reply_text("请在项目 Thread 里使用 /screen")
        return
    await _send_screen(update.message, cwd)


async def _on_stopped_generation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """用户点了草稿气泡上的原生“停止”按钮（Bot API 10.3 stopped_message_generation）→ 等同 /abort。"""
    payload = (update.api_kwargs or {}).get("stopped_message_generation")
    if not payload:
        return
    from src import liveness
    chat_id = (payload.get("chat") or {}).get("id") or config.CHAT_ID
    topic_id = liveness.topic_for_stopped_draft(payload)
    cwd = projects.get_path(topic_id) if topic_id else None
    log.info("[abort] 原生停止 topic=%s cwd=%s", topic_id, cwd)
    if not cwd:
        return
    text = await _abort_project(context.bot, cwd, int(chat_id), topic_id)
    await context.bot.send_message(chat_id=chat_id, message_thread_id=topic_id, text=text)


async def cmd_abort(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/abort：中断当前回合，丢弃已发出但还没被处理的消息（CLI 排队的 + 桥接还没投递的相册）。"""
    if not auth_check(update.effective_user.id):
        return
    cwd = _update_cwd(update)
    if not cwd:
        await update.message.reply_text("请在已绑定的项目话题内使用 /abort")
        return
    message = update.message
    text = await _abort_project(context.bot, cwd, message.chat_id, message.message_thread_id)
    await message.reply_text(text, parse_mode=None)


async def _abort_project(bot, cwd: str, chat_id: int, thread_id: int | None) -> str:
    topic_id = thread_id or chat_id
    albums = [k for k in _media_groups if k[0] == chat_id and k[1] == thread_id]
    for key in albums:
        _media_groups.pop(key, None)
    from src import abort, liveness
    async with _project_locks.setdefault(cwd, asyncio.Lock()):
        backend = backends.get_backend(cwd)
        pane = await _find_pane_for_project(cwd)
        running, dropped = (await abort.abort(pane, backend)) if pane else (False, "")
    stop_typing(topic_id)
    if (card := liveness.note_abort(topic_id)) and running:
        try:
            await bot.edit_message_text(chat_id=chat_id, message_id=card, text="⏹ 已中断")
        except Exception:
            pass
    log.info("[abort] %s pane=%s running=%s dropped=%s albums=%s", Path(cwd).name, pane, running, bool(dropped), len(albums))
    lines = ["⏹ 已中断当前回合" if running else "当前没有正在运行的回合"]
    if dropped:
        shown = dropped if len(dropped) <= 800 else dropped[:800] + "…"
        lines.append(f"已丢弃排队中的消息（需要的话可重发）：\n{shown}")
    if albums:
        lines.append(f"已丢弃 {len(albums)} 组还在接收中的相册")
    if not running and not dropped and not albums:
        lines.append("没有需要丢弃的排队消息")
    return "\n".join(lines)


async def cmd_permissions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update.effective_user.id):
        return
    from src import permissions
    cwd = _update_cwd(update)
    if not cwd:
        await update.message.reply_text("请在已绑定的项目话题内使用 /permissions")
        return
    if backends.get_backend(cwd) != "codex":
        await update.message.reply_text("请先 /model codex，或直接 /model codex full。")
        return
    args = context.args or []
    mode = args[0].lower() if args else None
    if len(args) > 1 or (mode and mode not in permissions.MODES):
        await update.message.reply_text("用法：/permissions full、/permissions workspace 或 /permissions read-only")
        return
    async with _project_locks.setdefault(cwd, asyncio.Lock()):
        if mode:
            await update.message.reply_text("正在切换权限，当前回合会中断，随后恢复原会话。")
            try:
                await permissions.apply_mode(cwd, mode, update.message.chat_id,
                                             update.message.message_thread_id or update.message.chat_id)
            except Exception as exc:
                log.exception("[permissions] switch failed project=%s", cwd)
                await update.message.reply_text(f"⚠️ 权限切换未完成：{exc}")
                return
        from src.api_server import terminal_sessions
        current = next((v for v in terminal_sessions.values()
                        if v.get("cwd") == cwd and v.get("backend") == "codex"), {})
        effective = mode or current.get("permission_mode")
        text = permissions.LABELS.get(effective, "未观测")
        await update.message.reply_text(
            f"{Path(cwd).name} · Codex\n当前权限：{text}\n"
            f"启动配置：{permissions.LABELS[permissions.get_mode(cwd)]}\n"
            "切换：/permissions full、/permissions workspace、/permissions read-only\n"
            "Full 可访问本机文件与网络；切换后可直接继续。")


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

    if backends.get_backend(project_path) == "codex":
        await update.message.reply_text("当前是 Codex 会话，历史由本机 codex resume 管理。用 /model claude 可返回 Claude。")
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

async def cmd_issues(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """按 agent:* 状态汇总 GitLab issue（直接调 API，不经过模型），每条带操作按钮。"""
    if not auth_check(update.effective_user.id):
        return
    text, markup = await _issues_summary()
    await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=markup)


async def _issues_summary():
    """第一层：精简列表（每个 issue 一个按钮）。"""
    from src import issues
    try:
        found = await asyncio.to_thread(issues.fetch_open_issues)
        return issues.overview(found)
    except FileNotFoundError:
        return "还没有配置 GitLab 令牌（~/.config/gitlab-token），运行 ./setup --only gitlab", None
    except Exception as exc:
        log.warning("[issues] 查询失败: %s", exc)
        return f"⚠️ 查询 GitLab 失败：{html_lib.escape(str(exc))}", None


# “不通过”时请用户回复原因：提示消息 id → (project_id, iid)
_issue_reason_prompts: dict[int, tuple[int, int]] = {}


async def _issues_callback(query, context, data: str):
    """列表 ↔ 详情两层；操作按钮只作用于当前打开的那一条。"""
    from telegram import ForceReply
    from src import issues
    _, action, project_id, iid = data.split(":")
    project_id, iid = int(project_id), int(iid)
    show = None  # 操作后要展示的详情 (project_id, iid)；None 表示回列表
    if action in ("list", "refresh"):
        await query.answer()
    elif action == "open":
        await query.answer()
        show = (project_id, iid)
    elif action == "drop":  # 放弃：在当前这一条上二次确认，防手滑
        issue = await asyncio.to_thread(issues.get_issue, project_id, iid)
        await query.answer()
        await query.edit_message_reply_markup(issues.confirm_keyboard(project_id, iid, issues.short_ref(issue)))
        return
    else:
        issue = await asyncio.to_thread(issues.get_issue, project_id, iid)
        ref = issues.short_ref(issue)
        try:
            if action == "pass":
                await asyncio.to_thread(issues.verify_pass, project_id, iid)
                await query.answer(f"{ref} 已验证通过并关闭")
                show = "next"  # 连续验证：跳到下一个待验证
            elif action == "fail":
                await asyncio.to_thread(issues.verify_fail, project_id, iid)
                await query.answer(f"{ref} 已退回待处理")
                prompt = await query.message.reply_text(
                    f"↩️ {ref} 已退回待处理。直接回复这条消息说明哪里不对，我会写进 issue 并通知负责的会话；不说明也可以。",
                    reply_markup=ForceReply(selective=True))
                _issue_reason_prompts[prompt.message_id] = (project_id, iid)
            elif action == "dropok":
                was_doing = "agent:doing" in issue.get("labels", [])
                await asyncio.to_thread(issues.abandon, project_id, iid)
                await query.answer(f"{ref} 已放弃并关闭")
                if was_doing:
                    result = await issues.notify_owner(
                        issue, f"GitLab issue {issue['web_url']} 用户已决定放弃处理并关闭，请停止这项工作，不需要再提交改动。")
                    await query.message.reply_text(f"🗑 {ref} 已放弃并关闭；{result}")
        except Exception as exc:
            log.warning("[issues] 操作失败 %s %s: %s", action, ref, exc)
            await query.answer(f"操作失败：{exc}"[:190], show_alert=True)
            show = (project_id, iid)
    try:
        found = await asyncio.to_thread(issues.fetch_open_issues)
        if show == "next":
            nxt = issues.next_to_verify(found)
            show = (nxt["project_id"], nxt["iid"]) if nxt else None
        view = issues.detail(found, *show) if show else None
        text, markup = view or issues.overview(found)
        await query.edit_message_text(text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=markup)
    except Exception as exc:
        if "not modified" not in str(exc):
            log.warning("[issues] 刷新视图失败: %s", exc)


async def _handle_issue_reason(message) -> bool:
    """用户回复了“说明哪里不对”的提示：写进 issue 并通知负责会话。返回 True 表示已处理，不再转给项目会话。"""
    reply_to = message.reply_to_message
    target = _issue_reason_prompts.pop(reply_to.message_id, None) if reply_to else None
    if not target:
        return False
    from src import issues
    project_id, iid = target
    reason = (message.text or message.caption or "").strip()
    try:
        issue = await asyncio.to_thread(issues.get_issue, project_id, iid)
        await asyncio.to_thread(issues.note, project_id, iid, f"↩️ 验证未通过的原因（用户在 TG 补充）：\n\n{reason}")
        result = await issues.notify_owner(
            issue, f"GitLab issue 验证未通过：{issue['web_url']}，{reason[:120]}；请按全局规则「GitLab issue 处理」重新接手。")
        await message.reply_text(f"已写进 {issues.short_ref(issue)}；{result}")
    except Exception as exc:
        log.warning("[issues] 记录原因失败: %s", exc)
        await message.reply_text(f"⚠️ 记录原因失败：{exc}")
    return True


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not auth_check(update.effective_user.id):
        return
    from src.agent_sessions import discover
    labels = {'running': ('🟢', '进行中'), 'retrying': ('🟡', '重试中'),
              'waiting_input': ('⚪', '空闲'), 'failed': ('🔴', '上一轮失败'),
              'waiting_confirmation': ('🟡', '等待确认'), 'unknown': ('⚪', '状态不明')}
    sessions = await discover()
    lines = ["<b>会话状态</b>\n"]
    for session in sessions:
        activity = session.get('activity', session['status'])
        icon, label = labels.get(activity, ('⚪', activity))
        name = html_lib.escape(session['project'])
        model = html_lib.escape(session.get('model') or session['backend'])
        lines.append(f"{icon} {name} · {label} · {model}")
        last = session.get('last_turn', {})
        if last.get('timestamp'):
            lines.append('  最后状态事件：' + html_lib.escape(last['timestamp']))
        if activity in {'failed', 'retrying'} and last.get('error'):
            lines.append('  ' + html_lib.escape(last['error']['reason']))
    if not sessions:
        lines.append("（暂无活跃会话）")
    from src.renderer import split_message
    for chunk, _raw in split_message("\n".join(lines), render=False):
        await update.message.reply_text(chunk, parse_mode='HTML')


# ============ Callback ============

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not auth_check(query.from_user.id):
        return
    data = query.data
    chat_id = query.message.chat_id

    if data.startswith("is:"):  # /issues 按钮：验证通过 / 不通过 / 放弃 / 刷新
        await _issues_callback(query, context, data)
        return

    if data.startswith("lv:"):  # 状态告警按钮：看终端 / 中断
        _, action, topic = data.split(":", 2)
        cwd = projects.get_path(int(topic))
        await query.answer()
        if not cwd:
            await query.message.reply_text("这个话题已经没有绑定项目了")
            return
        if action == "abort":
            text = await _abort_project(context.bot, cwd, chat_id, int(topic))
            await query.message.reply_text(text, parse_mode=None)
            return
        await _send_screen(query.message, cwd)
        return

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
            note = "；Codex 权限在重启其终端后生效" if backends.get_backend(path) == "codex" else ""
            await query.answer(f"{'已信任' if new_val else '已取消信任'}: {Path(path).name}{note}")
        except Exception:
            pass
        try:
            await query.edit_message_reply_markup(reply_markup=_trust_keyboard())
        except Exception as e:
            log.warning(f"[trust] edit markup 失败（忽略）: {e}")
        return

    if data == "new" or data.startswith("r:"):
        path = projects.get_path(query.message.message_thread_id)
        if path and backends.get_backend(path) == "codex":
            await query.answer("当前为 Codex，请先 /model claude", show_alert=True)
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

    # AskUserQuestion 菜单：多页表单每问的标题占位行，点了忽略
    if data == "mnop":
        return

    # AskUserQuestion 菜单：选项点击（单选即提交 / 多选、多页累积后刷新键盘）
    if data.startswith("ms:"):
        try:
            _, token, qi, oi = data.split(":", 3)
            qi, oi = int(qi), int(oi)
        except (ValueError, IndexError):
            return
        from src.api_server import handle_menu_select, menu_keyboard_for
        r = await handle_menu_select(token, qi, oi)
        try:
            if r == "submitted":
                await query.edit_message_text("✅ 已提交")
            elif r == "updated":
                kb = menu_keyboard_for(token)
                if kb:
                    await query.edit_message_reply_markup(reply_markup=kb)
            else:
                await query.edit_message_text("⚠️ 此菜单已失效（可能已在其它端选过）")
        except Exception:
            pass
        return

    # AskUserQuestion 菜单：提交累积的选择
    if data.startswith("mok:"):
        token = data[4:]
        from src.api_server import handle_menu_submit
        r = await handle_menu_submit(token)
        try:
            await query.edit_message_text("✅ 已提交" if r else "⚠️ 此菜单已失效")
        except Exception:
            pass
        return


# ============ 消息处理 ============

async def _inject_tmux(pane: str, text: str):
    """Paste one whole message, checking every tmux operation before Enter."""
    if "\n" in text:
        buf = f"ca_inject_{pane.lstrip('%')}"
        await backends.command("tmux", "set-buffer", "-b", buf, "--", text)
        await backends.command("tmux", "paste-buffer", "-p", "-d", "-b", buf, "-t", pane)
        await asyncio.sleep(0.15)
    else:
        await backends.command("tmux", "send-keys", "-t", pane, "-l", "--", text)
        await asyncio.sleep(0.15)
    await backends.command("tmux", "send-keys", "-t", pane, "Enter")
    log.info("[tmux] injected pane=%s chars=%s", pane, len(text))


_SHELL_CMDS = {"fish", "bash", "zsh", "sh", "tcsh", "csh", "ksh", "dash"}


async def _pane_has_claude(pane_id: str) -> bool:
    return bool(await backends.pane_processes(pane_id, "claude"))


async def _pane_has_backend(pane_id: str, cwd: str) -> bool:
    return bool(await backends.pane_processes(pane_id, backends.get_backend(cwd)))


# 新目录首启的信任框：Claude 默认选中 "No, exit"，Codex 默认 "Trust and continue"。
# 注入的首条消息 + Enter 会被当成选择 → 消息丢失甚至 CLI 退出，所以先显式选“信任”。
_TRUST_CHOICES = ("Yes, I trust this folder", "Trust and continue")


def _trust_prompt_state(screen: str) -> tuple[bool, bool]:
    """(信任框是否在屏, 当前光标是否已在“信任”选项上)"""
    lines = screen.splitlines()
    if not any(choice in line for line in lines for choice in _TRUST_CHOICES):
        return False, False
    selected = [line for line in lines if line.lstrip().startswith(("❯", "›"))]
    return True, any(choice in line for line in selected for choice in _TRUST_CHOICES)


async def _capture_pane(pane_id: str) -> str:
    try:
        return await backends.command("tmux", "capture-pane", "-p", "-t", pane_id)
    except Exception:
        return ""


async def _accept_trust_prompt(pane_id: str, wait: float = 4.0) -> bool:
    """Bridge-launched projects are bound topics the user already chose; accept folder trust."""
    deadline = time.time() + wait
    while time.time() < deadline:
        shown, on_trust = _trust_prompt_state(await _capture_pane(pane_id))
        if shown:
            for _ in range(3):
                if on_trust:
                    break
                await backends.command("tmux", "send-keys", "-t", pane_id, "Down")
                await asyncio.sleep(0.3)
                shown, on_trust = _trust_prompt_state(await _capture_pane(pane_id))
            if not on_trust:
                log.warning(f"[ensure_pane] pane={pane_id} 信任框未能选中“信任”，放弃")
                return False
            await backends.command("tmux", "send-keys", "-t", pane_id, "Enter")
            log.info(f"[ensure_pane] pane={pane_id} 已接受目录信任")
            await asyncio.sleep(2.0)
            return True
        await asyncio.sleep(0.5)
    return True


async def _hook_session_for_pane(pane_id: str, wait: float = 8.0) -> str | None:
    from src.api_server import terminal_sessions
    deadline = time.time() + wait
    while time.time() < deadline:
        for sid, info in list(terminal_sessions.items()):
            if info.get("tmux_pane") == pane_id and info.get("backend", "claude") == "claude":
                return sid
        await asyncio.sleep(0.5)
    return None


async def _ensure_pane_for_project(cwd: str, chat_id: int, topic_id: int | None) -> str | None:
    """Start the selected native CLI in tmux, then attach its transcript watcher."""
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
    backend = backends.get_backend(cwd)
    proj_name = Path(norm_cwd).name + ("-codex" if backend == "codex" else "")
    tmux_env_args = backends.launch_env_args(backend)
    try:
        proc = await asyncio.create_subprocess_exec(
            "tmux", "new-window", "-t", tmux_session, "-c", norm_cwd, "-n", proj_name,
            *tmux_env_args,
            "-P", "-F", "#{pane_id}",
            *(backends.codex_launch_args(cwd) if backend == "codex" else backends.claude_launch_args(cwd)),
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
    await backends.command("tmux", "set-option", "-p", "-t", pane_id, "@ca_project", norm_cwd)
    await backends.command("tmux", "set-option", "-p", "-t", pane_id, "@ca_backend", backend)

    # 等 claude 起来（最多 15s），且要求持续存在 ≥ 2s（防秒退 fallback 到 fish 的假 ready）
    stable_since: float | None = None
    ready = False
    for _ in range(30):
        await asyncio.sleep(0.5)
        alive = await _pane_has_backend(pane_id, cwd)
        if alive:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= 2.0:
                log.info(f"[ensure_pane] pane={pane_id} {backend} ready & stable")
                ready = True
                break
        else:
            if stable_since is not None:
                log.warning(f"[ensure_pane] pane={pane_id} {backend} 秒退（可能 auth 失败），放弃")
                return None
            stable_since = None

    if not ready:
        log.warning(f"[ensure_pane] pane={pane_id} {backend} 未在 15s 内就绪")
        return None
    if not await _accept_trust_prompt(pane_id):
        return None

    # 主动给这个 pane 注册 watcher — 不依赖 SessionStart hook 自动触发
    # （新 pane 起 claude 时 hook 不总是能触发，具体原因未知；直接注册最可靠）
    try:
        from src.api_server import register_pane_watcher
        # Claude's SessionStart hook reports the NEW session (its jsonl may not exist until the
        # first prompt). Globbing the newest jsonl would pick an old session or nothing at all.
        if backend == "claude" and await _hook_session_for_pane(pane_id):
            return pane_id
        # Fresh Codex writes its rollout only after the first user prompt.
        if backend == "claude" or await backends.codex_transcript(pane_id, norm_cwd):
            if not await register_pane_watcher(pane_id, norm_cwd):
                return None
    except Exception as e:
        log.warning(f"[ensure_pane] register_pane_watcher failed: {e}")
        return None

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
        await backends.infer_live_backend(cwd)
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
            if pane and await _pane_has_backend(pane, cwd):
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
        if await _pane_has_backend(pid, cwd):
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
    """为终端注入路径启动 typing。若已有任务，先取消再启新的（重置计时）。
    私聊 Thread 模式不发：客户端会把“正在输入”显示成列表预览里闪动的蓝色长条，且列表已有状态灯。"""
    if _private_thread_mode():
        return
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
MAX_VIDEO_SIZE = 20 * 1024 * 1024  # Bot API 下载上限
_VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mpeg", ".mpg", ".3gp"}


def _is_video_attachment(message) -> bool:
    """视频只落盘不交给模型（用户要求）：TG 视频、圆形视频、以及视频类文件。"""
    doc = message.document
    return bool(message.video or getattr(message, "video_note", None)
                or (doc and ((doc.mime_type or "").startswith("video/")
                             or Path(doc.file_name or "").suffix.lower() in _VIDEO_EXT)))

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
    is_video = _is_video_attachment(message)
    max_size = MAX_VIDEO_SIZE if is_video else MAX_FILE_SIZE

    if message.photo:
        # TG 图片：取最大尺寸
        file_obj = message.photo[-1]
        file_name = f"photo_{file_obj.file_unique_id}.jpg"
    elif message.document:
        doc = message.document
        file_obj = doc
        file_name = doc.file_name or f"file_{doc.file_unique_id}"
    elif message.video or getattr(message, "video_note", None):
        file_obj = message.video or message.video_note
        file_name = getattr(file_obj, "file_name", None) or f"video_{file_obj.file_unique_id}.mp4"
    elif message.audio or message.voice:
        return None, None, "暂不支持音频文件"
    elif message.sticker:
        return None, None, ""  # 贴纸静默忽略

    if not file_obj:
        return None, None, ""
    if file_obj.file_size and file_obj.file_size > max_size:
        return None, None, f"文件太大（{file_obj.file_size // 1024 // 1024}MB），下载上限 {max_size // 1024 // 1024}MB，未保存。"

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

    if tg_file.file_size and tg_file.file_size > max_size:
        return None, None, f"文件太大（{tg_file.file_size // 1024 // 1024}MB），下载上限 {max_size // 1024 // 1024}MB，未保存。"

    # 构建本地路径（视频放子目录：不参与图片的滚动清理）
    attachments_dir = Path(cwd) / ".attachments"
    if is_video:
        attachments_dir /= "videos"
    attachments_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    safe_name = file_name.replace("/", "_").replace(" ", "_")
    local_name = f"{ts}_{safe_name}"
    local_path = attachments_dir / local_name

    try:
        await tg_file.download_to_drive(str(local_path), read_timeout=30, connect_timeout=15)
    except Exception as e:
        local_path.unlink(missing_ok=True)  # 不留半截文件
        return None, None, f"下载文件失败: {e}"

    ext = Path(file_name).suffix.lower()
    rel_path = local_path.relative_to(Path(cwd)).as_posix()
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


_project_locks: dict[str, asyncio.Lock] = {}
_media_groups: dict[tuple, dict] = {}
MEDIA_GROUP_QUIET_SECONDS = 1.2


async def _flush_media_group(key):
    """TG sends album items separately, without an end marker; wait for quiet."""
    batch = _media_groups.get(key)
    if batch is None:
        return
    while True:
        remaining = MEDIA_GROUP_QUIET_SECONDS - (time.monotonic() - batch["updated"])
        if remaining <= 0:
            break
        await asyncio.sleep(remaining)
    if _media_groups.get(key) is not batch:  # 等待期间被 /abort 丢弃
        return
    _media_groups.pop(key, None)
    updates = sorted(batch["updates"].values(), key=lambda u: u.message.message_id)
    update = updates[0]
    if _update_cwd(update) != batch["cwd"]:
        await update.message.reply_text("⚠️ 相册接收期间项目绑定改变，请重新发送相册。")
        return
    await _deliver_message(update, batch["context"], [u.message for u in updates])


def _private_thread_mode() -> bool:
    """入口是 bot 私聊（CHAT_ID 为用户 ID，正数）而不是群组（负数）。"""
    return bool(config.CHAT_ID) and int(config.CHAT_ID) > 0


def _update_cwd(update: Update) -> str | None:
    msg = update.effective_message
    if not msg:
        return None
    # 私聊 Thread 模式（Bot API 9.3+）与群组话题一样按 Thread 路由；不在 Thread 里（All Messages）
    # 的消息不对应任何项目，绝不能落到“上一次用过的目录”上
    if msg.chat.type == "private" and not msg.message_thread_id:
        return None if _private_thread_mode() else state.get_cwd(msg.chat_id)
    return projects.get_path(msg.message_thread_id)


# 私聊 Thread 模式：输入框下方常驻的快捷按钮（回复键盘）。点了由桥接处理，不转给会话
KEY_SCREEN, KEY_ABORT = "📸 看终端", "⏹ 中断"
KEYBOARD_FILE = config.PROJECT_ROOT / ".reply-keyboard.json"


def quick_keyboard():
    from telegram import ReplyKeyboardMarkup, KeyboardButton
    return ReplyKeyboardMarkup([[KeyboardButton(KEY_SCREEN), KeyboardButton(KEY_ABORT)]], resize_keyboard=True,
                               is_persistent=True, input_field_placeholder="在这个 Thread 里继续对话")


async def ensure_quick_keyboard(bot, chat_id: int, thread_id: int) -> None:
    """每个私聊 Thread 只发一次带快捷键盘的说明消息（静默）。"""
    import json
    try:
        done = set(json.loads(KEYBOARD_FILE.read_text()))
    except (OSError, ValueError):
        done = set()
    if thread_id in done:
        return
    await bot.send_message(chat_id=chat_id, message_thread_id=thread_id, disable_notification=True,
                           text="常用操作已固定在输入框下方：看终端、中断（只作用于当前 Thread）",
                           reply_markup=quick_keyboard())
    done.add(thread_id)
    KEYBOARD_FILE.write_text(json.dumps(sorted(done)))


async def _handle_quick_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message = update.message
    if not message or message.text not in (KEY_SCREEN, KEY_ABORT):
        return False
    if not auth_check(update.effective_user.id):
        return True
    cwd = _update_cwd(update)
    if not cwd:
        await message.reply_text("请在项目 Thread 里使用")
    elif message.text == KEY_SCREEN:
        await _send_screen(message, cwd)
    else:
        await message.reply_text(await _abort_project(context.bot, cwd, message.chat_id, message.message_thread_id),
                                 parse_mode=None)
    return True


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if await _handle_quick_key(update, context):
        return
    if message and message.reply_to_message and auth_check(update.effective_user.id) \
            and await _handle_issue_reason(message):
        return
    if message and message.message_thread_id:
        from src import liveness
        liveness.note_activity(message.message_thread_id)
    group = getattr(message, "media_group_id", None)
    cwd = _update_cwd(update)
    if group and cwd and auth_check(update.effective_user.id):
        key = (message.chat_id, message.message_thread_id, update.effective_user.id, group)
        batch = _media_groups.get(key)
        if batch is None:
            batch = {"cwd": cwd, "context": context, "updates": {}, "updated": time.monotonic()}
            _media_groups[key] = batch
            context.application.create_task(_flush_media_group(key), update=update)
        batch["updates"][message.message_id] = update
        batch["updated"] = time.monotonic()
        return
    await _deliver_message(update, context)


async def _deliver_message(update, context, album_messages=None):
    cwd = _update_cwd(update)
    if not cwd:
        return await _handle_message(update, context, album_messages)
    async with _project_locks.setdefault(cwd, asyncio.Lock()):
        try:
            await _handle_message(update, context, album_messages)
        except Exception:
            log.exception("[msg] delivery failed project=%s", cwd)
            stop_typing(update.message.message_thread_id or update.message.chat_id)
            await update.message.reply_text("⚠️ 消息投递失败，请查看 /model 和服务日志后重试。")


RECEIPT_IDLE_WAIT = 15      # 空闲会话：这么久还没在会话日志里看到用户原文，就提醒
RECEIPT_BUSY_WAIT = 180     # 忙碌会话：消息会先排队，等这一轮结束才写进日志


async def _transcript_position(pane: str, cwd: str) -> tuple[str, int] | None:
    """投递前记下当前会话日志的路径和大小，之后只看新增部分。"""
    try:
        if backends.get_backend(cwd) == "codex":
            found = await backends.codex_transcript(pane, cwd)
            path = found[1] if found else None
        else:
            from src.api_server import terminal_sessions
            path = next((v.get("transcript_path") for v in terminal_sessions.values()
                         if v.get("tmux_pane") == pane and v.get("transcript_path")), None)
        if not path:
            return None
        return path, (Path(path).stat().st_size if Path(path).exists() else 0)
    except Exception:
        return None


def receipt_snippet(text: str) -> str:
    """用来在会话日志里认出这条消息的片段：优先取第一行非附件文字，否则取附件文件名。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    words = [l for l in lines if not l.startswith("@") and not l.startswith("[引用消息]")]
    if words:
        return words[0][:24]
    return Path(lines[0].lstrip("@")).name[:40] if lines else ""


async def _confirm_delivery(message, pane: str, cwd: str, text: str, path: str, offset: int):
    """确认会话日志里出现了用户原文。Codex 开着 Goal 时曾把用户消息替换成 Goal 续跑指令（2026-10-22），
    这类“消息悄悄丢了”必须让用户知道。"""
    from src import liveness
    from src.agent_sessions import public_text, tail_events
    snippet = receipt_snippet(text)
    if not snippet:
        return
    topic = message.message_thread_id
    busy = (liveness._topics.get(topic) is not None and liveness._topics[topic].state != "idle")
    deadline = time.time() + (RECEIPT_BUSY_WAIT if busy else RECEIPT_IDLE_WAIT)
    while time.time() < deadline:
        await asyncio.sleep(1.0)
        try:
            if any(role == "user" and snippet in (body or "") for role, body in map(public_text, tail_events(path, offset, limit=4 << 20))):
                return
        except Exception:
            return
    screen = await _capture_pane(pane)
    if busy and snippet in screen:  # 还在队列里排着：没丢，只是这一轮还没做完
        return
    where = "停在终端输入框里没有发出去" if snippet in screen else "没有进入会话"
    log.warning("[receipt] 消息未被会话签收 pane=%s where=%s snippet=%s", pane, where, snippet)
    try:
        await message.reply_text(f"⚠️ 这条消息{where}，会话没收到。请重发；如果一直这样，可以 /screen 看看终端状态。\n\n原文：{text[:500]}",
                                 parse_mode=None)
    except Exception:
        pass


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, album_messages=None):
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
    # “私聊”指私聊里不在任何话题内的消息；私聊话题按话题路由，和群组话题一样
    is_private = message.chat.type == "private" and not topic_id
    has_file = bool(message.photo or message.document or message.video
                    or getattr(message, "video_note", None)
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
    if is_private and _private_thread_mode():  # All Messages 里直接对话：不转发给任何会话
        await message.reply_text("这里（All Messages）不会转给任何会话，请到对应项目的 Thread 里发消息。新项目可以用 /new 名字 创建。")
        return

    session_key = chat_id if is_private else topic_id

    # 查找工作目录
    cwd = projects.get_path(session_key) if not is_private else state.get_cwd(session_key)
    if not cwd:
        await message.reply_text("此话题未绑定项目。请用 /projects 添加。")
        return

    # 处理文件附件
    if has_file:
        if album_messages:
            refs, captions, videos = [], [], []
            for part in album_messages:
                rel_path, ext, err = await _download_attachment(part, context, cwd)
                if err or not rel_path:
                    await message.reply_text(f"⚠️ 相册未投递：{err or '附件无法读取'}，请整组重发。")
                    return
                if _is_video_attachment(part):
                    videos.append(rel_path)
                else:
                    refs.append(f"@{rel_path}")
                if part.caption and part.caption not in captions:
                    captions.append(part.caption)
            if videos:
                await message.reply_text("📎 视频已保存，未提交模型处理：\n" + "\n".join(str(Path(cwd) / v) for v in videos),
                                         parse_mode=None)
            if not refs:
                return
            text = "\n".join(refs + (captions or ["看看这些附件"]))
        else:
            rel_path, ext, err = await _download_attachment(message, context, cwd)
            if err:
                await message.reply_text(err)
                return
            if not rel_path:
                # 静默忽略（如贴纸）
                return
            if _is_video_attachment(message):
                await message.reply_text(f"📎 视频已保存，未提交模型处理。\n路径：{Path(cwd) / rel_path}", parse_mode=None)
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
            f"⚠️ {backends.get_backend(cwd)} 终端启动失败，请检查本机 CLI 的登录和网关配置。"
        )
        return

    # Always verify the actual Codex rollout before injecting a message.
    # Existing terminals can change sessions independently of the bot.
    codex_first_turn = False
    codex_start_offsets = None
    if backends.get_backend(cwd) == "codex":
        from src.api_server import register_pane_watcher
        codex_first_turn = not await backends.codex_transcript(target_pane, cwd)
        if codex_first_turn:
            # No open rollout may mean a RESUMING CLI, not a new conversation.
            codex_start_offsets = backends.codex_transcript_offsets()
        if not codex_first_turn and not await register_pane_watcher(target_pane, cwd):
            await message.reply_text("⚠️ 尚未找到 Codex 会话日志，请在终端完成启动后重试。")
            return

    # Claude pane 未被监听（如服务重启时 jsonl 尚未生成）：记下投递前偏移，投递后再接上，避免漏转回复
    claude_start_offsets = None
    if backends.get_backend(cwd) == "claude":
        from src.api_server import terminal_sessions
        if not any(v.get("tmux_pane") == target_pane for v in terminal_sessions.values()):
            claude_start_offsets = backends.claude_transcript_offsets(cwd)

    # 标记：来源=TG（watcher 跳过 user event ephemeral 避免重复）+ 本轮首条 assistant text 应 quote-reply 到这条用户消息
    if not is_private and topic_id:
        from src.api_server import mark_tg_input, mark_tg_reply_target
        mark_tg_input(topic_id, text)
        mark_tg_reply_target(topic_id, message.message_id)
    start_typing(context.bot, chat_id, session_key)
    receipt_from = await _transcript_position(target_pane, cwd)
    await _inject_tmux(target_pane, text)
    if receipt_from:  # 后台确认会话真的收到了这条消息，丢了就提醒（不阻塞后续投递）
        asyncio.create_task(_confirm_delivery(message, target_pane, cwd, text, *receipt_from))
    # 👀：已送进会话；这一轮结束时 liveness 改成 👌。只在群组模式：私聊里 bot 的表情回应会触发
    # 客户端的“新回应”提示（💟 角标），用户要求私聊关闭（2026-10-22）
    if not is_private and topic_id and not _private_thread_mode():
        from src import liveness
        liveness.note_user_message(topic_id, message.message_id)
        asyncio.create_task(liveness.react(context.bot, chat_id, message.message_id, "👀"))
    if claude_start_offsets is not None:
        from src.api_server import register_pane_watcher
        if not await register_pane_watcher(target_pane, cwd, start_offsets=claude_start_offsets):
            stop_typing(session_key)
            await message.reply_text("⚠️ 已投递，但尚未找到 Claude 会话日志，回复可能不会转发；请检查终端。")
    if codex_first_turn:
        # Consume only bytes added since input; old resumed history must never replay.
        if not await register_pane_watcher(target_pane, cwd, start_offsets=codex_start_offsets):
            stop_typing(session_key)
            await message.reply_text("⚠️ 已投递，但 Codex 尚未生成日志；请检查终端的启动提示。")


# ============ 构建 App ============

def _strip_md(text: str) -> str:
    import re
    return re.sub(r"[*`#_]+", "", text)


async def _send_topic_welcome(bot, chat_id: int, topic_id: int, name: str, path: str):
    """话题欢迎消息 + 最近对话上下文；私聊 Thread 模式再补上输入框下方的快捷键盘"""
    try:
        await _send_topic_welcome_text(bot, chat_id, topic_id, name, path)
    finally:
        if int(chat_id) > 0:
            try:
                await ensure_quick_keyboard(bot, chat_id, topic_id)
            except Exception as exc:
                log.warning("[keyboard] 发送快捷键盘失败 thread=%s: %s", topic_id, exc)


async def _send_topic_welcome_text(bot, chat_id: int, topic_id: int, name: str, path: str):

    history = state.get_recent_history(path, max_turns=3)

    if not history:
        await bot.send_message(
            chat_id=chat_id, message_thread_id=topic_id,
            text=f"<b>{name}</b>\n直接发消息开始对话。",
            parse_mode="HTML",
        )
        return

    # 渲染最近对话
    from src.renderer import md_to_tg_html
    lines, plain = [f"<b>{html_lib.escape(name)}</b> — 最近对话：\n"], [f"{name} — 最近对话：\n"]
    for msg in history:
        role = "👤" if msg["role"] == "user" else "✦"
        raw = msg["text"][:300] + ("…" if len(msg["text"]) > 300 else "")
        lines.append(f"{role} {md_to_tg_html(raw)}\n")  # 与正式回复一样渲染 Markdown
        plain.append(f"{role} {_strip_md(raw)}\n")
    lines.append("\n直接发消息继续对话。")
    plain.append("\n直接发消息继续对话。")

    full_text = "\n".join(lines)
    if len(full_text) > 4000:
        full_text = "\n".join(plain)[:4000]  # 太长时退回纯文本，避免截断破坏 HTML 标签
    try:
        await bot.send_message(chat_id=chat_id, message_thread_id=topic_id, text=full_text,
                               parse_mode="HTML" if len("\n".join(lines)) <= 4000 else None)
        return
    except Exception as exc:  # 截断的 Markdown 偶尔渲染出不合法的 HTML：退回去掉符号的纯文本
        log.warning("[welcome] HTML 发送失败，改纯文本: %s", exc)
        full_text = "\n".join(plain)[:4000]
    await bot.send_message(chat_id=chat_id, message_thread_id=topic_id, text=full_text)


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

    # Missing paths may be renames, unmounted disks, or temporary outages.
    # Discovery must never delete Telegram history or unpin user messages.
    for topic_id, path in projects.all_bindings().items():
        if path and not Path(path).is_dir():
            log.warning("[sync] directory missing; preserving topic=%s path=%s", topic_id, path)

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
        bound_paths.add(str(Path(path).resolve()))
        name = Path(path).name
        bound_names[name] = bound_names.get(name, 0) + 1

    log.info(f"[sync] 发现 {len(unbound)} 个未绑定项目，已绑定路径数: {len(bound_paths)}")
    for p in unbound:
        if str(Path(p["path"]).resolve()) in bound_paths:
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
            from src import liveness
            liveness.remember_topic_name(topic.message_thread_id, topic_name)
            # 发送欢迎消息 + 历史会话列表
            await _send_topic_welcome(bot, chat_id, topic.message_thread_id, topic_name, p["path"])
            bound_names[p["name"]] = name_count + 1
            bound_paths.add(str(Path(p["path"]).resolve()))
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
                bound_paths.add(str(Path(p["path"]).resolve()))
                log.info(f"[sync] {p['name']} → topic {topic.message_thread_id} (重试成功)")
            except Exception as e2:
                log.warning(f"[sync] {p['name']} 重试也失败: {e2}")
        except Exception as e:
            log.warning(f"[sync] {p['name']} 创建话题失败: {e}")


async def _drop_own_topic_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """状态符号改话题名时 TG 会插一条“修改了话题名称”的系统消息，删掉免得刷屏。"""
    message = update.effective_message
    if message and message.from_user and message.from_user.id == context.bot.id:
        try:
            await message.delete()
        except Exception as exc:
            log.debug("[liveness] 删除改名系统消息失败: %s", exc)


def create_app() -> Application:
    # 网络健壮化：
    # 1. 显式 timeout 让单次请求不会无限挂
    # 2. 禁用 httpx keepalive 池 → 每次请求新建 TCP，避免"死连接被无限复用"导致 getUpdates 假死
    # 3. DC 故障转移 → 系统 DNS 解析到失效 Telegram IP 时自动换 IP 直连（见 src/tg_transport.py）
    # getUpdates 是 long-poll，用独立 client（跟普通 sendMessage 隔离，互不踩连接池）
    import httpx
    from src.tg_transport import build_request

    _no_keepalive = {"limits": httpx.Limits(max_keepalive_connections=0, max_connections=10)}

    updates_req = build_request(
        read_timeout=60, connect_timeout=15, pool_timeout=10,
        httpx_kwargs=_no_keepalive,
    )
    default_req = build_request(
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
            BotCommand("model", "模型 / effort / 权限：/model list 查看全部"),
            BotCommand("permissions", "Codex 权限：full / workspace / read-only"),
            BotCommand("abort", "中断当前回合，丢弃还没处理的排队消息"),
            BotCommand("screen", "看当前项目终端的截图"),
            BotCommand("new", "新建项目：/new 名字 [claude|codex]"),
            BotCommand("projects", "项目列表"),
            BotCommand("sessions", "历史会话"),
            BotCommand("status", "活跃会话"),
            BotCommand("issues", "GitLab issue 状态汇总（待验证/处理中/待处理）"),
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
        if config.CHAT_ID:
            from src import liveness
            asyncio.create_task(liveness.Monitor(application.bot, config.CHAT_ID).run())
    app.post_init = post_init

    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("permissions", cmd_permissions))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("sessions", cmd_sessions))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("issues", cmd_issues))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("abort", cmd_abort))
    app.add_handler(CommandHandler("screen", cmd_screen))
    app.add_handler(TypeHandler(Update, _on_stopped_generation), group=-1)  # 草稿气泡的原生停止按钮
    app.add_handler(CommandHandler("trust", cmd_trust))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.StatusUpdate.FORUM_TOPIC_EDITED, _drop_own_topic_edit))
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
            (filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.VIDEO_NOTE | filters.AUDIO
             | filters.VOICE | filters.Sticker.ALL)
            & (filters.ChatType.SUPERGROUP | filters.ChatType.PRIVATE),
            handle_message,
        )
    )

    return app
