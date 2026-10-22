"""私聊 Thread 模式：私聊里带 message_thread_id 的消息按 Thread 路由，与群组话题一致。"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src import bot


def update(chat_type, thread_id, chat_id=42):
    msg = SimpleNamespace(chat=SimpleNamespace(type=chat_type), chat_id=chat_id, message_thread_id=thread_id)
    return SimpleNamespace(effective_message=msg)


class RoutingTests(unittest.TestCase):
    def test_all_messages_never_routes_to_last_project_in_thread_mode(self):
        with patch.object(bot.config, "CHAT_ID", 42), patch.object(bot.state, "get_cwd", return_value="/w/last"):
            self.assertIsNone(bot._update_cwd(update("private", None)))

    def test_all_messages_chat_is_refused(self):
        import asyncio
        from unittest.mock import AsyncMock
        msg = SimpleNamespace(message_thread_id=None, chat_id=42, chat=SimpleNamespace(type="private"), text="hi",
                              caption=None, photo=None, document=None, video=None, video_note=None, audio=None,
                              voice=None, sticker=None, reply_to_message=None, reply_text=AsyncMock())
        upd = SimpleNamespace(effective_user=SimpleNamespace(id=1), message=msg)
        with patch.object(bot.config, "CHAT_ID", 42), patch.object(bot, "auth_check", return_value=True), \
             patch.object(bot, "_inject_tmux", AsyncMock()) as inject:
            asyncio.run(bot._handle_message(upd, SimpleNamespace()))
        inject.assert_not_awaited()
        self.assertIn("All Messages", msg.reply_text.await_args.args[0])

    def test_native_stop_aborts_that_thread(self):
        import asyncio
        from unittest.mock import AsyncMock
        from telegram import Update
        upd = Update.de_json({"update_id": 1, "stopped_message_generation": {
            "chat": {"id": 42, "type": "private"}, "message_thread_id": 4705, "draft_id": 4705}}, None)
        context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(bot.projects, "get_path", return_value="/w/ios"), \
             patch.object(bot, "_abort_project", AsyncMock(return_value="⏹ 已中断当前回合")) as abort:
            asyncio.run(bot._on_stopped_generation(upd, context))
        abort.assert_awaited_once_with(context.bot, "/w/ios", 42, 4705)
        self.assertEqual(context.bot.send_message.await_args.kwargs["message_thread_id"], 4705)

    def test_private_thread_routes_by_thread(self):
        with patch.object(bot.config, "CHAT_ID", -100), patch.object(bot.projects, "get_path", side_effect=lambda t: {7: "/w/ios"}.get(t)), \
             patch.object(bot.state, "get_cwd", return_value="/w/legacy"):
            self.assertEqual(bot._update_cwd(update("private", 7)), "/w/ios")
            self.assertEqual(bot._update_cwd(update("private", None)), "/w/legacy")   # 私聊不在 Thread 内
            self.assertEqual(bot._update_cwd(update("supergroup", 7)), "/w/ios")


if __name__ == "__main__":
    unittest.main()
