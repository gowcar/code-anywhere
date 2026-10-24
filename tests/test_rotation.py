"""自动换新会话：压缩满 3 次 → 一轮结束时交接 → 交接完成后换新会话（事件驱动）。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src import bot, rotation


class RotationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.object(rotation, "STATE_FILE", Path(self.tmp.name) / "state.json"),
                  patch.object(rotation, "ROTATE_AFTER", 3),
                  patch.object(rotation.asyncio, "sleep", AsyncMock())):
            p.start()
            self.addCleanup(p.stop)
        self.inject = AsyncMock()
        self.clear = AsyncMock(return_value=(True, "/clear"))
        for p in (patch.object(bot, "_inject_tmux", self.inject), patch.object(bot, "clear_session", self.clear),
                  patch.object(bot, "_capture_pane", AsyncMock(return_value="❯ ")),
                  patch.object(bot, "_find_pane_for_project", AsyncMock(return_value="%9"))):
            p.start()
            self.addCleanup(p.stop)

    def reply(self, text):
        return patch("src.agent_sessions.recent_messages", return_value={"last_assistant_message": {"text": text}})

    async def turn_end(self):
        await rotation.on_turn_end("sid", "/w", None, "%9", "/t.jsonl")

    def test_signals(self):
        self.assertTrue(rotation.is_compaction({"type": "compacted"}))
        self.assertTrue(rotation.is_compaction({"type": "system", "subtype": "compact_boundary"}))
        self.assertTrue(rotation.is_turn_end({"type": "system", "subtype": "turn_duration"}, "claude"))
        self.assertFalse(rotation.is_turn_end({"type": "system", "subtype": "turn_duration"}, "codex"))

    async def test_rotates_after_three_compactions_with_handoff(self):
        rotation.on_compaction("sid")
        rotation.on_compaction("sid")
        await self.turn_end()
        self.inject.assert_not_awaited()                          # 才 2 次，不动
        rotation.on_compaction("sid")
        await self.turn_end()
        self.assertIn(rotation.HANDOFF_MARK, self.inject.await_args.args[1])   # 第 3 次后的一轮结束：发交接
        with self.reply("已写进 PROGRESS。\n交接完成：继续"):
            await self.turn_end()
        self.clear.assert_awaited_once_with("/w")
        self.assertEqual(self.inject.await_args.args[1], rotation.CONTINUE_TEXT)   # 有待办：新会话接着做
        self.assertEqual(rotation._load(), {})

    async def test_no_pending_work_means_no_continue_message(self):
        for _ in range(3):
            rotation.on_compaction("sid")
        await self.turn_end()
        with self.reply("交接完成：无待办"):
            await self.turn_end()
        self.clear.assert_awaited_once()
        self.assertEqual(self.inject.await_count, 1)               # 只有交接那一条

    async def test_handoff_not_done_retries_once_then_waits_for_next_compaction(self):
        for _ in range(3):
            rotation.on_compaction("sid")
        await self.turn_end()
        with self.reply("我先看看这个 bug"):
            await self.turn_end()                                  # 跑题：退回待换
            await self.turn_end()                                  # 再发一次交接
            await self.turn_end()                                  # 还是没交接：放下
        self.clear.assert_not_awaited()
        self.assertEqual(self.inject.await_count, 2)
        self.assertEqual(rotation._load()["sid"]["phase"], "")
        rotation.on_compaction("sid")                              # 下一次压缩重新进入待换
        self.assertEqual(rotation._load()["sid"]["phase"], "pending")

    async def test_busy_or_queued_input_waits(self):
        for _ in range(3):
            rotation.on_compaction("sid")
        with patch.object(bot, "_capture_pane", AsyncMock(return_value="✶ Thinking… (3s)\nPress up to edit queued messages")):
            await self.turn_end()
        self.inject.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
