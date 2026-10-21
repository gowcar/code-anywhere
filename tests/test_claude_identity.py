"""Claude 会话身份：SessionStart 记在 pane 上的标签优先，jsonl 生成前也能准确接上（移植自 nas）。"""
import asyncio
from collections import defaultdict, deque
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from src import api_server, backends, bot


class ClaudeStartupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cwd = self.temp.name
        self.path = Path(self.cwd) / 'exact-session.jsonl'
        self.info = {'session_id': 'exact-session', 'cwd': self.cwd,
                     'transcript_path': str(self.path), 'model': 'claude-test', 'permission_mode': 'manual'}
        patches = [patch.object(backends, 'PREFERENCES_FILE', Path(self.cwd) / 'backends.json'),
                   patch.object(api_server, '_bot', SimpleNamespace(send_message=AsyncMock())),
                   patch.object(api_server, 'terminal_sessions', {}),
                   patch.object(api_server, '_watchers', {}),
                   patch.object(api_server, '_event_replay', defaultdict(lambda: deque(maxlen=200))),
                   patch.object(api_server.config, 'CHAT_ID', 34),
                   patch.object(api_server.projects, 'get_topic', return_value=12)]
        for p in patches: p.start(); self.addCleanup(p.stop)

    async def asyncTearDown(self):
        tasks = list(api_server._watchers.values())
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def register(self, metadata=True):
        with patch.object(backends, 'pane_processes', AsyncMock(return_value=['123'])), \
             patch.object(backends, 'claude_session', AsyncMock(return_value=self.info if metadata else None)), \
             patch.object(backends, 'remember_claude', AsyncMock()) as remember, \
             patch.object(api_server.projects, 'claude_dir') as scan:
            result = await api_server.register_pane_watcher('%181', self.cwd)
        return result, remember, scan

    async def test_missing_transcript_is_ready_and_first_response_is_delivered(self):
        self.assertFalse(self.path.exists())
        result, _, scan = await self.register()
        self.assertTrue(result)
        scan.assert_not_called()
        # Create the file before the watcher coroutine gets its first execution slot.
        self.path.write_text(json.dumps({'type': 'assistant', 'uuid': 'first',
            'message': {'content': [{'type': 'text', 'text': '首轮恢复成功'}]}}) + '\n')
        await asyncio.sleep(.65)
        api_server._bot.send_message.assert_awaited_once()
        self.assertIn('首轮恢复成功', api_server._bot.send_message.call_args.kwargs['text'])

    async def test_hook_metadata_wins_over_another_recent_project_session(self):
        (Path(self.cwd) / 'wrong-session.jsonl').write_text('{}\n')
        result, _, scan = await self.register()
        self.assertTrue(result)
        self.assertIn('exact-session', api_server.terminal_sessions)
        self.assertEqual(api_server.terminal_sessions['exact-session']['model'], 'claude-test')
        scan.assert_not_called()

    async def test_legacy_in_memory_hook_is_reused_and_persisted(self):
        api_server.terminal_sessions['exact-session'] = {**self.info, 'tmux_pane': '%181', 'topic_id': 12}
        result, remember, scan = await self.register(metadata=False)
        self.assertTrue(result)
        remember.assert_awaited_once()
        self.assertEqual(remember.call_args.args[1]['session_id'], 'exact-session')
        scan.assert_not_called()

    async def test_tag_recovers_after_bot_restart_before_first_input(self):
        self.assertFalse(api_server.terminal_sessions)
        result, _, _ = await self.register()
        self.assertTrue(result)
        self.assertFalse(self.path.exists())
        self.assertEqual(api_server.terminal_sessions['exact-session']['transcript_path'], str(self.path))
        self.assertFalse(api_server._watchers['exact-session'].done())

    async def test_tag_rejects_other_project_and_bad_filename(self):
        with patch.object(backends, 'command', AsyncMock(return_value=json.dumps(self.info))):
            self.assertEqual(await backends.claude_session('%181', self.cwd), self.info)
            self.assertIsNone(await backends.claude_session('%181', '/other'))
        with patch.object(backends, 'command', AsyncMock(return_value=json.dumps({**self.info, 'session_id': 'wrong'}))):
            self.assertIsNone(await backends.claude_session('%181', self.cwd))

    async def test_session_start_does_not_drop_fast_first_reply(self):
        request = SimpleNamespace(json=AsyncMock(return_value={**self.info, 'tmux_pane': '%181'}))
        with patch.object(backends, 'remember_claude', AsyncMock()):
            response = await api_server.handle_session_start(request)
        self.assertEqual(response.status, 200)
        self.path.write_text(json.dumps({'type': 'assistant', 'uuid': 'hook-first',
            'message': {'content': [{'type': 'text', 'text': 'hook 首轮'}]}}) + '\n')
        await asyncio.sleep(.65)
        api_server._bot.send_message.assert_awaited_once()



if __name__ == "__main__":
    unittest.main()
