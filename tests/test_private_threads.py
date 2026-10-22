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

    def test_quick_keys_handled_by_bridge_not_forwarded(self):
        import asyncio
        from unittest.mock import AsyncMock
        for key, target in ((bot.KEY_SCREEN, "_send_screen"), (bot.KEY_ABORT, "_abort_project")):
            msg = SimpleNamespace(text=key, chat_id=42, message_thread_id=4707, chat=SimpleNamespace(type="private"),
                                  reply_text=AsyncMock())
            upd = SimpleNamespace(effective_user=SimpleNamespace(id=1), message=msg, effective_message=msg)
            with patch.object(bot, "auth_check", return_value=True), \
                 patch.object(bot.projects, "get_path", return_value="/w/desk"), \
                 patch.object(bot, target, AsyncMock(return_value="⏹ 已中断当前回合")) as handler, \
                 patch.object(bot, "_inject_tmux", AsyncMock()) as inject:
                asyncio.run(bot.handle_message(upd, SimpleNamespace(bot=None)))
            handler.assert_awaited_once()
            inject.assert_not_awaited()

    def test_no_reactions_in_private_thread_mode(self):
        import asyncio
        from unittest.mock import AsyncMock
        msg = SimpleNamespace(message_thread_id=4707, chat_id=42, chat=SimpleNamespace(type="private"), text="hi",
                              caption=None, photo=None, document=None, video=None, video_note=None, audio=None,
                              voice=None, sticker=None, reply_to_message=None, reply_text=AsyncMock(), message_id=9,
                              media_group_id=None)
        upd = SimpleNamespace(effective_user=SimpleNamespace(id=1), message=msg, effective_message=msg)
        with patch.object(bot.config, "CHAT_ID", 42), patch.object(bot, "auth_check", return_value=True), \
             patch.object(bot.projects, "get_path", return_value="/w/desk"), \
             patch.object(bot.backends, "get_backend", return_value="codex"), \
             patch.object(bot.backends, "codex_transcript", AsyncMock(return_value=("sid", "/p"))), \
             patch.object(bot, "_find_pane_for_project", AsyncMock(return_value="%1")), \
             patch("src.api_server.register_pane_watcher", AsyncMock(return_value=True)), \
             patch.object(bot, "start_typing"), patch.object(bot, "_inject_tmux", AsyncMock()) as inject, \
             patch("src.liveness.react", AsyncMock()) as react, \
             patch.object(bot, "_transcript_position", AsyncMock(return_value=None)):
            asyncio.run(bot._handle_message(upd, SimpleNamespace(bot=None)))
        inject.assert_awaited_once()
        react.assert_not_called()

    def test_no_typing_indicator_in_private_thread_mode(self):
        with patch.object(bot.config, "CHAT_ID", 42), patch.object(bot.asyncio, "create_task") as create:
            bot.start_typing(None, 42, 4707)
        create.assert_not_called()

    def test_reply_quote_only_when_session_idle(self):
        import asyncio
        from unittest.mock import AsyncMock
        for screen, quoted in (("› Ask Codex to do anything", True), ("◦ Working (16s • esc to interrupt)", False)):
            msg = SimpleNamespace(message_thread_id=4704, chat_id=42, chat=SimpleNamespace(type="private"), text="新问题",
                                  caption=None, photo=None, document=None, video=None, video_note=None, audio=None,
                                  voice=None, sticker=None, reply_to_message=None, reply_text=AsyncMock(), message_id=9)
            upd = SimpleNamespace(effective_user=SimpleNamespace(id=1), message=msg, effective_message=msg)
            with patch.object(bot.config, "CHAT_ID", 42), patch.object(bot, "auth_check", return_value=True), \
                 patch.object(bot.projects, "get_path", return_value="/w/buddy"), \
                 patch.object(bot.backends, "get_backend", return_value="codex"), \
                 patch.object(bot.backends, "codex_transcript", AsyncMock(return_value=("sid", "/p"))), \
                 patch.object(bot, "_find_pane_for_project", AsyncMock(return_value="%1")), \
                 patch("src.api_server.register_pane_watcher", AsyncMock(return_value=True)), \
                 patch.object(bot, "_capture_pane", AsyncMock(return_value=screen)), \
                 patch.object(bot, "_transcript_position", AsyncMock(return_value=None)), \
                 patch.object(bot, "_inject_tmux", AsyncMock()), \
                 patch("src.api_server.mark_tg_reply_target") as mark:
                asyncio.run(bot._handle_message(upd, SimpleNamespace(bot=None)))
            self.assertEqual(mark.called, quoted, screen)

    def test_clear_refuses_busy_and_sends_engine_command(self):
        import asyncio
        from unittest.mock import AsyncMock
        cases = (("claude", "✢ Proofing… (8s · ↓ 1k tokens)", None),
                 ("claude", "────\n❯ \n────", "/clear"),
                 ("codex", "› Ask Codex to do anything", "/new"))
        for backend, screen, expected in cases:
            msg = SimpleNamespace(message_thread_id=4699, chat_id=42, chat=SimpleNamespace(type="private"),
                                  reply_text=AsyncMock())
            upd = SimpleNamespace(effective_user=SimpleNamespace(id=1), message=msg, effective_message=msg)
            with patch.object(bot, "auth_check", return_value=True), \
                 patch.object(bot.projects, "get_path", return_value="/w/ca"), \
                 patch.object(bot.backends, "get_backend", return_value=backend), \
                 patch.object(bot, "_find_pane_for_project", AsyncMock(return_value="%1")), \
                 patch.object(bot, "_capture_pane", AsyncMock(return_value=screen)), \
                 patch.object(bot.backends, "command", AsyncMock()) as command, \
                 patch.object(bot.asyncio, "sleep", AsyncMock()):
                asyncio.run(bot.cmd_clear(upd, SimpleNamespace()))
            typed = [c.args[-1] for c in command.await_args_list if "-l" in c.args]
            if expected:
                self.assertEqual(typed, [expected])
                self.assertIn("已清空", msg.reply_text.await_args.args[0])
            else:
                self.assertEqual(typed, [])
                self.assertIn("正在干活", msg.reply_text.await_args.args[0])

    def test_private_thread_routes_by_thread(self):
        with patch.object(bot.config, "CHAT_ID", -100), patch.object(bot.projects, "get_path", side_effect=lambda t: {7: "/w/ios"}.get(t)), \
             patch.object(bot.state, "get_cwd", return_value="/w/legacy"):
            self.assertEqual(bot._update_cwd(update("private", 7)), "/w/ios")
            self.assertEqual(bot._update_cwd(update("private", None)), "/w/legacy")   # 私聊不在 Thread 内
            self.assertEqual(bot._update_cwd(update("supergroup", 7)), "/w/ios")


if __name__ == "__main__":
    unittest.main()
