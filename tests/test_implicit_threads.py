"""私聊 Thread 模式：All Messages 里发消息时 Telegram 自动建的未绑定 Thread —— 提示后自动删除。"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src import bot


def created_update(tid=20890, implicit=True, chat_type="private", user_id=7):
    message = SimpleNamespace(
        forum_topic_created=SimpleNamespace(name="发起动作：读取文件 2 ...", is_name_implicit=implicit),
        chat=SimpleNamespace(type=chat_type), chat_id=7, message_thread_id=tid)
    return SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=user_id))


def make_context():
    tasks = []
    app = SimpleNamespace(create_task=MagicMock(side_effect=lambda coro, update=None: tasks.append(coro)))
    return SimpleNamespace(bot=AsyncMock(), application=app), tasks


class ImplicitThreadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        bot._implicit_threads.clear()
        self.patches = [patch.object(bot, "_private_thread_mode", return_value=True),
                        patch.object(bot, "auth_check", return_value=True),
                        patch.object(bot, "IMPLICIT_THREAD_TTL", 0)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    async def test_hint_then_delete(self):
        context, tasks = make_context()
        with patch.object(bot.projects, "get_path", return_value=None):
            await bot._on_topic_created(created_update(), context)
            self.assertIn(20890, bot._implicit_threads)
            hint = context.bot.send_message.await_args.kwargs
            self.assertEqual(hint["message_thread_id"], 20890)
            self.assertIn("发起动作：读取文件 2", hint["text"])
            self.assertIn("自动删除", hint["text"])
            await tasks[0]
        context.bot.delete_forum_topic.assert_awaited_once_with(chat_id=7, message_thread_id=20890)
        self.assertNotIn(20890, bot._implicit_threads)

    async def test_delete_failure_asks_user_to_delete_manually(self):
        context, tasks = make_context()
        context.bot.delete_forum_topic.side_effect = RuntimeError("TOPIC_ID_INVALID")
        with patch.object(bot.projects, "get_path", return_value=None):
            await bot._on_topic_created(created_update(), context)
            await tasks[0]
        self.assertIn("手动删除", context.bot.send_message.await_args.kwargs["text"])

    async def test_bound_meanwhile_is_kept(self):
        context, tasks = make_context()
        with patch.object(bot.projects, "get_path", side_effect=[None, "/w/demo"]):
            await bot._on_topic_created(created_update(), context)
            await tasks[0]
        context.bot.delete_forum_topic.assert_not_awaited()

    async def test_ignored_cases(self):
        cases = {"bot 自己建的（有名字）": created_update(implicit=False),
                 "群组": created_update(chat_type="supergroup"),
                 "已绑定": created_update(tid=20231)}
        for label, update in cases.items():
            with self.subTest(label), patch.object(bot.projects, "get_path",
                                                   side_effect=lambda t: "/w/yard" if t == 20231 else None):
                context, tasks = make_context()
                await bot._on_topic_created(update, context)
                self.assertEqual(tasks, [])
                context.bot.send_message.assert_not_awaited()
        with patch.object(bot, "auth_check", return_value=False):
            context, tasks = make_context()
            await bot._on_topic_created(created_update(), context)
            self.assertEqual(tasks, [])


if __name__ == "__main__":
    unittest.main()
