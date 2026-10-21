import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src import api_server, backends
from src.renderer import split_user_message


class UserEchoTests(unittest.TestCase):
    def test_agent_header_is_compact_and_body_survives_chunking(self):
        body = '  引用 <tag> & 😀\n' * 1000
        prefix = '⇢ from seatalk-workspace\n\n'
        chunks = split_user_message('[Agent message receipt-1 from seatalk-workspace]\n' + body)
        self.assertEqual(''.join(c[len(prefix):] for c in chunks), body)
        self.assertTrue(all(c.startswith(prefix) for c in chunks))
        self.assertTrue(all(len(c.encode('utf-16-le')) // 2 <= 4000 for c in chunks))

    def test_quoted_agent_marker_is_not_rewritten(self):
        text = '例子：[Agent message receipt-1 from apex]\n正文'
        self.assertEqual(split_user_message(text), ['› ' + text])

    def test_long_input_is_lossless_with_whitespace_and_markup(self):
        text = '  <tag>& **literal**\n\n' + '中文😀\n' * 2500 + ' END  '
        chunks = split_user_message(text)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(''.join(chunk[2:] for chunk in chunks), text)
        self.assertTrue(all(len(c.encode('utf-16-le')) // 2 <= 4000 for c in chunks))

    def test_888_chars_are_not_cut_and_boundary_keeps_tail(self):
        self.assertEqual(split_user_message('文' * 888), ['› ' + '文' * 888])
        for length in (3997, 3998, 7994, 7995):
            text = 'a' * length
            self.assertEqual(''.join(c[2:] for c in split_user_message(text)), text)


class WatcherUserEchoTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_ordered_echo_dedup_and_tg_source_suppression(self):
        text = '<tag>&😀\n' * 1600 + '尾部完整'
        def user(uid, body):
            return {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {
                'type': 'UserMessage', 'id': uid, 'content': [{'type': 'Text', 'text': body}]}}}
        with tempfile.TemporaryDirectory() as cwd:
            path = Path(cwd) / 'rollout.jsonl'
            event = user('pc-msg', text)
            path.write_text(''.join(json.dumps(e) + '\n' for e in
                                   [event, event, user('tg-msg', 'from TG')]))
            fake = SimpleNamespace(send_message=AsyncMock())
            with patch.object(api_server, '_bot', fake), \
                 patch.object(api_server, 'terminal_sessions', {'echo-test': {'cwd': cwd, 'backend': 'codex'}}), \
                 patch.object(backends, 'get_backend', return_value='codex'), \
                 patch.object(api_server, 'broadcast_event'), \
                 patch.object(api_server, '_consume_tg_input', side_effect=lambda topic, body: body == 'from TG'):
                task = asyncio.create_task(api_server._watch_transcript('echo-test', str(path), 202, 34, start_pos=0))
                try:
                    expected = split_user_message(text)
                    for _ in range(40):
                        if fake.send_message.await_count >= len(expected):
                            break
                        await asyncio.sleep(.05)
                    calls = fake.send_message.call_args_list
                    self.assertEqual([c.kwargs['text'] for c in calls], expected)
                    self.assertTrue(all(c.kwargs['message_thread_id'] == 202 and
                                        c.kwargs['parse_mode'] is None for c in calls))
                finally:
                    task.cancel()
                    await task
