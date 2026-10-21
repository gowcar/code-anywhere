import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src import turn_status as state, backends, api_server


def event(kind, turn='a', second=1, **extra):
    return {'type': 'event_msg', 'timestamp': f'2026-10-15T01:00:{second:02d}Z',
            'payload': {'type': kind, 'turn_id': turn, **extra}}


ERROR = {'message': 'Selected model is at capacity. Please try a different model.',
         'codex_error_info': 'server_overloaded'}


class TurnStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        p = patch.object(state, 'STATE_DB', self.root / 'status.db')
        p.start(); self.addCleanup(p.stop)
        self.bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=123)))

    def test_real_completion_preserves_sanitized_error(self):
        normalized = backends.normalize_codex(event('task_complete', error=ERROR))
        self.assertEqual(normalized['activity'], 'failed')
        self.assertEqual(normalized['error']['code'], 'capacity')
        self.assertEqual(normalized['turn_id'], 'a')
        for error in [{'message': '401 api_key=secret'}, {'message': 'https://host/?token=secret'},
                      'timeout secret', {'codex_error_info': 'rate_limit'}]:
            self.assertNotIn('secret', json.dumps(state.safe_error(error)))

    async def test_error_then_completion_notified_once_persistently(self):
        state.observe('s', event('task_started'))
        state.observe('s', event('error', second=2, will_retry=False, message=ERROR['message']))
        await state.notify_failure(self.bot, 's', 'project', 1, 2)
        state.observe('s', event('task_complete', second=3, error=ERROR))
        await state.notify_failure(self.bot, 's', 'project', 1, 2)
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(state.get('s')['notified'], 123)
        self.assertIn('本轮已失败结束', self.bot.send_message.call_args.kwargs['text'])
        self.assertEqual(self.bot.send_message.call_args.kwargs['message_thread_id'], 2)

    async def test_retry_then_success_does_not_alert(self):
        state.observe('s', event('task_started'))
        state.observe('s', event('error', second=2, will_retry=True, message=ERROR['message']))
        self.assertEqual(state.get('s')['activity'], 'retrying')
        await state.notify_failure(self.bot, 's', 'project', 1, 2)
        state.observe('s', event('task_complete', second=3))
        await state.notify_failure(self.bot, 's', 'project', 1, 2)
        self.bot.send_message.assert_not_awaited()
        self.assertEqual(state.get('s')['activity'], 'idle')

    async def test_success_abort_and_historical_failure_do_not_alert(self):
        for sid, item in [('ok', event('task_complete')), ('abort', event('turn_aborted')),
                          ('old', event('task_complete', error=ERROR))]:
            state.observe(sid, item, historical=sid == 'old')
            await state.notify_failure(self.bot, sid, 'project', 1, 2)
        self.bot.send_message.assert_not_awaited()
        self.assertEqual(state.get('old')['activity'], 'failed')

    async def test_new_turn_ignores_old_late_completion(self):
        state.observe('s', event('task_started'))
        state.observe('s', event('task_complete', second=2, error=ERROR))
        state.observe('s', event('task_started', turn='b', second=3))
        state.observe('s', event('task_complete', turn='a', second=4, error=ERROR))
        self.assertEqual(state.get('s')['activity'], 'running')
        self.assertEqual(state.get('s')['turn_id'], 'b')
        await state.notify_failure(self.bot, 's', 'project', 1, 2)
        self.bot.send_message.assert_not_awaited()

    async def test_failed_telegram_delivery_retries_without_losing_state(self):
        state.observe('s', event('task_complete', error=ERROR))
        self.bot.send_message.side_effect = RuntimeError('offline')
        with self.assertRaises(RuntimeError):
            await state.notify_failure(self.bot, 's', 'project', 1, 2)
        self.assertTrue(state.get('s')['pending'])
        self.bot.send_message.side_effect = None
        await state.notify_failure(self.bot, 's', 'project', 1, 2)
        self.assertFalse(state.get('s')['pending'])

    def test_bootstrap_restores_error_without_replaying_notification(self):
        p = self.root / 'rollout.jsonl'
        p.write_text(json.dumps(event('task_complete', error=ERROR)) + '\n')
        state.bootstrap('s', p)
        self.assertEqual(state.display_state('s', 'waiting_input')[0], 'failed')
        self.assertEqual(state.display_state('s', 'running')[0], 'running')
        self.assertFalse(state.get('s')['pending'])

    async def test_unspecified_error_waits_for_final_outcome(self):
        state.observe('s', event('task_started'))
        state.observe('s', event('error', second=2, message=ERROR['message']))
        await state.notify_failure(self.bot, 's', 'project', 1, 2)
        self.bot.send_message.assert_not_awaited()
        state.observe('s', event('task_complete', second=3, last_agent_message='done'))
        self.assertEqual(state.get('s')['activity'], 'idle')

    async def test_restart_preserves_pending_and_successful_delivery_receipt(self):
        p = self.root / 'rollout.jsonl'
        events = [event('task_started'), event('task_complete', second=2, error=ERROR)]
        p.write_text(''.join(json.dumps(e) + '\n' for e in events))
        for e in events:
            state.observe('s', e)
        state.bootstrap('s', p)
        self.assertTrue(state.get('s')['pending'])
        await state.notify_failure(self.bot, 's', 'project', 1, 2)
        state.bootstrap('s', p)
        await state.notify_failure(self.bot, 's', 'project', 1, 2)
        self.bot.send_message.assert_awaited_once()

    async def test_status_and_feedback_show_failed_turn(self):
        from src import bot, agent_sessions
        state.observe('s', event('task_complete', error=ERROR))
        session = {'session_id': 'codex:%1:1:1', 'project': 'fixture', 'backend': 'codex',
                   'status': 'waiting_input', 'activity': 'failed', 'last_turn': state.get('s'),
                   'status_evidence': 'idle prompt', 'last_activity_at': 1, '_transcript': None}
        update = SimpleNamespace(effective_user=SimpleNamespace(id=1),
                                 message=SimpleNamespace(reply_text=AsyncMock()))
        with patch.object(agent_sessions, 'discover', AsyncMock(return_value=[session])), \
             patch.object(bot, 'auth_check', return_value=True):
            await bot.cmd_status(update, None)
            result = await agent_sessions.get_feedback(session['session_id'])
        self.assertEqual(result['activity'], 'failed')
        self.assertEqual(result['last_turn']['error']['code'], 'capacity')
        text = update.message.reply_text.call_args.args[0]
        self.assertIn('上一轮失败', text)
        self.assertIn('模型服务当前容量不足', text)
        self.assertNotIn('🟢', text)

    async def test_watcher_failure_notification_and_typing_stop(self):
        p = self.root / 'rollout.jsonl'
        p.write_text('')
        info = {'cwd': str(self.root), 'backend': 'codex'}
        with patch.object(api_server, '_bot', self.bot), \
             patch.object(api_server, 'terminal_sessions', {'s': info}), \
             patch.object(backends, 'get_backend', return_value='codex'), \
             patch.object(api_server, 'broadcast_event'), \
             patch('src.bot.stop_typing') as stop:
            task = asyncio.create_task(api_server._watch_transcript('s', str(p), 2, 1, start_pos=0))
            await asyncio.sleep(.1)
            p.write_text(json.dumps(event('task_started')) + '\n' +
                         json.dumps(event('task_complete', second=2, error=ERROR)) + '\n')
            try:
                for _ in range(70):
                    if self.bot.send_message.await_count:
                        break
                    await asyncio.sleep(.1)
                self.bot.send_message.assert_awaited_once()
                stop.assert_called_with(2)
                self.assertEqual(info['turn_status']['activity'], 'failed')
            finally:
                task.cancel()
                await task
