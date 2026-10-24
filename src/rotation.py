"""自动换新会话：上下文反复压缩会“降智”（早先的约定变成摘要的摘要），压缩到一定次数就交接并开新会话。

全部由会话记录里的事件驱动，不用定时任务（2026-10-24 用户要求）：
- 压缩事件（Claude 的 compact_boundary、Codex 的 compacted）→ 计数；达到 ROTATE_AFTER 次 → 标记待换。
- 一轮结束（Claude 的 turn_duration、Codex 的 task_complete）且待换 → 发交接消息：让会话把进度写进
  recon/PROGRESS.md 并回复“交接完成：继续 / 无待办”。
- 交接那一轮结束且回复了“交接完成” → /clear（Codex 为 /new）开新会话；有待办就让新会话读外脑接着做。
  没回复“交接完成”（被打断、跑题）就不换，最多再试一次，之后等下一次压缩重来。
"""
import asyncio
import json
import logging
import os
import time
from pathlib import Path

from src.config import PROJECT_ROOT

log = logging.getLogger("code-anywhere.rotation")

ROTATE_AFTER = int(os.getenv("SESSION_ROTATE_AFTER_COMPACTIONS", "3"))   # 0 = 关闭
STATE_FILE = PROJECT_ROOT / ".session-rotation.json"
HANDOFF_MARK = "[code-anywhere 自动换新会话]"
DONE_WORD = "交接完成"

HANDOFF_TEXT = (
    f"{HANDOFF_MARK} 本会话上下文已经压缩了 {{n}} 次，再用下去会遗忘早先的约定。请现在做交接：把当前进度、"
    "没做完的事、关键约定和下一步写进 recon/PROGRESS.md 的「当前位置」（按外脑规则保持精简，必要时先建 recon/ 骨架），"
    "提交并推送；完成后只回复一行：`交接完成：继续`（还有没做完的事）或 `交接完成：无待办`。交接后会自动开新会话，新会话会先读外脑。"
)
CONTINUE_TEXT = "[code-anywhere 新会话] 上一个会话已交接。请先读 recon/PROGRESS.md 的「当前位置」，按其中的下一步继续。"


def _load() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1))
    tmp.replace(STATE_FILE)


def is_compaction(event: dict) -> bool:
    if event.get("type") == "compacted":                                   # Codex
        return True
    return event.get("type") == "system" and event.get("subtype") == "compact_boundary"   # Claude


def is_turn_end(event: dict, backend: str) -> bool:
    """Claude 每轮结束写 system/turn_duration；Codex 的一轮结束由调用方按 task_complete 判定。"""
    return backend == "claude" and event.get("type") == "system" and event.get("subtype") == "turn_duration"


def on_compaction(session_id: str) -> None:
    if ROTATE_AFTER <= 0:
        return
    state = _load()
    item = state.setdefault(session_id, {"compactions": 0, "phase": "", "attempts": 0})
    item["compactions"] += 1
    if item["compactions"] >= ROTATE_AFTER and not item["phase"]:
        item["phase"] = "pending"
    _save(state)
    log.info("[rotation] %s 压缩 %s 次 phase=%s", session_id[:8], item["compactions"], item["phase"])


async def on_turn_end(session_id: str, cwd: str, topic_id: int | None, pane: str, transcript: str) -> None:
    """一轮结束时推进交接；在后台任务里调用，不阻塞 watcher。"""
    state = _load()
    item = state.get(session_id)
    if not item or not item.get("phase"):
        return
    from src import backends, bot
    from src.abort import claude_has_queue, claude_running, codex_running
    screen = await bot._capture_pane(pane)
    if claude_running(screen) or codex_running(screen) or claude_has_queue(screen) or "Messages to be submitted" in screen:
        return   # 用户又发了消息或还有排队输入：这一轮不动，等下一次空闲
    if item["phase"] == "pending":
        async with bot._project_locks.setdefault(cwd, asyncio.Lock()):
            await bot._inject_tmux(pane, HANDOFF_TEXT.format(n=item["compactions"]))
        item.update(phase="handoff", since=time.time())
        _save(state)
        log.info("[rotation] %s 已发交接消息", session_id[:8])
        return
    if item["phase"] != "handoff":
        return
    from src.agent_sessions import recent_messages
    reply = ((recent_messages(transcript).get("last_assistant_message") or {}).get("text") or "")
    if DONE_WORD not in reply:
        item["attempts"] = item.get("attempts", 0) + 1
        # 交接被打断或跑题：再发一次；两次都不成就放下，等下一次压缩重来
        item["phase"] = "pending" if item["attempts"] < 2 else ""
        if not item["phase"]:
            item["compactions"] = max(0, ROTATE_AFTER - 1)
            item["attempts"] = 0
        _save(state)
        log.info("[rotation] %s 交接未完成（attempts=%s）", session_id[:8], item["attempts"])
        return
    keep_going = "继续" in reply.split(DONE_WORD, 1)[1][:10]
    ok, detail = await bot.clear_session(cwd)
    state.pop(session_id, None)
    _save(state)
    log.info("[rotation] %s 换新会话 ok=%s %s", session_id[:8], ok, detail)
    if not ok:
        return
    if keep_going:
        await asyncio.sleep(4)   # 等新会话就绪（Claude 的 SessionStart 会注入外脑摘要）
        new_pane = await bot._find_pane_for_project(cwd) or pane
        async with bot._project_locks.setdefault(cwd, asyncio.Lock()):
            await bot._inject_tmux(new_pane, CONTINUE_TEXT)
    if topic_id and bot.config.CHAT_ID:
        try:
            from src.api_server import _bot
            await _bot.send_message(
                chat_id=bot.config.CHAT_ID, message_thread_id=topic_id, parse_mode=None, disable_notification=True,
                text=f"🔄 上下文已压缩 {item['compactions']} 次，已自动交接到 recon/PROGRESS.md 并换了新会话"
                     + ("，正在按外脑里的下一步继续。" if keep_going else "。"))
        except Exception:
            log.warning("[rotation] 通知失败", exc_info=True)
