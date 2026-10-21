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


class ClaudeLaunchArgsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, mode):
        (self.root / '.claude').mkdir(exist_ok=True)
        (self.root / '.claude' / name).write_text(json.dumps({'permissions': {'defaultMode': mode}}))

    def test_plain_claude_without_project_settings(self):
        self.assertEqual(backends.claude_launch_args(str(self.root)), ['claude'])

    def test_project_bypass_adds_cli_flag(self):
        self.write('settings.json', 'bypassPermissions')
        self.assertEqual(backends.claude_launch_args(str(self.root)),
                         ['claude', '--dangerously-skip-permissions', '--settings', backends.CLAUDE_FULL_SETTINGS])

    def test_local_settings_override_shared_bypass(self):
        self.write('settings.json', 'bypassPermissions')
        self.write('settings.local.json', 'default')
        self.assertEqual(backends.claude_launch_args(str(self.root)), ['claude'])


class TrustPromptTests(unittest.TestCase):
    CLAUDE = (" Quick safety check: Is this a project you created or one you trust?\n"
              " ❯ No, exit\n   Yes, I trust this folder\n Enter to confirm · Esc to cancel")
    CODEX = ("  Trust this folder? Codex can read, edit, and run files here\n"
             "› 1. Trust and continue\n  2. Quit\n  enter continue · esc quit")

    def test_claude_default_is_exit(self):
        self.assertEqual(bot._trust_prompt_state(self.CLAUDE), (True, False))
        moved = self.CLAUDE.replace(' ❯ No, exit\n   Yes', '   No, exit\n ❯ Yes')
        self.assertEqual(bot._trust_prompt_state(moved), (True, True))

    def test_codex_default_is_trust(self):
        self.assertEqual(bot._trust_prompt_state(self.CODEX), (True, True))

    def test_normal_screen_has_no_prompt(self):
        self.assertEqual(bot._trust_prompt_state('❯ \n  -- INSERT --'), (False, False))


class HookSessionTests(unittest.TestCase):
    def test_prefers_hook_registered_claude_session_for_pane(self):
        sessions = {'old': {'tmux_pane': '%1', 'backend': 'codex'},
                    'new': {'tmux_pane': '%1'}, 'other': {'tmux_pane': '%2'}}
        with patch.dict(api_server.terminal_sessions, sessions, clear=True):
            self.assertEqual(asyncio.run(bot._hook_session_for_pane('%1', wait=0.1)), 'new')
            self.assertIsNone(asyncio.run(bot._hook_session_for_pane('%9', wait=0.1)))


class ClaudeLateWatcherTests(unittest.TestCase):
    def test_injection_offsets_pick_grown_log_not_newer_old_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            target, other = folder / 'target.jsonl', folder / 'other.jsonl'
            target.write_text('{}\n'); other.write_text('{}\n')
            with patch('src.projects.claude_dir', return_value=folder):
                offsets = backends.claude_transcript_offsets('/p')
            target.write_text('{}\n{"new": 1}\n')
            os_time = target.stat().st_mtime - 100
            import os; os.utime(target, (os_time, os_time))  # old session is "newer" by mtime
            started = {}

            async def fake_watch(sid, path, *args, start_pos=None):
                started[sid] = start_pos

            with patch('src.projects.claude_dir', return_value=folder), \
                 patch('src.projects.get_topic', return_value=7), \
                 patch.object(backends, 'get_backend', return_value='claude'), \
                 patch.object(backends, 'pane_processes', AsyncMock(return_value=['claude'])), \
                 patch.object(api_server, '_watch_transcript', fake_watch), \
                 patch.object(api_server, '_bot', object()), \
                 patch.object(api_server.config, 'CHAT_ID', 34), \
                 patch.dict(api_server.terminal_sessions, {}, clear=True):
                ok = asyncio.run(api_server.register_pane_watcher('%1', '/p', start_offsets=offsets))
                self.assertTrue(ok)
                self.assertEqual(api_server.terminal_sessions['target']['tmux_pane'], '%1')
            self.assertEqual(started, {'target': 3})
            api_server._watchers.pop('target', None)


class WatchResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for p in (patch.object(api_server, 'WATCH_OFFSETS_FILE', self.root / 'offsets.json'),
                  patch.object(api_server, '_watch_offsets', None)):
            p.start(); self.addCleanup(p.stop)

    def test_saved_offset_round_trip_and_limits(self):
        log = self.root / 'a.jsonl'
        api_server.save_watch_offset(str(log), 100)
        api_server._watch_offsets = None  # 模拟重启后从磁盘读取
        self.assertEqual(api_server.saved_watch_offset(str(log), 150), 100)
        self.assertIsNone(api_server.saved_watch_offset(str(log), 50))  # 文件被截断
        self.assertIsNone(api_server.saved_watch_offset(str(log), 100 + api_server.WATCH_RESUME_MAX_BACKLOG + 1))
        api_server._offsets()[str(log.resolve())][1] -= api_server.WATCH_RESUME_MAX_AGE + 1
        self.assertIsNone(api_server.saved_watch_offset(str(log), 150))

    def test_recovery_resumes_from_saved_offset(self):
        folder = self.root / 'proj'; folder.mkdir()
        log = folder / 'sid.jsonl'
        log.write_text('{"a": 1}\n{"b": 2}\n')
        api_server.save_watch_offset(str(log), 9)
        started = []

        async def fake_watch(sid, path, *args, start_pos=None):
            started.append(start_pos)

        with patch('src.projects.claude_dir', return_value=folder), \
             patch('src.projects.get_topic', return_value=7), \
             patch.object(backends, 'get_backend', return_value='claude'), \
             patch.object(backends, 'pane_processes', AsyncMock(return_value=['claude'])), \
             patch.object(api_server, '_watch_transcript', fake_watch), \
             patch.object(api_server, '_bot', object()), \
             patch.object(api_server.config, 'CHAT_ID', 34), \
             patch.dict(api_server.terminal_sessions, {}, clear=True):
            asyncio.run(api_server.register_pane_watcher('%1', '/p', resume_saved=True))
            asyncio.run(api_server.register_pane_watcher('%1', '/p'))  # 非恢复场景仍从末尾开始
        api_server._watchers.pop('sid', None)
        self.assertEqual(started, [9, log.stat().st_size])

    def test_stop_watchers_cancels_all(self):
        async def scenario():
            task = asyncio.create_task(asyncio.sleep(60))
            with patch.dict(api_server._watchers, {'s': task}, clear=True):
                await api_server.stop_watchers()
            return task.cancelled()
        self.assertTrue(asyncio.run(scenario()))


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
                        patch.object(api_server, '_event_replay', defaultdict(lambda: deque(maxlen=200))),
                        patch('src.model_prefs.PREFS_FILE', Path(self.temp.name) / 'prefs.json'),
                        patch('src.model_prefs.codex_models', return_value=[])]
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

    async def test_resuming_rollout_reads_only_bytes_after_input_snapshot(self):
        await self._check_registration_snapshot(existing=True)

    async def test_genuinely_new_rollout_keeps_first_reply(self):
        await self._check_registration_snapshot(existing=False)

    async def _check_registration_snapshot(self, existing):
        cwd = self.temp.name
        backends.set_backend(cwd, 'codex')
        root = Path(cwd) / 'sessions'; root.mkdir()
        path = root / 'rollout-test.jsonl'
        old = json.dumps(item('AgentMessage', 'yesterday', 'old')) + '\n'
        if existing:
            path.write_text(old)
        with patch('src.projects.codex_sessions_dir', return_value=root):
            offsets = backends.codex_transcript_offsets()
        with path.open('a') as f:
            f.write(json.dumps(item('AgentMessage', 'new reply', 'new')) + '\n')
        with patch.object(api_server.config, 'CHAT_ID', 34), \
             patch.object(api_server.projects, 'get_topic', return_value=206), \
             patch.object(backends, 'pane_processes', AsyncMock(return_value=['123'])), \
             patch.object(backends, 'codex_transcript', AsyncMock(return_value=('sid', str(path)))), \
             patch.object(backends, 'command', AsyncMock(return_value='full')):
            self.assertTrue(await api_server.register_pane_watcher('%33', cwd, start_offsets=offsets))
            task = api_server._watchers['sid']
            try:
                for _ in range(30):
                    if api_server._bot.send_message.await_count:
                        break
                    await asyncio.sleep(.05)
                api_server._bot.send_message.assert_awaited_once()
                self.assertIn('new reply', api_server._bot.send_message.call_args.kwargs['text'])
                self.assertNotIn('yesterday', api_server._bot.send_message.call_args.kwargs['text'])
            finally:
                task.cancel()
                await task

    async def test_missing_open_rollout_captures_offsets_before_injection(self):
        cwd = self.temp.name; backends.set_backend(cwd, 'codex')
        message = SimpleNamespace(
            message_thread_id=206, chat_id=34, chat=SimpleNamespace(type='supergroup'),
            text='new input', caption=None, photo=None, document=None, video=None,
            audio=None, voice=None, sticker=None, reply_to_message=None,
            message_id=100, reply_text=AsyncMock())
        update = SimpleNamespace(message=message, effective_user=SimpleNamespace(id=77))
        order = []
        def snapshot():
            order.append('snapshot'); return {'/old-rollout': 1234}
        async def inject(*args): order.append('inject')
        with patch.object(bot, 'auth_check', return_value=True), \
             patch.object(bot.config, 'CHAT_ID', 34), \
             patch.object(bot.projects, 'get_path', return_value=cwd), \
             patch.object(bot, '_find_pane_for_project', AsyncMock(return_value='%33')), \
             patch.object(backends, 'codex_transcript', AsyncMock(return_value=None)), \
             patch.object(backends, 'codex_transcript_offsets', side_effect=snapshot), \
             patch.object(bot, '_inject_tmux', side_effect=inject), \
             patch.object(bot, 'start_typing'), \
             patch.object(api_server, 'mark_tg_input'), \
             patch.object(api_server, 'mark_tg_reply_target'), \
             patch.object(api_server, 'register_pane_watcher', AsyncMock(return_value=True)) as register:
            await bot._handle_message(update, SimpleNamespace(bot=object()))
        self.assertEqual(order, ['snapshot', 'inject'])
        register.assert_awaited_once_with('%33', cwd, start_offsets={'/old-rollout': 1234})

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
        self.assertIn('引擎：Codex', message.reply_text.call_args.args[0])

    async def test_web_rejects_inactive_backend(self):
        cwd = self.temp.name; backends.set_backend(cwd, 'codex')
        api_server.terminal_sessions['old'] = {'cwd': cwd, 'backend': 'claude', 'tmux_pane': '%3'}
        request = SimpleNamespace(match_info={'id': 'old'})
        result = await api_server.handle_session_input(request)
        self.assertEqual(result.status, 409)


if __name__ == '__main__': unittest.main()
