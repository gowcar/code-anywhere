"""HTTP API Server — 接收 Claude Code Hook 回调 + web 客户端总线

Hook 端点：
  POST /session_start  — 终端 Claude 启动
  POST /session_stop   — 终端 Claude 停止
  POST /notification   — Claude 输出通知
  POST /permission     — 工具权限审批请求

Web client 总线（web admin 接入用）：
  GET  /sessions/active           — 当前活跃终端会话列表
  GET  /events/stream?session_id= — SSE，订阅指定 session 的 transcript 事件
  POST /sessions/{id}/input       — 通过 tmux send-keys 注入用户输入

通用：
  GET  /health                    — 健康检查
"""

import asyncio
import json
import logging
import uuid
from collections import defaultdict, deque
from pathlib import Path

from aiohttp import web

from src.config import BOT_PORT
from src import backends
from src import projects
from src import state
import src.config as config

log = logging.getLogger("code-anywhere.api")

# 权限审批等待队列：request_id → {event, decision}
pending_permissions: dict[str, dict] = {}

# 终端活跃会话：session_id → {topic_id, chat_id, tmux_pane, ...}
terminal_sessions: dict[str, dict] = {}

# 从 TG 注入的用户消息队列：topic_id → [text, ...]
# bot.py 注入 tmux 后 mark_tg_input；watcher 看到匹配的 user event 时 pop 并 skip 显示
# 避免 TG 端"自己气泡 + bot ephemeral 👤"的重复
_pending_tg_inputs: dict[int, list[str]] = {}


def mark_tg_input(topic_id: int, text: str) -> None:
    if topic_id is None:
        return
    _pending_tg_inputs.setdefault(topic_id, []).append(text)


def _consume_tg_input(topic_id: int, text: str) -> bool:
    q = _pending_tg_inputs.get(topic_id)
    if not q:
        return False
    try:
        q.remove(text)
    except ValueError:
        return False
    if not q:
        _pending_tg_inputs.pop(topic_id, None)
    return True


# 从 TG 触发的下一轮 assistant 输出应该 quote-reply 到哪条用户消息
# bot.py 注入 tmux 前 mark_tg_reply_target；watcher 首条 assistant text 消耗
# 每轮只 quote 一次，后续 chunk 都是普通消息
_pending_tg_reply: dict[int, int] = {}


def mark_tg_reply_target(topic_id: int, message_id: int) -> None:
    if topic_id is None or not message_id:
        return
    # 用户连发覆盖：只 quote 最新那条
    _pending_tg_reply[topic_id] = message_id


def _consume_tg_reply(topic_id: int) -> int | None:
    return _pending_tg_reply.pop(topic_id, None)


# ============ AskUserQuestion 菜单 → TG inline keyboard ============
# 三形态：单问单选（点即提交）/ 单问多选 / 多页表单（累积各页选择后一次提交）。
# 按键机制（sandbox 实测）：单选项 Down×idx+Enter（选中且自动前进到下一页/Submit）；
# 多选项 Down 定位 + Space toggle；Tab 切下一页签；Submit 页 Enter 提交。每页光标初始在第0项。
# 关键事实：菜单等待时 tool_use(AskUserQuestion) 已结构化写入 transcript，watcher 直接可读。
_pending_menus: dict[str, dict] = {}
# token → {topic_id, chat_id, pane, msg_id,
#          questions:[{header,question,multiSelect,options:[label,...]}],
#          selections:{qi: int|None(单选) | set(多选)}}


def _menu_token(tool_use_id: str) -> str:
    return (tool_use_id or uuid.uuid4().hex)[:12]


def _menu_keyboard(token: str):
    """按当前 selections 渲染键盘。单问单选无提交键（点即提交），其余带「提交选择」。"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    m = _pending_menus[token]
    qs = m["questions"]
    multi_q = len(qs) > 1
    rows = []
    for qi, q in enumerate(qs):
        sel = m["selections"].get(qi)
        answered = bool(sel) if q["multiSelect"] else (sel is not None)
        if multi_q:  # 多页时用一行标注每问（占位按钮，点了忽略）
            rows.append([InlineKeyboardButton(
                f"{'☑' if answered else '▫'} {q['header'] or ('问' + str(qi + 1))}",
                callback_data="mnop")])
        for oi, label in enumerate(q["options"]):
            picked = (oi in sel) if q["multiSelect"] else (sel == oi)
            rows.append([InlineKeyboardButton(
                f"{'✅ ' if picked else ''}{label}"[:60],
                callback_data=f"ms:{token}:{qi}:{oi}")])
    single_radio = len(qs) == 1 and not qs[0]["multiSelect"]
    if not single_radio:
        rows.append([InlineKeyboardButton("✅ 提交选择", callback_data=f"mok:{token}")])
    return InlineKeyboardMarkup(rows)


def menu_keyboard_for(token: str):
    """给 bot callback 刷新键盘用；token 失效返回 None。"""
    return _menu_keyboard(token) if token in _pending_menus else None


async def _offer_menu(chat_id: int, topic_id: int, session_id: str,
                      tool_use_id: str, tinput: dict) -> bool:
    """AskUserQuestion → TG inline keyboard。返回 True=已接管。"""
    import html as _html

    questions = tinput.get("questions") or []
    if not questions:
        return False
    sess = terminal_sessions.get(session_id) or {}
    pane = sess.get("tmux_pane")
    if not pane or not _bot:
        return False

    qs = []
    for q in questions:
        opts = [(o.get("label") or "?") for o in (q.get("options") or [])]
        if not opts:
            return False
        qs.append({
            "header": q.get("header") or "",
            "question": q.get("question") or "",
            "multiSelect": bool(q.get("multiSelect")),
            "options": opts,
        })

    token = _menu_token(tool_use_id)
    _pending_menus[token] = {
        "topic_id": topic_id, "chat_id": chat_id, "pane": pane, "msg_id": None,
        "questions": qs,
        "selections": {qi: (set() if q["multiSelect"] else None) for qi, q in enumerate(qs)},
    }

    if len(qs) == 1:
        q = qs[0]
        tail = "（可多选，选完点提交）" if q["multiSelect"] else ""
        body = f"❓ <b>{_html.escape(q['header'] or '请选择')}</b>{tail}\n{_html.escape(q['question'])}"
    else:
        lines = [f"❓ <b>请依次作答（{len(qs)} 问），选完点「提交选择」</b>"]
        for qi, q in enumerate(qs):
            mark = "（多选）" if q["multiSelect"] else ""
            lines.append(f"{qi + 1}. {_html.escape(q['question'] or q['header'])}{mark}")
        body = "\n".join(lines)

    try:
        from src.bot import stop_typing
        stop_typing(topic_id)
    except Exception:
        pass
    msg = await _bot.send_message(
        chat_id=chat_id, message_thread_id=topic_id,
        text=body, parse_mode="HTML",
        reply_markup=_menu_keyboard(token),
    )
    try:
        _pending_menus[token]["msg_id"] = msg.message_id
    except Exception:
        pass
    log.info(f"[menu] offered token={token} topic={topic_id} questions={len(qs)}")
    return True


async def _inject_keys(pane: str, keys: list[str]):
    """一次性 tmux send-keys 多个具名按键。"""
    if not keys:
        return
    proc = await asyncio.create_subprocess_exec(
        "tmux", "send-keys", "-t", pane, *keys,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.wait()


async def _inject_key_seq(pane: str, keys: list[str]):
    """逐键注入：Enter/Tab 会切页/提交，其后留停顿让 TUI 跟上，避免漏键。"""
    for k in keys:
        await _inject_keys(pane, [k])
        await asyncio.sleep(0.25 if k in ("Enter", "Tab") else 0.04)


def _build_keys(m: dict) -> list[str]:
    """据 selections 生成完整按键序列（见顶部机制注释）。"""
    qs = m["questions"]
    keys: list[str] = []
    for qi, q in enumerate(qs):
        sel = m["selections"].get(qi)
        if not q["multiSelect"]:
            idx = sel if isinstance(sel, int) else 0
            keys += ["Down"] * idx + ["Enter"]  # 选中 + 自动前进（末页则到 Submit）
        else:
            prev = 0
            for idx in sorted(sel or []):
                keys += ["Down"] * (idx - prev) + ["Space"]
                prev = idx
            keys += ["Tab"]  # 前进到下一页签
    single_radio = len(qs) == 1 and not qs[0]["multiSelect"]
    if not single_radio:
        keys += ["Enter"]  # Submit 页提交
    return keys


async def _submit_menu(token: str) -> bool:
    m = _pending_menus.pop(token, None)
    if not m:
        return False
    keys = _build_keys(m)
    await _inject_key_seq(m["pane"], keys)
    log.info(f"[menu] submitted token={token} keys={keys}")
    return True


async def handle_menu_select(token: str, qi: int, oi: int) -> str | None:
    """点某问某项。更新 selections；单问单选→即提交('submitted')；否则返回'updated'刷新键盘。"""
    m = _pending_menus.get(token)
    if not m:
        return None
    if qi >= len(m["questions"]):
        return "updated"
    q = m["questions"][qi]
    if oi >= len(q["options"]):
        return "updated"
    if q["multiSelect"]:
        s = m["selections"][qi]
        if not isinstance(s, set):
            s = set()
            m["selections"][qi] = s
        s.discard(oi) if oi in s else s.add(oi)
    else:
        m["selections"][qi] = oi
    if len(m["questions"]) == 1 and not q["multiSelect"]:
        await _submit_menu(token)
        return "submitted"
    return "updated"


async def handle_menu_submit(token: str) -> str | None:
    """点「提交选择」。"""
    if token not in _pending_menus:
        return None
    await _submit_menu(token)
    return "submitted"


# ── Web client bus ───────────────────────────────────────────────────────────
# SSE 订阅者池：session_id → [asyncio.Queue, ...]
# 每个 web 客户端订阅时挂一个 Queue；watcher 解析 JSONL 后 fan-out 给所有 queue。
_sse_subs: dict[str, list[asyncio.Queue]] = defaultdict(list)

# 每个 session 最近 N 条事件缓存，供 web 端晚到时回放（reconnect / 刷新页面）
_event_replay: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))


def broadcast_event(session_id: str, event: dict) -> None:
    """Watcher 解析出一条 JSONL 事件后，扇出给所有 web SSE 订阅者。

    同时写入 replay 缓存（最近 200 条），让刚连上的 web 端能拉到最近的对话。
    """
    _event_replay[session_id].append(event)
    subs = _sse_subs.get(session_id)
    if not subs:
        return
    dead = []
    for q in subs:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            log.warning(f"[sse] subscriber queue full, dropping for session={session_id[:12]}")
        except Exception as e:
            log.warning(f"[sse] broadcast fail: {e}")
            dead.append(q)
    for q in dead:
        try:
            subs.remove(q)
        except ValueError:
            pass

# Bot 实例引用（启动时设置）
_bot = None
_tg_app = None


def set_bot(bot, tg_app):
    global _bot, _tg_app
    _bot = bot
    _tg_app = tg_app


def retire_project_watchers(cwd: str, backend: str, keep_sid: str | None = None):
    """Remove inactive backend / stale native sessions for this project."""
    for sid, info in list(terminal_sessions.items()):
        if info.get("cwd") != cwd:
            continue
        if info.get("backend", "claude") == backend and (keep_sid is None or sid == keep_sid):
            continue
        task = _watchers.pop(sid, None)
        if task:
            task.cancel()
        terminal_sessions.pop(sid, None)


async def register_pane_watcher(pane_id: str, cwd: str, from_start: bool = False) -> bool:
    if not _bot or not config.CHAT_ID:
        return False
    topic_id = projects.get_topic(cwd)
    if not topic_id:
        return False
    backend = backends.get_backend(cwd)
    if not await backends.pane_processes(pane_id, backend):
        return False
    latest = None
    sid = None
    for _ in range(10):
        if backend == "codex":
            found = await backends.codex_transcript(pane_id, cwd)
            if found:
                sid, path = found
                latest = Path(path)
        else:
            proj_path = projects.claude_dir(cwd)
            files = list(proj_path.glob("*.jsonl")) if proj_path else []
            if files:
                latest = max(files, key=lambda x: x.stat().st_mtime)
                sid = latest.stem
        if latest:
            break
        await asyncio.sleep(0.5)
    if not latest or not sid:
        log.warning("[register_pane] no %s transcript: %s pane=%s", backend, cwd, pane_id)
        return False
    if backend == "codex":
        backends.remember_codex(cwd, sid)
    retire_project_watchers(cwd, backend, keep_sid=sid)
    existing = _watchers.get(sid)
    if sid in terminal_sessions and existing and not existing.done():
        terminal_sessions[sid]["tmux_pane"] = pane_id
        return True
    model = ""
    permission_mode = None
    if backend == "codex":
        # Model changes are recorded in turn_context, including resumed sessions.
        with latest.open() as f:
            for line in f:
                try:
                    ev = json.loads(line)
                    if ev.get("type") == "turn_context":
                        model = ev.get("payload", {}).get("model", model)
                        permission_mode = backends.normalize_codex(ev).get("permission_mode")
                except ValueError:
                    continue
    if backend == "codex":
        # A restart's launch flags take effect before the next turn_context exists.
        tagged_mode = await backends.command("tmux", "display-message", "-p", "-t", pane_id, "#{@ca_permission}")
        if tagged_mode in {"full", "workspace", "read-only"}:
            permission_mode = tagged_mode
    terminal_sessions[sid] = {
        "permission_mode": permission_mode,
        "topic_id": topic_id, "chat_id": config.CHAT_ID, "cwd": cwd,
        "tmux_pane": pane_id, "model": model, "backend": backend,
        "transcript_path": str(latest),
    }
    old = _watchers.pop(sid, None)
    if old:
        old.cancel()
    # Establish byte offset NOW, before callers can inject the first message.
    _watchers[sid] = asyncio.create_task(_watch_transcript(
        sid, str(latest), topic_id, config.CHAT_ID, start_pos=0 if from_start else latest.stat().st_size))
    log.info("[register_pane] %s %s pane=%s sid=%s topic=%s", backend, Path(cwd).name, pane_id, sid, topic_id)
    return True


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

    # Bot spawn 的 Claude 已通过 CODE_ANYWHERE_SKIP 环境变量跳过 hook
    # 这里只会收到终端会话的回调，所以不再排除自身项目

    if not _bot or not config.CHAT_ID:
        return web.json_response({"ok": True})

    # 单 session 模式：每个项目唯一 topic
    topic_id = projects.get_topic(cwd)
    if not topic_id:
        log.info(f"[session_start] 项目 {cwd} 未绑定话题，跳过")
        return web.json_response({"ok": True})

    # 非交互式会话（无 tmux pane）跳过 — 由其他服务自行管理（如 claude-agent）
    if not tmux_pane:
        log.info(f"[session_start] {Path(cwd).name} 无 tmux pane，跳过（非交互式）")
        return web.json_response({"ok": True})

    if backends.get_backend(cwd) != "claude":
        return web.json_response({"ok": True, "ignored": "inactive backend"})
    retire_project_watchers(cwd, "claude", keep_sid=session_id)

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
    log.info(f"[session_start] {project_name} sid={session_id[:8]} → topic {topic_id}")

    # 启动 JSONL 监听（流式同步终端输出到 TG）— watcher 按 sid 索引，多 session 互不踩
    if transcript_path:
        old_task = _watchers.pop(session_id, None)
        if old_task:
            old_task.cancel()
        _watchers[session_id] = asyncio.create_task(
            _watch_transcript(session_id, transcript_path, topic_id, config.CHAT_ID)
        )

    return web.json_response({"ok": True})


# JSONL Watcher 任务：session_id → Task（多 session 改造后按 sid 索引）
_watchers: dict[str, asyncio.Task] = {}


async def _watch_transcript(session_id: str, path: str, topic_id: int, chat_id: int, start_pos: int | None = None):
    """监听 JSONL 文件，流式转发终端输出到 TG"""
    import base64 as _b64
    import io as _io
    import html as html_lib
    from src.renderer import format_tool_use, split_message, send_ephemeral

    seen_ids = set()
    sent_image_tool_ids: set[str] = set()  # 已发图的 tool_use_id（按 sid 维度去重）
    tool_use_image_paths: dict[str, str] = {}  # tool_use_id → 原图绝对路径（按白名单从 tool_use 块抓）
    last_pos = 0

    IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

    def _abs_image_path(p: str) -> str | None:
        """相对路径按 session cwd 拼绝对；后缀必须是图片"""
        if not p:
            return None
        pp = Path(p)
        if not pp.is_absolute():
            cwd = (terminal_sessions.get(session_id) or {}).get("cwd") or ""
            if not cwd:
                return None
            pp = Path(cwd) / p
        if pp.suffix.lower() not in IMAGE_EXTS:
            return None
        return str(pp)

    # 跳到文件末尾（只监听新内容）
    try:
        last_pos = Path(path).stat().st_size if start_pos is None else start_pos
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

    backend = terminal_sessions.get(session_id, {}).get("backend", "claude")
    cwd = terminal_sessions.get(session_id, {}).get("cwd", "")
    pane_check_counter = 0
    try:
        while True:
            await asyncio.sleep(0.5)
            if cwd and backends.get_backend(cwd) != backend:
                break
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

            with open(path, "rb") as f:
                f.seek(last_pos)
                new_bytes = f.read()
            end = new_bytes.rfind(b"\n")
            if end < 0:
                continue
            last_pos += end + 1
            new_data = new_bytes[:end + 1].decode("utf-8", errors="replace")

            for line in new_data.splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if backend == "codex":
                    event = backends.normalize_codex(event)
                    if event is None:
                        continue
                    if event.get("type") == "system":
                        terminal_sessions.get(session_id, {})["model"] = event.get("model", "")
                        if event.get("permission_mode"):
                            terminal_sessions.get(session_id, {})["permission_mode"] = event["permission_mode"]
                    elif event.get("type") == "result":
                        from src.bot import stop_typing
                        stop_typing(topic_id)
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

                # Fan-out 给 web SSE 订阅者（不阻塞 TG 发送）
                try:
                    broadcast_event(session_id, event)
                except Exception as e:
                    log.warning(f"[watcher] broadcast fail: {e}")

                try:
                    if event_type == "user":
                        content = event.get("message", {}).get("content", "")
                        text = ""
                        if isinstance(content, str):
                            text = content
                        elif isinstance(content, list):
                            # 扫 tool_result 触发发图
                            # 触发条件（任一满足，且 tool_use_id 没发过图）：
                            #  a) tool_use 阶段记了原图路径 → 文件落盘后发原图（send_document）
                            #  b) tool_result inner 含 image block → 没原图时退化用 base64 缩略图（send_photo）
                            for c in content:
                                if c.get("type") != "tool_result":
                                    continue
                                tool_use_id = c.get("tool_use_id", "")
                                if not tool_use_id or tool_use_id in sent_image_tool_ids:
                                    continue
                                inner = c.get("content")
                                imgs = [x for x in inner if isinstance(x, dict) and x.get("type") == "image"] if isinstance(inner, list) else []
                                orig_path = tool_use_image_paths.pop(tool_use_id, None)
                                if not orig_path and not imgs:
                                    continue  # 跟图无关

                                sent = False
                                if orig_path and Path(orig_path).is_file():
                                    # 原图永远走 send_document：TG 的 send_photo 会客户端压缩到 ~1280 长边 + 转 JPEG，长图会糊
                                    try:
                                        size = Path(orig_path).stat().st_size
                                        with open(orig_path, "rb") as f:
                                            await _bot.send_document(
                                                chat_id=chat_id,
                                                message_thread_id=topic_id,
                                                document=f,
                                                filename=Path(orig_path).name,
                                            )
                                        log.info(f"[img] sent ORIG-doc {size}B path={orig_path} tool_use_id={tool_use_id[:12]}")
                                        sent = True
                                    except Exception as e:
                                        log.warning(f"[img] send orig fail ({orig_path}): {e}")

                                if not sent:
                                    # Fallback：原图缺失（如 Bash 截屏未落文件、或工具没在白名单），用 base64 缩略图
                                    for item in imgs[:4]:
                                        src = item.get("source", {}) or {}
                                        if src.get("type") != "base64":
                                            continue
                                        raw_b64 = src.get("data") or ""
                                        if not raw_b64:
                                            continue
                                        media_type = src.get("media_type", "image/png")
                                        ext = "jpg" if "jpeg" in media_type else (
                                            "png" if "png" in media_type else
                                            "webp" if "webp" in media_type else
                                            "gif" if "gif" in media_type else "bin"
                                        )
                                        try:
                                            raw = _b64.b64decode(raw_b64)
                                        except Exception as e:
                                            log.warning(f"[img] base64 decode fail: {e}")
                                            continue
                                        size = len(raw)
                                        bio = _io.BytesIO(raw)
                                        bio.name = f"claude.{ext}"
                                        try:
                                            if size <= 10 * 1024 * 1024:
                                                await _bot.send_photo(
                                                    chat_id=chat_id,
                                                    message_thread_id=topic_id,
                                                    photo=bio,
                                                )
                                            else:
                                                bio.seek(0)
                                                await _bot.send_document(
                                                    chat_id=chat_id,
                                                    message_thread_id=topic_id,
                                                    document=bio,
                                                    filename=bio.name,
                                                )
                                            log.info(f"[img] sent THUMB {size}B (no orig) tool_use_id={tool_use_id[:12]} media={media_type}")
                                        except Exception as e:
                                            log.warning(f"[img] send thumb fail: {e}")
                                sent_image_tool_ids.add(tool_use_id)
                            # 再走原有 user 文本逻辑（用户在终端输入的内容）
                            for c in content:
                                if c.get("type") == "text":
                                    text = c["text"]
                                    break
                        if text.strip():
                            # 只 echo 真人消息（origin.kind == 'human'）。
                            # compact 摘要(isCompactSummary)、harness 注入([Your previous
                            # response had no visible output...])、[Request interrupted by user]
                            # 等都是 origin=None 的非真人条目 —— 永久 echo 会污染 topic，静默跳过。
                            _origin = event.get("origin") or {}
                            _is_human = isinstance(_origin, dict) and _origin.get("kind") == "human"
                            # 来源是 TG → TG 端已有用户气泡，skip 避免重复
                            # 来源是 web/终端真人 → 在 TG 永久显示 👤，保证 3 端历史一致
                            if _is_human and not _consume_tg_input(topic_id, text):
                                truncated = html_lib.escape(text[:500])
                                try:
                                    await _bot.send_message(
                                        chat_id=chat_id,
                                        message_thread_id=topic_id,
                                        text=f"👤 {truncated}",
                                        parse_mode="HTML",
                                    )
                                except Exception as e:
                                    log.warning(f"[watcher] user echo 发送失败: {e}")

                    elif event_type == "assistant":
                        content = event.get("message", {}).get("content", [])
                        for block in content:
                            if block.get("type") == "text":
                                text = block.get("text", "").strip()
                                if text:
                                    # Claude 出真正的回复了 → 停掉注入路径的 typing
                                    try:
                                        from src.bot import stop_typing
                                        stop_typing(topic_id)
                                    except Exception:
                                        pass
                                    chunks = split_message(text, render=True)
                                    # 每轮首条 assistant text quote-reply 到触发的用户消息；后续 chunk 不 quote
                                    reply_to = _consume_tg_reply(topic_id)
                                    for rendered, raw in chunks:
                                        send_kwargs = dict(
                                            chat_id=chat_id, message_thread_id=topic_id,
                                            text=f"🤖 {rendered}",
                                            parse_mode="HTML",
                                        )
                                        if reply_to:
                                            send_kwargs["reply_to_message_id"] = reply_to
                                            reply_to = None  # 只用一次
                                        try:
                                            await _bot.send_message(**send_kwargs)
                                        except Exception as e:
                                            log.warning(f"[watcher] HTML 发送失败: {e}")
                                            from telegram.error import BadRequest
                                            if isinstance(e, BadRequest):
                                                fb = {**send_kwargs, "text": f"🤖 {raw}"}
                                                fb.pop("parse_mode", None)
                                                await _bot.send_message(**fb)
                                            else:
                                                try:
                                                    await _bot.send_message(
                                                        **send_kwargs,
                                                        read_timeout=30,
                                                        connect_timeout=15,
                                                    )
                                                except Exception:
                                                    fb = {**send_kwargs, "text": f"🤖 {raw}"}
                                                    fb.pop("parse_mode", None)
                                                    await _bot.send_message(**fb)
                            elif block.get("type") == "tool_use":
                                tname = block.get("name", "?")
                                tinput = block.get("input", {}) or {}
                                tid = block.get("id", "")
                                # AskUserQuestion → 转 TG inline keyboard（阶段1: 单问单选）
                                if tname == "AskUserQuestion":
                                    try:
                                        if await _offer_menu(chat_id, topic_id, session_id, tid, tinput):
                                            continue  # 已接管，不再走原工具展示
                                    except Exception as e:
                                        log.warning(f"[menu] offer 失败: {e}")
                                # 白名单：抓原图路径供 tool_result 阶段读取真图
                                cand: str | None = None
                                if tname == "mcp__Playwright__browser_take_screenshot":
                                    cand = tinput.get("filename")
                                elif tname in ("Read", "Write", "MultiEdit", "NotebookEdit"):
                                    cand = tinput.get("file_path")
                                if tid and cand:
                                    abs_p = _abs_image_path(cand)
                                    if abs_p:
                                        tool_use_image_paths[tid] = abs_p
                                desc = format_tool_use(tname, tinput)
                                await send_ephemeral(
                                    _bot, chat_id,
                                    desc,
                                    message_thread_id=topic_id,
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

    # 兜底：本轮结束 → 停掉注入路径的 typing（防止 assistant 无文本块的边缘情况）
    info = terminal_sessions.get(session_id)
    if info and info.get("topic_id"):
        try:
            from src.bot import stop_typing
            stop_typing(info["topic_id"])
        except Exception:
            pass

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
        from src.renderer import send_ephemeral
        text = html.escape(message[:4000])
        if title:
            text = f"<b>{html.escape(title)}</b>\n{text}"
        await send_ephemeral(
            _bot, info["chat_id"],
            text,
            message_thread_id=info["topic_id"],
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
        "sse_subscribers": {sid: len(qs) for sid, qs in _sse_subs.items() if qs},
    })


# ── Web client bus endpoints ─────────────────────────────────────────────────
async def handle_sessions_active(request: web.Request) -> web.Response:
    """GET /sessions/active — 列出当前活跃的终端会话。

    供 web 端展示会话列表。每条带 title / last_activity_at（JSONL mtime），
    前端按 last_activity_at 分组（今天/昨天/...），title 走 ChatGPT 风格。
    """
    import subprocess

    # 一次 tmux 调用拿 window_name 做默认 title 兜底
    pane_window: dict[str, str] = {}
    try:
        cp = subprocess.run(
            ["tmux", "list-panes", "-a", "-F", "#{pane_id}\t#{window_name}"],
            capture_output=True, text=True, timeout=2,
        )
        for line in (cp.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                pane_window[parts[0]] = parts[1]
    except Exception:
        pass

    sessions = []
    for sid, info in terminal_sessions.items():
        cwd = info.get("cwd", "")
        pane = info.get("tmux_pane", "")
        window_name = pane_window.get(pane, "")
        # title 优先级：看起来像人起的 window_name > "项目名 · #短sid"
        # 排除 fish/zsh/bash 这种 shell 名；排除 "2.1.114" 这种版本号；排除带 [tmux] 字样
        wn_looks_human = bool(window_name) and not (
            window_name in ("fish", "zsh", "bash", "sh", "node", "python", "claude")
            or all(c.isdigit() or c == "." for c in window_name)
            or "[tmux]" in window_name
            or window_name.startswith("kin-")  # 我们自动生成的默认 name
        )
        if wn_looks_human:
            title = window_name
        else:
            title = f"{Path(cwd).name} · #{sid[:8]}"
        # last_activity_at：取 JSONL 文件 mtime
        transcript_path = info.get("transcript_path", "")
        last_activity = 0.0
        if transcript_path:
            try:
                last_activity = Path(transcript_path).stat().st_mtime
            except Exception:
                pass
        sessions.append({
            "session_id": sid,
            "cwd": cwd,
            "project": Path(cwd).name if cwd else "",
            "title": title,
            "tmux_pane": pane,
            "model": info.get("model", ""),
            "backend": info.get("backend", "claude"),
            "permission_mode": info.get("permission_mode"),
            "topic_id": info.get("topic_id"),
            "subscribers": len(_sse_subs.get(sid, [])),
            "last_activity_at": last_activity,
        })
    return web.json_response({"sessions": sessions})


async def handle_events_stream(request: web.Request) -> web.StreamResponse:
    """GET /events/stream?session_id=...&replay=1

    SSE 长连接。先回放最近事件（如果 replay=1），再实时推流。
    每 15s 一个 keepalive comment 防代理掐断。
    """
    session_id = request.query.get("session_id", "").strip()
    if not session_id:
        return web.json_response({"error": "session_id required"}, status=400)

    replay = request.query.get("replay", "1") not in ("0", "false", "no")

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream; charset=utf-8",
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx 别缓
        },
    )
    await resp.prepare(request)
    await resp.write(b": connected\n\n")

    # 把这个订阅者挂上
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _sse_subs[session_id].append(q)
    log.info(f"[sse] subscribe session={session_id[:12]} total_subs={len(_sse_subs[session_id])}")

    async def _send_event(ev: dict):
        payload = json.dumps(ev, ensure_ascii=False)
        await resp.write(f"data: {payload}\n\n".encode("utf-8"))

    try:
        # 回放：让刚连上的 web 端看到最近 200 条历史
        if replay:
            history = list(_event_replay.get(session_id, []))
            for ev in history:
                await _send_event(ev)
            await resp.write(b": replay-done\n\n")

        # 实时推流
        while True:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=15.0)
                await _send_event(ev)
            except asyncio.TimeoutError:
                # keepalive
                await resp.write(b": ping\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    except Exception as e:
        log.warning(f"[sse] stream error: {e}")
    finally:
        try:
            _sse_subs[session_id].remove(q)
        except ValueError:
            pass
        log.info(f"[sse] unsubscribe session={session_id[:12]} remaining={len(_sse_subs.get(session_id, []))}")

    return resp


async def handle_session_input(request: web.Request) -> web.Response:
    """POST /sessions/{id}/input — body={"text": "..."}

    通过 tmux send-keys 把用户文本注入终端 Claude 的输入。
    复用 bot._inject_tmux，与 TG → 终端的链路完全一致。
    """
    session_id = request.match_info["id"]
    info = terminal_sessions.get(session_id)
    if not info:
        return web.json_response({"error": "session not found"}, status=404)

    if backends.get_backend(info["cwd"]) != info.get("backend", "claude"):
        return web.json_response({"error": "inactive backend; refresh sessions"}, status=409)
    pane = info.get("tmux_pane")
    if not pane:
        return web.json_response({"error": "non-interactive session (no tmux pane)"}, status=400)

    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON body"}, status=400)

    text = (data.get("text") or "").rstrip("\n")
    if not text:
        return web.json_response({"error": "text required"}, status=400)

    # 懒导入避免循环
    from src.bot import _inject_tmux, _project_locks
    try:
        async with _project_locks.setdefault(info["cwd"], asyncio.Lock()):
            if backends.get_backend(info["cwd"]) != info.get("backend", "claude"):
                return web.json_response({"error": "inactive backend"}, status=409)
            if not await backends.pane_processes(pane, info.get("backend", "claude")):
                return web.json_response({"error": "terminal is no longer running"}, status=409)
            await _inject_tmux(pane, text)
    except Exception as e:
        log.warning(f"[web-input] inject fail: {e}")
        return web.json_response({"error": f"inject failed: {e}"}, status=500)

    log.info(f"[web-input] session={session_id[:12]} pane={pane} text={text[:60]!r}")
    return web.json_response({"ok": True})


def create_api_app() -> web.Application:
    app = web.Application()
    # Hook callbacks
    app.router.add_post("/session_start", handle_session_start)
    app.router.add_post("/session_stop", handle_session_stop)
    app.router.add_post("/notification", handle_notification)
    app.router.add_post("/permission", handle_permission)
    # Web client bus (read-only + input passthrough)
    app.router.add_get("/sessions/active", handle_sessions_active)
    app.router.add_get("/events/stream", handle_events_stream)
    app.router.add_post("/sessions/{id}/input", handle_session_input)
    # Common
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
    """Recover only selected native CLI processes, including Codex after restart."""
    if not _bot or not config.CHAT_ID:
        return
    from src.bot import _find_pane_for_project, _project_locks
    for topic_id, cwd in projects.all_bindings().items():
        async with _project_locks.setdefault(cwd, asyncio.Lock()):
            try:
                pane = await _find_pane_for_project(cwd)
                if pane:
                    await register_pane_watcher(pane, cwd)
            except Exception:
                log.exception("[recover] failed project=%s", cwd)
