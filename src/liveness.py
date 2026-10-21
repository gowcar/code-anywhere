"""会话运行状态可见性：话题名状态符号、运行中状态卡、疑似卡住告警。

每 POLL 秒读一次各项目终端屏幕与会话日志：
- 判定 running / waiting（等审批或回答）/ retrying（API 重试）/ idle；
- “还活着”只认真实进展：token 数、会话日志大小、屏幕内容（去掉计时行）有变化。
  计时在模型接口卡住时也会走，不能当作进展；
- running 超过 STALL 秒无进展判为 stalled，告警一次，恢复后改成“已恢复”。
"""
import asyncio
import hashlib
import html
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from src.config import PROJECT_ROOT

log = logging.getLogger("code-anywhere.liveness")

POLL = 10            # 采样间隔（秒）
CARD_AFTER = 15      # 一轮超过这么久才出状态卡，快速回答不打扰
CARD_EVERY = 15      # 没有工具调用时（如长时间思考）状态卡的刷新间隔
MOVE_EVERY = 45      # 状态卡挪到底部（删旧发新）的最小间隔，太频繁会触发 TG 限流
TOOL_LINES = 4       # 卡片里滚动显示的最近动作条数
BUDGET = 8           # 状态卡/改名每分钟最多占用的 TG 调用次数（群组共用约 20 条/分钟，优先留给正式回复）
SPINNER = "◐◓◑◒"
STALL = 300          # 运行中无进展超过这么久判为疑似卡住
TITLE_STABLE = 20    # 状态稳定这么久才改话题名
TITLE_GAP = 60       # 同一话题两次改名的最小间隔
NAMES_FILE = PROJECT_ROOT / ".topic-names.json"

EMOJI = {"running": "🟢", "waiting": "🟡", "retrying": "🔴", "stalled": "🔴", "idle": ""}
_KNOWN_PREFIX = re.compile(r"^(?:🟢|🟡|🔴|🟠|⚪)\s*")


# ---------------------------------------------------------------- 屏幕解析（纯函数）

@dataclass
class Snapshot:
    state: str = "idle"          # running / waiting / retrying / idle
    elapsed: str = ""            # CLI 自报的本轮用时，如 "3m 12s"
    tokens: str = ""             # 如 "↓ 51.3k"
    context: str = ""            # 如 "58%" 已用 / "97% 剩余"
    detail: str = ""             # retrying 等状态的原因
    fingerprint: str = ""        # 去掉计时/状态栏后的屏幕摘要，用于判断进展


_CLAUDE_SPINNER = re.compile(r"^\s*\S\s+(\S+…)\s*\(([^)]*)\)", re.MULTILINE)
_CLAUDE_CONTEXT = re.compile(r"(\d+)% \(\d+K/\d+K\)")
_CODEX_WORKING = re.compile(r"Working \(([^•)]+?)\s*•")
_CODEX_CONTEXT = re.compile(r"(\d+)% context left")
_TOKENS = re.compile(r"([↓↑])\s*([\d.]+k?)\s*tokens")
_RETRY = re.compile(r"(?:Retrying in \d+\s*\w*.*?|Reconnecting.*?|retrying.*?attempt \d+.*?)$", re.IGNORECASE | re.MULTILINE)
_WAITING = ("Do you want to proceed", "Do you want to make this edit", "Enter to select", "Esc to cancel")
_VOLATILE = re.compile(r"\d+(?:\.\d+)?k?\s*tokens|\(\d+[hms][^)]*\)|\d+%|Working \([^)]*\)|\d+m \d+s|\d+s\b")


_NOISE = ("Tip:", "context left", "bypass permissions", "-- INSERT --", "esc to interrupt", "shift+tab")


def _fingerprint(screen: str) -> str:
    """屏幕的“实质内容”摘要。运行状态行的动词与动画符号、轮换的 Tip、状态栏会自己变，
    算进来就永远判不出卡住，所以整行排除；其余行再去掉计时、百分比等数字。"""
    lines = []
    for line in screen.splitlines():
        if (not line.strip() or _CLAUDE_SPINNER.match(line) or _CODEX_WORKING.search(line)
                or any(noise in line for noise in _NOISE) or _CLAUDE_CONTEXT.search(line)):
            continue
        lines.append(_VOLATILE.sub("", line))
    return hashlib.sha1("\n".join(lines).encode()).hexdigest()[:16]


def parse_screen(screen: str, backend: str) -> Snapshot:
    snap = Snapshot(fingerprint=_fingerprint(screen))
    if retry := _RETRY.search(screen):
        snap.state, snap.detail = "retrying", retry.group(0).strip()[:120]
    if backend == "codex":
        if m := _CODEX_WORKING.search(screen):
            snap.state = snap.state if snap.state == "retrying" else "running"
            snap.elapsed = m.group(1).strip()
        if m := _CODEX_CONTEXT.search(screen):
            snap.context = f"剩余 {m.group(1)}%"
    else:
        if m := _CLAUDE_SPINNER.search(screen):
            snap.state = snap.state if snap.state == "retrying" else "running"
            inside = m.group(2)
            if t := re.search(r"(\d+h\s*)?(\d+m\s*)?\d+s", inside):
                snap.elapsed = t.group(0).strip()
            if t := _TOKENS.search(inside):
                snap.tokens = f"{t.group(1)} {t.group(2)}"
        if m := _CLAUDE_CONTEXT.search(screen):
            snap.context = f"已用 {m.group(1)}%"
    tail = "\n".join(screen.splitlines()[-25:])
    if snap.state == "idle" and any(marker in tail for marker in _WAITING):
        snap.state = "waiting"
    return snap


def base_title(name: str) -> str:
    return _KNOWN_PREFIX.sub("", name).strip()


def titled(base: str, state: str) -> str:
    emoji = EMOJI.get(state, "")
    return f"{emoji} {base}" if emoji else base


def human_elapsed(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}秒"
    if seconds < 3600:
        return f"{seconds // 60}分{seconds % 60:02d}秒"
    return f"{seconds // 3600}小时{seconds % 3600 // 60:02d}分"


# ---------------------------------------------------------------- 每个话题的运行状态

@dataclass
class Topic:
    topic_id: int
    state: str = "idle"
    state_since: float = field(default_factory=time.time)
    turn_started: float | None = None
    last_progress: float = field(default_factory=time.time)
    progress_key: tuple = ()
    snapshot: Snapshot = field(default_factory=Snapshot)
    card_id: int | None = None
    card_activity: int = 0
    card_updated: float = 0.0
    card_posted: float = 0.0
    frame: int = 0                       # 转圈动画帧：每次刷新换一帧，停了就说明没在刷新
    alert_id: int | None = None
    title_applied_at: float = 0.0


_topics: dict[int, Topic] = {}
_activity: dict[int, int] = {}          # 话题里“正式消息”计数：卡片之后出现新消息就挪到底部
_last_tool: dict[int, deque] = {}       # 每个话题最近几条工具动作 (描述, 时间)
_monitor: "Monitor | None" = None
_names: dict[str, dict] | None = None


def note_activity(topic_id: int) -> None:
    _activity[topic_id] = _activity.get(topic_id, 0) + 1


def note_tool(topic_id: int, description: str) -> None:
    plain = html.unescape(re.sub(r"<[^>]+>", "", description)).replace("\n", " ").strip()
    _last_tool.setdefault(topic_id, deque(maxlen=TOOL_LINES)).append((plain[:70], time.time()))


async def on_tool(topic_id: int, description: str) -> bool:
    """工具动作进状态卡滚动显示，替代一条条单独发、几秒后删的消息。返回 False 时调用方照旧单独发。"""
    note_tool(topic_id, description)
    if _monitor is None:
        return False
    await _monitor.tool_event(topic_id)
    return True


def _load_names() -> dict:
    global _names
    if _names is None:
        try:
            _names = json.loads(NAMES_FILE.read_text())
        except (OSError, ValueError):
            _names = {}
    return _names


def _save_names() -> None:
    tmp = NAMES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_names, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(NAMES_FILE)


def remember_topic_name(topic_id: int, name: str) -> None:
    """建话题时登记原始名称；改状态时只改前缀，不丢自定义的名字。"""
    names = _load_names()
    names[str(topic_id)] = {"base": base_title(name), "applied": name}
    _save_names()


def card_text(topic: Topic, now: float) -> str:
    snap = topic.snapshot
    took = snap.elapsed or human_elapsed(now - (topic.turn_started or now))
    spinner = SPINNER[topic.frame % len(SPINNER)]
    head = {"running": f"{spinner} 运行中", "retrying": "🔁 接口重试中", "stalled": "⚠️ 疑似卡住",
            "waiting": "🟡 等你审批或回答"}.get(topic.state, f"{spinner} 运行中")
    lines = [f"{head} · {took}"]
    facts = [x for x in (f"{snap.tokens} tokens" if snap.tokens else "", f"上下文{snap.context}" if snap.context else "") if x]
    if facts:
        lines.append(" · ".join(facts))
    if topic.state in ("retrying", "stalled"):
        quiet = human_elapsed(now - topic.last_progress)
        lines.append(f"{quiet}无进展" + (f"：{snap.detail}" if snap.detail else ""))
    if tools := _last_tool.get(topic.topic_id):
        lines.append("")
        lines += [f"{desc}  · {human_elapsed(now - at)}前" for desc, at in tools]
    return "\n".join(lines)


# ---------------------------------------------------------------- 主循环

class Monitor:
    def __init__(self, bot, chat_id: int):
        global _monitor
        self.bot, self.chat_id = bot, chat_id
        self.paused_until = 0.0  # TG 限流时暂停所有状态卡/改名调用，把额度让给正式回复
        self._flush: dict[int, asyncio.Task] = {}
        self._calls: deque = deque()
        _monitor = self

    def _min_gap(self) -> float:
        """同一张卡两次编辑的最小间隔：活跃卡越多越慢，整体控制在 TG 每分钟额度内。"""
        active = sum(1 for t in _topics.values() if t.card_id)
        return max(5.0, 4.0 * active)

    async def tool_event(self, topic_id: int, now: float | None = None):
        now = now or time.time()
        topic = _topics.setdefault(topic_id, Topic(topic_id))
        if topic.turn_started is None:  # 有工具调用就是在干活，不必等满 CARD_AFTER
            topic.turn_started, topic.last_progress = now, now
            topic.state, topic.state_since = "running", now
        topic.last_progress = now
        wait = topic.card_updated + self._min_gap() - now if topic.card_id else 0
        if self._paused():
            wait = max(wait, 10.0)  # 额度用完：动作先攒着，稍后合并进一次编辑
        if wait <= 0:
            await self.update_card(topic, now, force=True)
        elif topic_id not in self._flush or self._flush[topic_id].done():
            async def later():  # 节流期间的动作合并到下一次编辑
                await asyncio.sleep(wait)
                await self.update_card(topic, time.time(), force=True)
            self._flush[topic_id] = asyncio.create_task(later())

    def _paused(self) -> bool:
        """限流暂停中，或本分钟额度已用完。"""
        now = time.time()
        while self._calls and now - self._calls[0] > 60:
            self._calls.popleft()
        return now < self.paused_until or len(self._calls) >= BUDGET

    def _spend(self):
        self._calls.append(time.time())

    def _note_error(self, exc: Exception):
        retry = getattr(exc, "retry_after", None)
        if retry:
            seconds = retry.total_seconds() if hasattr(retry, "total_seconds") else float(retry)
            self.paused_until = max(self.paused_until, time.time() + seconds + 5)
            log.warning("[liveness] TG 限流，暂停 %.0f 秒", seconds + 5)

    async def run(self):
        # 重启前改过名的话题（如还挂着 🟢）先纳入检查，状态稳定后自动恢复
        for tid, entry in _load_names().items():
            if entry.get("applied") != entry.get("base"):
                _topics.setdefault(int(tid), Topic(int(tid), state="unknown"))
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("[liveness] tick failed")
            await asyncio.sleep(POLL)

    async def tick(self, now: float | None = None):
        from src import api_server, backends, projects
        now = now or time.time()
        seen = set()
        for sid, info in list(api_server.terminal_sessions.items()):
            topic_id, pane, cwd = info.get("topic_id"), info.get("tmux_pane"), info.get("cwd")
            if not topic_id or not pane or topic_id in seen or projects.get_path(topic_id) != cwd:
                continue
            seen.add(topic_id)
            backend = info.get("backend", "claude")
            if backends.get_backend(cwd) != backend:
                continue
            try:
                screen = await backends.command("tmux", "capture-pane", "-p", "-t", pane)
            except Exception:
                screen = ""
            snap = parse_screen(screen, backend) if screen else Snapshot()
            if backend == "codex" and (info.get("turn_status") or {}).get("activity") == "retrying":
                snap.state = "retrying"
            if any(p.get("session_id") == sid for p in _pending_permission_events()):
                snap.state = "waiting"
            size = _size(info.get("transcript_path"))
            await self.observe(topic_id, snap, size, now)
        for topic_id in list(_topics):
            if topic_id not in seen:
                await self.observe(topic_id, Snapshot(), 0, now)

    async def observe(self, topic_id: int, snap: Snapshot, size: int, now: float):
        topic = _topics.setdefault(topic_id, Topic(topic_id))
        key = (snap.tokens, size, snap.fingerprint)
        if key != topic.progress_key:
            topic.progress_key, topic.last_progress = key, now
        active = snap.state in ("running", "retrying")
        if active and topic.turn_started is None:
            topic.turn_started, topic.last_progress = now, now
        state = snap.state
        if active and now - topic.last_progress >= STALL:
            state = "stalled"
        topic.snapshot = snap
        if state != topic.state:
            previous = topic.state
            topic.state, topic.state_since = state, now
            await self.on_change(topic, previous, now)
        if active or (state in ("stalled", "waiting") and topic.turn_started):
            await self.update_card(topic, now)
        await self.update_title(topic, now)

    async def on_change(self, topic: Topic, previous: str, now: float):
        log.info("[liveness] topic=%s %s → %s", topic.topic_id, previous, topic.state)
        if topic.state == "stalled" and not topic.alert_id:
            await self.send_alert(topic, now)
        if previous == "stalled" and topic.state != "stalled" and topic.alert_id:
            await self._edit(topic.alert_id, "✅ 已恢复进展" if topic.state != "idle" else "✅ 这一轮已结束")
            topic.alert_id = None
        if topic.state == "idle":
            topic.turn_started = None
            _last_tool.pop(topic.topic_id, None)
            if topic.card_id:  # 正常结束：回答本身已说明完成，卡片删掉
                await self._delete(topic.card_id)
                topic.card_id = None

    async def update_card(self, topic: Topic, now: float, force: bool = False):
        if not topic.turn_started or (not force and now - topic.turn_started < CARD_AFTER):
            return
        if topic.state == "idle" or self._paused():
            return
        # 后面有了新消息、且距上次挪动够久：挪到底部，不受编辑间隔限制
        move = (topic.card_id is not None and _activity.get(topic.topic_id, 0) != topic.card_activity
                and now - topic.card_posted >= MOVE_EVERY)
        if not force and not move and topic.card_id and now - topic.card_updated < max(CARD_EVERY, self._min_gap()) \
                and topic.state == "running":
            return
        topic.frame += 1
        text = card_text(topic, now)
        if move:
            await self._delete(topic.card_id)  # 后面有了新消息：删掉旧卡，在底部重发
            topic.card_id = None
        if topic.card_id:  # 编辑时必须带上按钮，否则 TG 会把按钮清掉
            await self._edit(topic.card_id, text, markup=buttons(topic.topic_id))
        else:
            topic.card_id = await self._send(topic.topic_id, text, silent=True, markup=buttons(topic.topic_id))
            topic.card_posted = now
            topic.card_activity = _activity.get(topic.topic_id, 0)
        topic.card_updated = now

    async def send_alert(self, topic: Topic, now: float):
        snap = topic.snapshot
        reason = snap.detail or "token、会话日志和屏幕内容都没有变化"
        text = (f"⚠️ 疑似卡住：已 {human_elapsed(now - topic.last_progress)} 没有进展\n原因线索：{reason}\n"
                "可以先看终端，确认后再决定是否中断。")
        topic.alert_id = await self._send(topic.topic_id, text, silent=False, markup=buttons(topic.topic_id))

    async def update_title(self, topic: Topic, now: float):
        entry = _load_names().get(str(topic.topic_id))
        if entry is None:  # 不知道原名的话题（可能被手动改过名）一律不碰，免得冲掉自定义名字
            return
        wanted = titled(entry["base"], topic.state)
        if wanted == entry["applied"] or now - topic.state_since < TITLE_STABLE \
                or now - topic.title_applied_at < TITLE_GAP or self._paused():
            return
        try:
            self._spend()
            await self.bot.edit_forum_topic(chat_id=self.chat_id, message_thread_id=topic.topic_id, name=wanted)
            entry["applied"], topic.title_applied_at = wanted, now
            _save_names()
        except Exception as exc:
            log.warning("[liveness] 改话题名失败 topic=%s: %s", topic.topic_id, exc)
            self._note_error(exc)
            topic.title_applied_at = now

    async def _send(self, topic_id: int, text: str, silent: bool, markup=None) -> int | None:
        try:
            self._spend()
            msg = await self.bot.send_message(chat_id=self.chat_id, message_thread_id=topic_id, text=text,
                                              disable_notification=silent, reply_markup=markup)
            return msg.message_id
        except Exception as exc:
            log.warning("[liveness] 发送失败 topic=%s: %s", topic_id, exc)
            self._note_error(exc)
            return None

    async def _edit(self, message_id: int, text: str, markup=None):
        try:
            self._spend()
            await self.bot.edit_message_text(chat_id=self.chat_id, message_id=message_id, text=text,
                                             reply_markup=markup)
        except Exception as exc:
            if "not modified" not in str(exc):
                log.warning("[liveness] 编辑失败 msg=%s: %s", message_id, exc)
            self._note_error(exc)

    async def _delete(self, message_id: int):
        try:
            self._spend()
            await self.bot.delete_message(chat_id=self.chat_id, message_id=message_id)
        except Exception as exc:
            log.warning("[liveness] 删除失败 msg=%s: %s", message_id, exc)
            self._note_error(exc)


def buttons(topic_id: int):
    """状态卡与告警上的按钮：点一下就能看终端或中断，不用敲命令。"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⏹ 中断", callback_data=f"lv:abort:{topic_id}"),
        InlineKeyboardButton("📸 看终端", callback_data=f"lv:shot:{topic_id}"),
    ]])


def note_flood(seconds: float) -> None:
    """其他地方（正式回复）撞上限流时调用：状态卡整体暂停，把额度让出来。"""
    if _monitor:
        _monitor.paused_until = max(_monitor.paused_until, time.time() + seconds + 5)


def note_abort(topic_id: int) -> int | None:
    """/abort 之后：卡片改成“已中断”保留（返回卡片 id 供调用方编辑），状态回到 idle。"""
    topic = _topics.get(topic_id)
    if not topic:
        return None
    card, topic.card_id, topic.turn_started = topic.card_id, None, None
    return card


def _size(path: str | None) -> int:
    try:
        return Path(path).stat().st_size if path else 0
    except OSError:
        return 0


def _pending_permission_events() -> list[dict]:
    from src import api_server
    return [p.get("event") or {} for p in api_server.pending_permissions.values()]


def screen_excerpt(screen: str, lines: int = 40) -> str:
    body = "\n".join(line.rstrip() for line in screen.splitlines() if line.strip())
    tail = "\n".join(body.splitlines()[-lines:])
    return f"<pre>{html.escape(tail[-3500:])}</pre>"
