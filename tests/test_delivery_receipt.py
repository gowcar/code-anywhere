"""投递签收：会话日志里没出现用户原文就提醒（Codex 开 Goal 时曾吞掉用户消息）。"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src import bot


def codex_user(text):
    return json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user",
                       "content": [{"type": "input_text", "text": text}]}}) + "\n"


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "rollout.jsonl"
        self.path.write_text(codex_user("旧消息"))
        self.offset = self.path.stat().st_size
        self.message = SimpleNamespace(message_thread_id=4704, reply_text=AsyncMock())

    def run_confirm(self, text, screen=""):
        with patch.object(bot, "RECEIPT_IDLE_WAIT", 1.5), patch.object(bot, "_capture_pane", AsyncMock(return_value=screen)), \
             patch.object(bot.asyncio, "sleep", AsyncMock()):
            asyncio.run(bot._confirm_delivery(self.message, "%39", "/w", text, str(self.path), self.offset))

    def test_received_message_is_silent(self):
        with self.path.open("a") as f:
            f.write(codex_user("@.attachments/a.jpg\n后面带个💟的图标是啥意思"))
        self.run_confirm("@.attachments/a.jpg\n后面带个💟的图标是啥意思")
        self.message.reply_text.assert_not_awaited()

    def test_swallowed_message_is_reported(self):
        with self.path.open("a") as f:   # Goal 续跑把这一轮的输入换掉了
            f.write(codex_user("<codex_internal_context source=\"goal\">Continue working</codex_internal_context>"))
        self.run_confirm("@.attachments/a.jpg\n后面带个💟的图标是啥意思")
        self.assertIn("没有进入会话", self.message.reply_text.await_args.args[0])

    def test_text_left_in_composer_is_reported(self):
        self.run_confirm("帮我看看日志", screen="› 帮我看看日志")
        self.assertIn("停在终端输入框里", self.message.reply_text.await_args.args[0])

    def test_claude_queued_command_counts_as_received(self):
        with self.path.open("a") as f:   # Claude 一轮进行中收到的消息记为 queued_command 附件
            f.write(json.dumps({"type": "attachment", "attachment": {"type": "queued_command", "prompt": "图标太多了很花"}}) + "\n")
        self.run_confirm("图标太多了很花", screen="❯ 图标太多了很花")
        self.message.reply_text.assert_not_awaited()

    def test_quote_is_skipped_for_snippet(self):
        self.assertEqual(bot.receipt_snippet("[引用消息]\n别人说的\n[/引用消息]\n\n我的问题"), "我的问题")

    def test_snippet(self):
        self.assertEqual(bot.receipt_snippet("@.attachments/x.jpg\n看看这个"), "看看这个")
        self.assertEqual(bot.receipt_snippet("@.attachments/x/y.jpg"), "y.jpg")


if __name__ == "__main__":
    unittest.main()
