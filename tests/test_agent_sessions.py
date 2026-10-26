import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src import agent_sessions as service


class SessionToolsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'rollout.jsonl'
        self.path.write_text('')
        self.patch = patch.object(service, 'RECEIPTS', Path(self.tmp.name) / 'receipts.sqlite3')
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.session = {'session_id': 'codex:%99:123:456', 'status': 'waiting_input',
                        'cwd': self.tmp.name, 'tmux_pane': '%99', '_transcript': str(self.path)}

    def test_public_summary_excludes_reasoning_and_tools(self):
        for event in [
            {'type': 'response_item', 'payload': {'type': 'reasoning', 'text': 'secret'}},
            {'type': 'assistant', 'message': {'content': [{'type': 'thinking', 'thinking': 'secret'},
                                                          {'type': 'tool_use', 'input': 'secret'}]}},
        ]:
            self.assertEqual(service.public_text(event)[1], '')
        self.assertEqual(service.public_text({'type': 'response_item', 'payload': {
            'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'public'}]}}),
            ('assistant', 'public'))

    def test_status_running_confirmation_unknown_and_idle(self):
        self.assertEqual(service.screen_state('Working (12s • esc to interrupt)\n› Ask Codex to do anything')[0], 'running')
        self.assertEqual(service.screen_state('Would you like to proceed?\n›')[0], 'waiting_confirmation')
        self.assertEqual(service.screen_state('Some unrelated log output')[0], 'unknown')
        self.assertEqual(service.screen_state('› Ask Codex to do anything')[0], 'waiting_input')
        # 新版 Claude Code 干活时输入框照样显示，只有转圈那一行能说明在忙
        self.assertEqual(service.screen_state('● 第 1 次完成。\n· Simmering… (18s · ↓ 188 tokens)\n' + '─' * 20 + '\n❯ \n' + '─' * 20)[0], 'running')

    async def test_busy_does_not_inject(self):
        self.session['status'] = 'running'
        with patch.object(service, 'discover', AsyncMock(return_value=[self.session])), \
             patch('src.bot._inject_tmux', AsyncMock()) as inject:
            result = await service.send_message(self.session['session_id'], 'hello', 'busy-1')
        self.assertEqual(result['status'], 'not_sent')
        inject.assert_not_awaited()

    async def test_urgent_enters_working_session_and_counts_queued_receipt(self):
        self.session['status'] = 'running'
        async def inject(pane, text):  # Claude 一轮进行中收到的输入记为 queued_command 附件
            with self.path.open('a') as f:
                f.write(json.dumps({'type': 'attachment', 'attachment': {'type': 'queued_command', 'prompt': text}}) + '\n')
        with patch.object(service, 'discover', AsyncMock(return_value=[self.session])), \
             patch('src.bot._inject_tmux', AsyncMock(side_effect=inject)) as write:
            result = await service.send_message(self.session['session_id'], 'hotfix', 'urgent-1', urgent=True)
        self.assertEqual(result['status'], 'received')
        self.assertIn('[Urgent agent message urgent-1 from', write.await_args.args[1])

    async def test_urgent_still_refuses_confirmation_prompt(self):
        self.session['status'] = 'waiting_confirmation'
        with patch.object(service, 'discover', AsyncMock(return_value=[self.session])), \
             patch('src.bot._inject_tmux', AsyncMock()) as write:
            result = await service.send_message(self.session['session_id'], 'hotfix', 'urgent-2', urgent=True)
        self.assertEqual(result['status'], 'not_sent')
        write.assert_not_awaited()

    async def test_urgent_and_normal_requests_do_not_share_an_id(self):
        with patch.object(service, 'discover', AsyncMock(return_value=[self.session])), \
             patch('src.bot._inject_tmux', AsyncMock()), patch.object(service.asyncio, 'sleep', AsyncMock()):
            await service.send_message(self.session['session_id'], 'hello', 'same-1')
            with self.assertRaises(ValueError):
                await service.send_message(self.session['session_id'], 'hello', 'same-1', urgent=True)

    async def test_receipt_idempotency_and_conflicting_retry(self):
        async def inject(pane, text):
            with self.path.open('a') as f:
                f.write(json.dumps({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user',
                    'content': [{'type': 'input_text', 'text': text}]}}) + '\n')
        with patch.object(service, 'discover', AsyncMock(return_value=[self.session])), \
             patch('src.bot._inject_tmux', AsyncMock(side_effect=inject)) as write:
            first = await service.send_message(self.session['session_id'], 'hello', 'receipt-1')
            second = await service.send_message(self.session['session_id'], 'hello', 'receipt-1')
            self.assertEqual(first['status'], 'received')
            self.assertEqual(second, first)
            write.assert_awaited_once()
            with self.assertRaises(ValueError):
                await service.send_message(self.session['session_id'], 'different', 'receipt-1')

    async def test_no_receipt_does_not_report_success(self):
        # Preexisting matching text must not count as a receipt of this injection.
        self.path.write_text(json.dumps({'type': 'user', 'message': {
            'content': '[Agent message unconfirmed-1 from agent]\nhello'}}) + '\n')
        with patch.object(service, 'discover', AsyncMock(return_value=[self.session])), \
             patch('src.bot._inject_tmux', AsyncMock()) as write, \
             patch.object(service.asyncio, 'sleep', AsyncMock()):
            result = await service.send_message(self.session['session_id'], 'hello', 'unconfirmed-1')
            self.assertEqual(result['status'], 'unconfirmed')
            await service.send_message(self.session['session_id'], 'hello', 'unconfirmed-1')
            write.assert_awaited_once()

    async def test_fresh_codex_without_rollout_accepts_first_message_and_binds_new_log(self):
        self.session.update(backend='codex', _transcript=None)

        async def inject(pane, text):
            self.path.write_text(json.dumps({'type': 'response_item', 'payload': {
                'type': 'message', 'role': 'user',
                'content': [{'type': 'input_text', 'text': text}]}}) + '\n')

        with patch.object(service, 'discover', AsyncMock(return_value=[self.session])), \
             patch.object(service.backends, 'codex_transcript_offsets', return_value={str(self.path.resolve()): 0}), \
             patch.object(service.backends, 'codex_transcript', AsyncMock(return_value=('native-new', str(self.path)))), \
             patch('src.api_server.register_pane_watcher', AsyncMock(return_value=True)) as register, \
             patch('src.bot._inject_tmux', AsyncMock(side_effect=inject)) as write:
            result = await service.send_message(self.session['session_id'], 'first', 'fresh-codex-1')

        self.assertEqual(result['status'], 'received')
        write.assert_awaited_once()
        register.assert_awaited_once_with('%99', self.tmp.name,
                                          start_offsets={str(self.path.resolve()): 0})

    async def test_missing_transcript_still_refuses_non_codex_target(self):
        self.session.update(backend='claude', _transcript=None)
        with patch.object(service, 'discover', AsyncMock(return_value=[self.session])), \
             patch('src.bot._inject_tmux', AsyncMock()) as write:
            result = await service.send_message(self.session['session_id'], 'hello', 'fresh-claude-1')
        self.assertEqual(result['status'], 'not_sent')
        write.assert_not_awaited()

    async def test_replaced_target_not_sent(self):
        with patch.object(service, 'discover', AsyncMock(side_effect=[[self.session], []])), \
             patch('src.bot._inject_tmux', AsyncMock()) as write:
            result = await service.send_message(self.session['session_id'], 'hello', 'replaced-1')
        self.assertEqual(result['status'], 'not_sent')
        write.assert_not_awaited()

    async def test_exited_status(self):
        with patch.object(service, 'discover', AsyncMock(return_value=[])):
            result = await service.get_status('old-session')
        self.assertEqual(result['status'], 'exited')

    async def test_feedback_reports_late_receipt_and_latest_assistant(self):
        self.session.update(last_activity_at=123, status_evidence='empty input prompt')
        with patch.object(service, 'discover', AsyncMock(return_value=[self.session])), \
             patch('src.bot._inject_tmux', AsyncMock()), \
             patch.object(service.asyncio, 'sleep', AsyncMock()):
            sent = await service.send_message(self.session['session_id'], 'hello', 'late-1')
            self.assertEqual(sent['status'], 'unconfirmed')
            with self.path.open('a') as f:
                f.write(json.dumps({'type': 'user', 'timestamp': '2026-10-08T01:00:00Z',
                    'message': {'content': '[Agent message late-1 from agent]\nhello'}}) + '\n')
                f.write(json.dumps({'type': 'assistant', 'timestamp': '2026-10-08T01:00:01Z',
                    'message': {'content': [{'type': 'text', 'text': 'I am checking.'}]}}) + '\n')
            result = await service.get_feedback(self.session['session_id'], 'late-1')
        self.assertTrue(result['alive'])
        self.assertEqual(result['delivery']['status'], 'received')
        self.assertEqual(result['last_assistant_message']['text'], 'I am checking.')
        self.assertEqual(result['last_message']['role'], 'assistant')
        self.assertNotIn('_transcript', result['delivery'])

    async def test_feedback_rejects_wrong_target_request(self):
        with patch.object(service, 'discover', AsyncMock(return_value=[])):
            await service.send_message('old', 'hello', 'wrong-1')
            with self.assertRaises(ValueError):
                await service.get_feedback('different', 'wrong-1')

    def test_tail_offset_ignores_old_messages(self):
        self.path.write_text('{"old":true}\n')
        offset = self.path.stat().st_size
        with self.path.open('a') as f:
            f.write('{"new":true}\n')
        self.assertEqual(service.tail_events(self.path, offset), [{'new': True}])


if __name__ == '__main__':
    unittest.main()
