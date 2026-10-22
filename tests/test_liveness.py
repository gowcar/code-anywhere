"""运行状态可见性：状态卡、卡住告警、话题名状态符号。假 bot + 可控时钟，不连 TG。"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src import liveness


def snap(state="running", tokens="↓ 1k", fp="a"):
    return liveness.Snapshot(state=state, tokens=tokens, fingerprint=fp)


class FakeBot:
    def __init__(self):
        self.next_id = 100
        self.sent, self.edited, self.deleted, self.renamed = [], [], [], []

    async def send_message(self, **kw):
        await asyncio.sleep(0)  # 像真实网络请求一样让出执行权，才能暴露并发问题
        self.next_id += 1
        self.sent.append(kw)
        return SimpleNamespace(message_id=self.next_id)

    async def edit_message_text(self, **kw):
        self.edited.append(kw)

    async def delete_message(self, **kw):
        self.deleted.append(kw["message_id"])

    async def edit_forum_topic(self, **kw):
        self.renamed.append(kw["name"])


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for p in (patch.object(liveness, "NAMES_FILE", Path(self.temp.name) / "names.json"),
                  patch.object(liveness, "CARDS_FILE", Path(self.temp.name) / "cards.json"),
                  patch.object(liveness, "_names", None),
                  patch.dict(liveness._topics, {}, clear=True),
                  patch.dict(liveness._activity, {}, clear=True),
                  patch.dict(liveness._last_tool, {}, clear=True),
                  patch.object(liveness, "_monitor", None),
                  patch.dict(liveness._user_msgs, {}, clear=True),
                  patch("src.projects.get_path", return_value="/w/demo")):
            p.start(); self.addCleanup(p.stop)
        self.bot = FakeBot()
        self.mon = liveness.Monitor(self.bot, -100)

        liveness.remember_topic_name(7, "demo")

    def at(self, t, s, size=0):
        asyncio.run(self.mon.observe(7, s, size, 1000 + t))

    def test_card_appears_after_threshold_moves_to_bottom_and_is_deleted_on_finish(self):
        self.at(0, snap(tokens="↓ 1k"))
        self.at(10, snap(tokens="↓ 2k"))
        self.assertEqual(self.bot.sent, [])                       # 不满 15 秒不打扰
        self.at(20, snap(tokens="↓ 3k"))
        self.assertEqual(len(self.bot.sent), 1)
        self.assertTrue(self.bot.sent[0]["disable_notification"])  # 静默
        self.assertIn("运行中", self.bot.sent[0]["text"])
        card_buttons = [b.callback_data for b in self.bot.sent[0]["reply_markup"].inline_keyboard[0]]
        self.assertEqual(card_buttons, ["lv:abort:7", "lv:shot:7"])  # 卡片自带中断按钮
        liveness.note_activity(7)                                  # 回复出现在卡片后面
        self.at(50, snap(tokens="↓ 4k"))
        self.assertEqual(self.bot.deleted, [])                     # 距上次挪动不足 45 秒，原地更新
        self.at(70, snap(tokens="↓ 5k"))
        self.assertEqual(self.bot.deleted, [101])
        self.assertEqual(len(self.bot.sent), 2)                    # 在底部重发
        self.at(90, snap(state="idle", tokens=""))
        self.assertEqual(self.bot.deleted, [101, 102])             # 正常结束删卡

    def test_tool_event_starts_card_immediately_and_scrolls_recent_actions(self):
        async def scenario():
            with patch.object(liveness.time, "time", return_value=2000.0):
                for i in range(6):
                    await liveness.on_tool(7, f"⚡ <code>step {i}</code>")
            return liveness._topics[7]
        topic = asyncio.run(scenario())
        self.assertEqual(len(self.bot.sent), 1)                    # 第一条工具调用立刻出卡片
        self.assertEqual(topic.state, "running")
        self.assertEqual([d for d, _ in liveness._last_tool[7]], [f"⚡ step {i}" for i in range(2, 6)])
        self.assertIn("⚡ <code>step 0</code>", self.bot.sent[0]["text"])
        self.assertEqual(self.bot.sent[0]["parse_mode"], "HTML")      # 路径不会被识别成 bot 命令链接
        text = liveness.card_text(topic, 2001.0)
        self.assertTrue(text.splitlines()[0][0] in liveness.SPINNER)
        self.assertEqual(text.count("⚡ <code>step"), 4)            # 只滚动显示最近 4 条

    def test_concurrent_updates_send_only_one_card(self):
        async def scenario():
            topic = liveness._topics.setdefault(7, liveness.Topic(7))
            topic.turn_started, topic.state = 1000.0, "running"
            await asyncio.gather(self.mon.update_card(topic, 1020.0, force=True),
                                 self.mon.update_card(topic, 1020.0, force=True))
        asyncio.run(scenario())
        self.assertEqual(len(self.bot.sent), 1)

    def test_race_reproduces_without_lock(self):
        async def scenario():
            topic = liveness._topics.setdefault(7, liveness.Topic(7))
            topic.turn_started, topic.state = 1000.0, "running"
            await asyncio.gather(self.mon._update_card(topic, 1020.0, True),
                                 self.mon._update_card(topic, 1020.0, True))
        asyncio.run(scenario())
        self.assertEqual(len(self.bot.sent), 2)                    # 不加锁就会各发一张

    def test_stall_alert_once_with_buttons_and_recovery(self):
        self.at(0, snap(tokens="↓ 1k"))
        for t in range(10, 300, 10):
            self.at(t, snap(tokens="↓ 1k"))                        # 计时在走但 token/屏幕不动
        self.assertEqual(liveness._topics[7].state, "running")
        self.at(310, snap(tokens="↓ 1k"))
        self.assertEqual(liveness._topics[7].state, "stalled")
        alerts = [m for m in self.bot.sent if "疑似卡住" in m["text"] and not m["disable_notification"]]
        self.assertEqual(len(alerts), 1)
        self.assertFalse(alerts[0]["disable_notification"])       # 告警要提醒
        buttons = [b.callback_data for b in alerts[0]["reply_markup"].inline_keyboard[0]]
        self.assertEqual(buttons, ["lv:abort:7", "lv:shot:7"])
        self.at(320, snap(tokens="↓ 1k"))
        self.assertEqual(len([m for m in self.bot.sent if "疑似卡住" in m["text"]]), 1)  # 只告警一次
        self.at(330, snap(tokens="↓ 9k"))                          # 有进展了
        self.assertTrue(any("已恢复" in e["text"] for e in self.bot.edited))

    def test_title_follows_state_with_debounce_and_gap(self):
        self.at(0, snap())
        self.at(10, snap(tokens="↓ 2k"))
        self.assertEqual(self.bot.renamed, [])                     # 稳定不足 20 秒
        self.at(20, snap(tokens="↓ 3k"))
        self.assertEqual(self.bot.renamed, ["🟢 demo"])
        self.at(30, snap(state="idle", tokens=""))
        self.at(55, snap(state="idle", tokens=""))
        self.assertEqual(self.bot.renamed, ["🟢 demo"])            # 距上次改名不足 60 秒
        self.at(85, snap(state="idle", tokens=""))
        self.assertEqual(self.bot.renamed, ["🟢 demo", "demo"])

    def test_title_not_starved_by_card_budget(self):
        for _ in range(liveness.BUDGET):
            self.mon._spend()                                       # 卡片把额度用完了
        self.at(0, snap())
        self.at(25, snap(tokens="↓ 2k"))
        self.assertEqual(self.bot.renamed, ["🟢 demo"])

    def test_leftover_cards_deleted_on_start_and_only_one_card_per_topic(self):
        liveness.CARDS_FILE.write_text('{"7": 55, "9": 66}')       # 上次运行留下的卡
        asyncio.run(self._run_one_tick())
        self.assertEqual(sorted(self.bot.deleted), [55, 66])
        self.at(0, snap()); self.at(20, snap(tokens="↓ 2k")); self.at(40, snap(tokens="↓ 3k"))
        self.assertEqual(len(self.bot.sent), 1)                    # 一直只有一张卡，原地更新
        self.assertEqual(liveness.CARDS_FILE.read_text(), '{"7": 101}')

    def test_unknown_topic_name_is_never_touched(self):
        asyncio.run(self.mon.observe(8, snap(), 0, 1000))
        asyncio.run(self.mon.observe(8, snap(tokens="↓ 9k"), 0, 1100))
        self.assertEqual(self.bot.renamed, [])

    def test_restart_restores_leftover_prefix(self):
        liveness._load_names()["7"]["applied"] = "🟢 demo"        # 重启前挂着 🟢
        asyncio.run(self._run_one_tick())
        self.at(30, liveness.Snapshot())                           # 首次采样：状态判为空闲
        self.assertEqual(self.bot.renamed, [])
        self.at(60, liveness.Snapshot())                           # 稳定 20 秒后恢复原名
        self.assertEqual(self.bot.renamed, ["demo"])

    async def _run_one_tick(self):
        with patch.object(liveness.Monitor, "tick", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await self.mon.run()


class PrivateModeTests(unittest.TestCase):
    """私聊 Thread 模式：工具调用照旧单独发、几秒后消散；长时间无动作冒心跳；不发状态卡。"""
    def test_tools_go_ephemeral_and_heartbeat_only_when_quiet(self):
        bot = FakeBot()
        sent = []
        async def fake_ephemeral(_bot, chat_id, text, ttl=5.0, message_thread_id=None, parse_mode="HTML"):
            sent.append((message_thread_id, text))
        async def scenario():
            with patch.dict(liveness._topics, {}, clear=True), patch.dict(liveness._last_tool, {}, clear=True), \
                 patch.object(liveness, "_monitor", None), patch("src.renderer.send_ephemeral", fake_ephemeral):
                mon = liveness.Monitor(bot, 42)                    # 正数 = 私聊
                handled = await liveness.on_tool(7, "⚡ <code>ls</code>")
                topic = liveness._topics[7]
                start = topic.turn_started
                await mon.observe(7, snap(tokens="↓ 1k"), 0, start + 30)   # 30 秒：不打扰
                quiet = list(sent)
                await mon.observe(7, snap(tokens="↓ 2k"), 0, start + 70)   # 70 秒无动作：心跳
                return handled, quiet
        handled, quiet = asyncio.run(scenario())
        self.assertFalse(handled)                                   # 调用方照旧发消散消息
        self.assertEqual(quiet, [])
        self.assertEqual(len(sent), 1)
        self.assertIn("仍在运行", sent[0][1])
        self.assertEqual(bot.sent, [])                              # 没有状态卡

    def test_live_elapsed_ticks_with_own_clock(self):
        topic = liveness.Topic(7, state="running", turn_started=1000.0)
        topic.snapshot = liveness.Snapshot(state="running", elapsed="5s")
        self.assertIn("1分05秒", liveness.card_text(topic, 1065.0, live=True))
        self.assertIn("5s", liveness.card_text(topic, 1065.0))

    def test_stopped_draft_maps_to_topic(self):
        self.assertEqual(liveness.topic_for_stopped_draft({"message_thread_id": 4705, "draft_id": 4705}), 4705)
        self.assertEqual(liveness.topic_for_stopped_draft({"draft_id": liveness.draft_id(4704, 37)}), 4704)


class ReactionTests(unittest.TestCase):
    def test_done_marks_all_pending_messages_and_abort_does_not(self):
        bot = FakeBot(); bot.reactions = []
        async def set_message_reaction(**kw):
            bot.reactions.append((kw["message_id"], kw["reaction"][0].emoji))
        bot.set_message_reaction = set_message_reaction
        async def scenario():
            with patch.dict(liveness._user_msgs, {}, clear=True), patch.object(liveness, "_monitor", None):
                liveness.Monitor(bot, -100)
                liveness.note_user_message(7, 11); liveness.note_user_message(7, 12)
                liveness.turn_finished(7)
                liveness.note_user_message(8, 21)
                with patch.dict(liveness._topics, {8: liveness.Topic(8)}):
                    liveness.note_abort(8)
                liveness.turn_finished(8)
                await asyncio.sleep(0.05)
        asyncio.run(scenario())
        self.assertEqual(sorted(bot.reactions), [(11, "👌"), (12, "👌")])


class FloodTests(unittest.TestCase):
    def test_formal_reply_waits_and_resends_on_flood(self):
        from telegram.error import RetryAfter
        from src import api_server
        send = AsyncMock(side_effect=[RetryAfter(3), SimpleNamespace(message_id=1)])
        with patch.object(api_server, "_bot", SimpleNamespace(send_message=send)), \
             patch.object(api_server.asyncio, "sleep", AsyncMock()) as sleep, \
             patch.object(liveness, "note_flood") as flood:
            result = asyncio.run(api_server._tg_send(chat_id=1, text="✦ hi"))
        self.assertEqual(result.message_id, 1)
        self.assertEqual(send.await_count, 2)                      # 等待后重发，没丢
        sleep.assert_awaited_once_with(4)
        flood.assert_called_once_with(3)

    def test_monitor_budget_stops_calls(self):
        self.addCleanup(setattr, liveness, "_monitor", liveness._monitor)
        mon = liveness.Monitor(FakeBot(), -100)
        for _ in range(liveness.BUDGET):
            mon._spend()
        self.assertTrue(mon._paused())
        mon._calls.clear()
        self.assertFalse(mon._paused())
        liveness.note_flood(30)
        self.assertTrue(mon._paused())


class TextTests(unittest.TestCase):
    def test_base_title_strips_status_prefix(self):
        self.assertEqual(liveness.base_title("🟢 digital-buddy-ios"), "digital-buddy-ios")
        self.assertEqual(liveness.titled("demo", "idle"), "demo")
        self.assertEqual(liveness.titled("demo", "stalled"), "🔴 demo")

    def test_tool_note_is_plain_text(self):
        with patch.dict(liveness._last_tool, {}, clear=True):
            liveness.note_tool(3, "⚡ <code>git status &amp;&amp; ls</code>")
            self.assertEqual(liveness._last_tool[3][-1][0], "⚡ git status && ls")


if __name__ == "__main__":
    unittest.main()
