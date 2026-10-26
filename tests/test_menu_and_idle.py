"""终端停在选择题 / 输入框里有灰色建议时的判断（2026-10-25 flower-ios、issue-desk）。"""
import unittest
from unittest.mock import AsyncMock, patch

from src import agent_sessions, api_server, bot, liveness

MENU = """⏺ 接下来需要和你确认几个关键决策点。
────────────────────────────────────────
←  ☒ 登录方式  ☒ 网页版  ☐ 离线  ✔ Submit  →
没有网络的时候能玩吗？
❯ 1. 能玩，联网后同步 (Recommended)
  2. 必须联网
  3. Type something.
────────────────────────────────────────
  4. Chat about this
Enter to select · Tab/Arrow keys to navigate · Esc to cancel
"""


class IdleDetectionTests(unittest.TestCase):
    def test_dim_suggestion_counts_as_empty_prompt(self):
        screen = "✻ Baked for 4s\n\x1b[39m❯ \x1b[2m好，我先去 Thread 配置 issue 流程\x1b[0m\n  4% (36K/1000K)"
        self.assertEqual(agent_sessions.screen_state(screen)[0], 'waiting_input')

    def test_real_draft_is_not_idle(self):
        screen = "✻ Baked for 4s\n\x1b[39m❯ 你来做\x1b[0m\n  4% (36K/1000K)"
        self.assertEqual(agent_sessions.screen_state(screen)[0], 'unknown')

    def test_plain_screens_unchanged(self):
        self.assertEqual(agent_sessions.screen_state("❯ \n")[0], 'waiting_input')
        self.assertEqual(agent_sessions.screen_state("› Ask Codex to do anything\n")[0], 'waiting_input')


class MenuTests(unittest.IsolatedAsyncioTestCase):
    def test_menu_open(self):
        self.assertTrue(bot.menu_open(MENU))
        self.assertFalse(bot.menu_open("❯ \n  4% (36K/1000K) | Opus"))

    async def test_screen_menu_sent_when_no_button_menu(self):
        mon = liveness.Monitor.__new__(liveness.Monitor)
        mon._send = AsyncMock()
        with patch.dict(api_server.terminal_sessions, {'s': {'topic_id': 7, 'tmux_pane': '%5'}}, clear=True), \
             patch.dict(api_server._pending_menus, {}, clear=True), \
             patch.object(bot, '_capture_pane', AsyncMock(return_value=MENU)):
            await mon.offer_screen_menu(7, delay=0)
        text = mon._send.await_args.args[1]
        self.assertIn('没有网络的时候能玩吗', text)
        self.assertIn('回复数字', text)

    async def test_no_duplicate_when_buttons_already_offered(self):
        mon = liveness.Monitor.__new__(liveness.Monitor)
        mon._send = AsyncMock()
        with patch.dict(api_server.terminal_sessions, {'s': {'topic_id': 7, 'tmux_pane': '%5'}}, clear=True), \
             patch.dict(api_server._pending_menus, {'t': {'topic_id': 7}}, clear=True), \
             patch.object(bot, '_capture_pane', AsyncMock(return_value=MENU)):
            await mon.offer_screen_menu(7, delay=0)
        mon._send.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
