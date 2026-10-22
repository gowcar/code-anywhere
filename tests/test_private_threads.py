"""私聊 Thread 模式：私聊里带 message_thread_id 的消息按 Thread 路由，与群组话题一致。"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src import bot


def update(chat_type, thread_id, chat_id=42):
    msg = SimpleNamespace(chat=SimpleNamespace(type=chat_type), chat_id=chat_id, message_thread_id=thread_id)
    return SimpleNamespace(effective_message=msg)


class RoutingTests(unittest.TestCase):
    def test_private_thread_routes_by_thread(self):
        with patch.object(bot.projects, "get_path", side_effect=lambda t: {7: "/w/ios"}.get(t)), \
             patch.object(bot.state, "get_cwd", return_value="/w/legacy"):
            self.assertEqual(bot._update_cwd(update("private", 7)), "/w/ios")
            self.assertEqual(bot._update_cwd(update("private", None)), "/w/legacy")   # 私聊不在 Thread 内
            self.assertEqual(bot._update_cwd(update("supergroup", 7)), "/w/ios")


if __name__ == "__main__":
    unittest.main()
