import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src import agent_sessions as service, session_mcp as mcp
from src.renderer import split_user_message


class SenderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.source = dict(session_id='codex:%1:123:456', project='digital buddy',
                           backend='codex', tmux_pane='%1')

    async def test_dead_pane_not_discovered_via_reused_tty(self):
        with patch.object(service.backends, 'command', AsyncMock(return_value='%13\t1\t/old')), \
             patch.object(service.backends, 'pane_processes', AsyncMock()) as processes:
            self.assertEqual(await service.discover(), [])
            processes.assert_not_awaited()

    def test_exact_process_identity(self):
        self.assertEqual(mcp.source_session([self.source], [('123', '456')]), self.source['session_id'])
        with self.assertRaises(ValueError):
            mcp.source_session([self.source], [('123', '999')])

    def test_parent_walk(self):
        def stat(path):
            pid = int(path.parent.name)
            name, parent = ('codex', 1) if pid == 123 else ('python', 123)
            return f'{pid} ({name}) S {parent} ' + '0 ' * 17 + '456'
        with patch.object(mcp.os, 'getppid', return_value=124), patch.object(Path, 'read_text', stat):
            self.assertEqual(mcp.caller_processes(), [('123', '456')])

    async def test_mcp_ignores_supplied_label(self):
        call = AsyncMock(side_effect=[{'sessions': [self.source]}, {'status': 'not_sent'}])
        with patch.object(mcp, 'call', call), patch.object(mcp, 'caller_processes', return_value=[('123', '456')]):
            await mcp.send_message('target', 'hello', 'id', sender='forged')
        self.assertEqual(call.call_args.args[2], {'text': 'hello', 'request_id': 'id',
                                                'source_session_id': self.source['session_id']})

    async def test_source_name_and_ambiguity(self):
        with patch.object(service, 'discover', AsyncMock(return_value=[self.source])):
            self.assertEqual(await service.resolve_sender(self.source['session_id'], 'forged'),
                             ('digital buddy', self.source['session_id']))
            result = await service.send_message(self.source['session_id'], 'hello', 'self',
                                                source_session_id=self.source['session_id'])
            self.assertEqual(result['status'], 'not_sent')
        peer = dict(self.source, session_id='codex:%2:124:457', tmux_pane='%2')
        with patch.object(service, 'discover', AsyncMock(return_value=[self.source, peer])):
            self.assertEqual((await service.resolve_sender(self.source['session_id'], None))[0],
                             'digital buddy:codex:1')
        with patch.object(service, 'discover', AsyncMock(return_value=[])):
            with self.assertRaises(ValueError):
                await service.resolve_sender(self.source['session_id'], 'forged')

    async def test_old_receipt_retry_remains_idempotent(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(service, 'RECEIPTS', Path(directory) / 'r.db'):
            db = service.receipt_db()
            result = {'status': 'received', 'request_id': 'old', 'session_id': 'target'}
            fp = hashlib.sha256(json.dumps(['target', 'hello', 'agent']).encode()).hexdigest()
            db.execute('INSERT INTO receipts VALUES (?,?,?)', ('old', fp, json.dumps(result)))
            db.commit()
            db.close()
            with patch.object(service, 'discover', AsyncMock()) as discover:
                self.assertEqual(await service.send_message('target', 'hello', 'old'), result)
                discover.assert_not_awaited()

    def test_unicode_and_legacy_display(self):
        self.assertEqual(split_user_message('[Agent message id from 中文 workspace]\nhello'),
                         ['⇢ from 中文 workspace\n\nhello'])
        self.assertEqual(split_user_message('[Agent message id from agent]\nhello'),
                         ['⇢ from 来源未识别（旧客户端）\n\nhello'])
