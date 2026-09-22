"""Protocol/routing regressions; no gateway requests or real Telegram writes."""
import asyncio
from collections import defaultdict, deque
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src import backends, api_server, bot


def item(kind, text, uid='msg-1'):
    return {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {
        'type': kind, 'id': uid, 'content': [{'type': 'Text', 'text': text}]}}}


class PreferencesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pref = patch.object(backends, 'PREFERENCES_FILE', Path(self.temp.name) / 'backends.json')
        self.sessions = patch.object(backends, 'SESSIONS_FILE', Path(self.temp.name) / 'sessions.json')
        self.pref.start(); self.sessions.start()
        self.addCleanup(self.pref.stop); self.addCleanup(self.sessions.stop)

    def test_project_switch_persists_and_other_projects_stay_claude(self):
        self.assertEqual(backends.get_backend('/a'), 'claude')
        backends.set_backend('/a', 'codex')
        self.assertEqual(backends.get_backend('/a'), 'codex')
        self.assertEqual(backends.get_backend('/b'), 'claude')
        backends.set_backend('/a', 'claude')
        self.assertEqual(backends.get_backend('/a'), 'claude')
        with self.assertRaises(ValueError): backends.set_backend('/a', 'shell')

    def test_malformed_preferences_fail_closed(self):
        backends.PREFERENCES_FILE.write_text('{')
        with self.assertRaises(ValueError): backends.get_backend('/a')

    def test_saved_codex_resume_and_sandbox(self):
        backends.remember_codex('/a', 'native-thread')
        with patch('src.state.is_trusted', return_value=False):
            args = backends.codex_launch_args('/a')
        self.assertEqual(args[-2:], ['resume', 'native-thread'])
        self.assertIn('read-only', args)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', args)

    def test_new_and_legacy_public_messages(self):
        result = backends.normalize_codex(item('AgentMessage', '你好'))
        self.assertEqual(result['message']['content'][0]['text'], '你好')
        result = backends.normalize_codex(item('UserMessage', '测试'))
        self.assertEqual(result['origin'], {'kind': 'human'})
        legacy = {'type': 'event_msg', 'payload': {'type': 'agent_message', 'message': 'old'}}
        self.assertEqual(backends.normalize_codex(legacy)['message']['content'][0]['text'], 'old')

    def test_internal_and_duplicate_records_are_not_published(self):
        for ev in [item('Reasoning', 'private'), item('DeveloperMessage', 'private'),
                   {'type': 'world_state', 'payload': {'state': 'private'}},
                   {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant', 'content': 'duplicate'}}]:
            self.assertIsNone(backends.normalize_codex(ev))


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.patches = [patch.object(backends, 'PREFERENCES_FILE', Path(self.temp.name) / 'backends.json'),
                        patch.object(backends, 'SESSIONS_FILE', Path(self.temp.name) / 'sessions.json'),
                        patch.object(api_server, '_bot', SimpleNamespace(send_message=AsyncMock())),
                        patch.object(api_server, 'terminal_sessions', {}),
                        patch.object(api_server, '_watchers', {}),
                        patch.object(api_server, '_event_replay', defaultdict(lambda: deque(maxlen=200)))]
        for p in self.patches: p.start(); self.addCleanup(p.stop)

    async def test_actual_rollout_bound_by_pid_and_cwd(self):
        path = Path(self.temp.name) / 'rollout-test.jsonl'
        path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': 'right', 'cwd': '/a'}}) + '\n')
        with patch.object(backends, 'pane_processes', AsyncMock(return_value=['123'])), \
             patch.object(backends, 'command', AsyncMock(return_value='n' + str(path))):
            self.assertEqual(await backends.codex_transcript('%2', '/a'), ('right', str(path)))
            self.assertIsNone(await backends.codex_transcript('%2', '/wrong'))

    async def test_partial_utf8_line_and_duplicate_message_sent_once(self):
        cwd = self.temp.name; backends.set_backend(cwd, 'codex')
        api_server.terminal_sessions['sid'] = {'cwd': cwd, 'backend': 'codex'}
        path = Path(cwd) / 'rollout.jsonl'; path.write_bytes(b'')
        task = asyncio.create_task(api_server._watch_transcript('sid', str(path), 12, 34, start_pos=0))
        data = (json.dumps(item('AgentMessage', '你好，回归验证'), ensure_ascii=False) + '\n').encode()
        split = data.index('你'.encode()) + 1
        path.write_bytes(data[:split])
        await asyncio.sleep(.6)
        api_server._bot.send_message.assert_not_awaited()
        with path.open('ab') as f: f.write(data[split:] + data)
        await asyncio.sleep(.65)
        task.cancel(); await task
        self.assertEqual(api_server._bot.send_message.await_count, 1)
        self.assertIn('你好', api_server._bot.send_message.call_args.kwargs['text'])
        self.assertEqual(len(api_server._event_replay['sid']), 1)

    async def test_restart_skips_history_and_switch_stops_output(self):
        cwd = self.temp.name; backends.set_backend(cwd, 'codex')
        api_server.terminal_sessions['sid'] = {'cwd': cwd, 'backend': 'codex'}
        path = Path(cwd) / 'rollout.jsonl'
        path.write_text(json.dumps(item('AgentMessage', 'old')) + '\n')
        task = asyncio.create_task(api_server._watch_transcript('sid', str(path), 12, 34))
        await asyncio.sleep(.55)
        api_server._bot.send_message.assert_not_awaited()
        backends.set_backend(cwd, 'claude')
        with path.open('a') as f: f.write(json.dumps(item('AgentMessage', 'inactive')) + '\n')
        await asyncio.wait_for(task, 2)
        api_server._bot.send_message.assert_not_awaited()

    async def test_stale_claude_hook_ignored_after_switch(self):
        cwd = self.temp.name; backends.set_backend(cwd, 'codex')
        request = SimpleNamespace(json=AsyncMock(return_value={'cwd': cwd, 'session_id': 'old', 'tmux_pane': '%3'}))
        with patch.object(api_server.config, 'CHAT_ID', 123), patch.object(api_server.projects, 'get_topic', return_value=1):
            response = await api_server.handle_session_start(request)
        self.assertIn('inactive backend', response.text)
        self.assertFalse(api_server.terminal_sessions)

    async def test_injection_failure_does_not_send_enter(self):
        with patch.object(backends, 'command', AsyncMock(side_effect=RuntimeError('missing pane'))) as command:
            with self.assertRaises(RuntimeError): await bot._inject_tmux('%2', 'hello')
            self.assertEqual(command.await_count, 1)
            self.assertNotIn('Enter', command.call_args.args)

    async def test_model_command_authorization(self):
        update = SimpleNamespace(effective_user=SimpleNamespace(id=77))
        with patch.object(bot, 'auth_check', return_value=False), patch.object(backends, 'set_backend') as set_backend:
            await bot.cmd_model(update, SimpleNamespace(args=['codex']))
            set_backend.assert_not_called()

    async def test_switch_interrupts_old_turn_and_attaches_selected_backend(self):
        cwd = self.temp.name
        message = SimpleNamespace(message_thread_id=12, chat_id=34, reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=SimpleNamespace(id=77), message=message)
        with patch.object(bot, 'auth_check', return_value=True), \
             patch.object(bot, '_update_cwd', return_value=cwd), \
             patch.object(bot, '_find_pane_for_project', AsyncMock(side_effect=['%old', '%new'])), \
             patch.object(backends, 'command', AsyncMock()) as command, \
             patch.object(api_server, 'register_pane_watcher', AsyncMock(return_value=True)) as register:
            await bot.cmd_model(update, SimpleNamespace(args=['gpt']))
        self.assertEqual(backends.get_backend(cwd), 'codex')
        command.assert_awaited_once_with('tmux', 'send-keys', '-t', '%old', 'Escape')
        register.assert_awaited_once_with('%new', cwd)
        self.assertIn('GPT / Codex', message.reply_text.call_args.args[0])

    async def test_web_rejects_inactive_backend(self):
        cwd = self.temp.name; backends.set_backend(cwd, 'codex')
        api_server.terminal_sessions['old'] = {'cwd': cwd, 'backend': 'claude', 'tmux_pane': '%3'}
        request = SimpleNamespace(match_info={'id': 'old'})
        result = await api_server.handle_session_input(request)
        self.assertEqual(result.status, 409)


if __name__ == '__main__': unittest.main()
