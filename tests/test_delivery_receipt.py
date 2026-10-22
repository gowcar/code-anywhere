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

    def test_message_found_in_newer_log_rebinds_instead_of_warning(self):
        # /clear 后 hook 通知没送到：桥接还看旧日志，原文出现在同目录的新日志里
        others = {str(self.path.resolve()): self.offset}
        new_log = Path(self.temp.name) / "new-session.jsonl"
        new_log.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "插队需求你在看么"}}) + "\n")
        rebind = AsyncMock()
        with patch.object(bot, "RECEIPT_IDLE_WAIT", 1.5), patch.object(bot, "_capture_pane", AsyncMock(return_value="❯ 插队需求你在看么")), \
             patch.object(bot.asyncio, "sleep", AsyncMock()), patch.object(bot, "_rebind_claude", rebind):
            asyncio.run(bot._confirm_delivery(self.message, "%24", "/w", "插队需求你在看么", str(self.path), self.offset, others=others))
        self.message.reply_text.assert_not_awaited()
        self.assertEqual(rebind.await_args.args[2], str(new_log.resolve()))

    def test_sent_message_in_history_is_not_called_left_in_composer(self):
        screen = "❯ 帮我看看日志\n\n● 好的，我看一下\n" + "─" * 40 + "\n❯ \n" + "─" * 40 + "\n  bypass permissions on"
        self.run_confirm("帮我看看日志", screen=screen)
        self.assertIn("没有进入会话", self.message.reply_text.await_args.args[0])

    def test_sync_rebinds_when_pane_tag_points_to_new_session(self):
        from src import api_server
        sessions = {"old-sid": {"tmux_pane": "%24", "backend": "claude"}}
        tag = {"session_id": "new-sid", "transcript_path": "/p/new-sid.jsonl"}
        rebind = AsyncMock()
        with patch.dict(api_server.terminal_sessions, sessions, clear=True), \
             patch.object(bot.backends, "claude_session", AsyncMock(return_value=tag)), patch.object(bot, "_rebind_claude", rebind):
            asyncio.run(bot._sync_claude_session("%24", "/w"))
            rebind.assert_awaited_once()
            rebind.reset_mock()
            sessions_same = {"new-sid": {"tmux_pane": "%24", "backend": "claude"}}
            api_server.terminal_sessions.clear(); api_server.terminal_sessions.update(sessions_same)
            asyncio.run(bot._sync_claude_session("%24", "/w"))
            rebind.assert_not_awaited()

    def test_quote_is_skipped_for_snippet(self):
        self.assertEqual(bot.receipt_snippet("[引用消息]\n别人说的\n[/引用消息]\n\n我的问题"), "我的问题")

    def test_snippet(self):
        self.assertEqual(bot.receipt_snippet("@.attachments/x.jpg\n看看这个"), "看看这个")
        self.assertEqual(bot.receipt_snippet("@.attachments/x/y.jpg"), "y.jpg")


if __name__ == "__main__":
    unittest.main()
