import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src import agent_sessions as service, procinfo, session_mcp as mcp
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

    async def test_dead_pane_with_stripped_empty_path_is_ignored(self):
        with patch.object(service.backends, 'command', AsyncMock(return_value='%13\t1')), \
             patch.object(service.backends, 'pane_processes', AsyncMock()) as processes:
            self.assertEqual(await service.discover(), [])
            processes.assert_not_awaited()

    def test_exact_process_identity(self):
        self.assertEqual(mcp.source_session([self.source], [('123', '456')]), self.source['session_id'])
        with self.assertRaises(ValueError):
            mcp.source_session([self.source], [('123', '999')])

    def test_parent_walk_linux(self):
        def stat(path):
            pid = int(path.parent.name)
            name, parent = ('codex', 1) if pid == 123 else ('python', 123)
            return f'{pid} ({name}) S {parent} ' + '0 ' * 17 + '456'
        with patch.object(mcp.os, 'getppid', return_value=124), patch.object(procinfo, '_LINUX', True), \
             patch.object(Path, 'read_text', stat):
            self.assertEqual(mcp.caller_processes(), [('123', '456')])

    def test_parent_walk_macos(self):
        # macOS 没有 /proc：同样的父进程链改由 ps 取得，启动标记为启动时刻
        table = {124: dict(comm='/usr/bin/python3', ppid='123', lstart='Sat Oct 25 16:15:50 2026'),
                 123: dict(comm='/Users/x/.local/bin/claude', ppid='1', lstart='Fri Oct 24 23:05:07 2026')}
        with patch.object(mcp.os, 'getppid', return_value=124), patch.object(procinfo, '_LINUX', False), \
             patch.object(procinfo, '_ps', lambda pid, field: table[int(pid)][field]):
            found = mcp.caller_processes()
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][0], '123')
        self.assertTrue(found[0][1].isdigit())  # 进会话 ID（以冒号分段），不能带冒号

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
