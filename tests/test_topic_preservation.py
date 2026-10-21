import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from src import bot, backends
import json
import os

class TopicPreservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_directory_never_deletes_topic_or_unpins(self):
        fake = SimpleNamespace(delete_forum_topic=AsyncMock(), unpin_all_forum_topic_messages=AsyncMock())
        with patch.object(bot.projects, 'all_bindings', return_value={123: '/missing-directory-fixture'}), \
             patch.object(bot.projects, 'discover', return_value=[]), \
             patch.object(bot.projects, 'unbind') as unbind, \
             patch.object(bot, '_purge_topic_state', AsyncMock()) as purge:
            await bot.sync_all_projects(fake, 34)
        fake.delete_forum_topic.assert_not_awaited()
        fake.unpin_all_forum_topic_messages.assert_not_awaited()
        unbind.assert_not_called()
        purge.assert_not_awaited()

    async def test_renamed_rollout_matches_only_actual_process_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rollout-test.jsonl'
            path.write_text(json.dumps({'type':'session_meta','payload':{
                'id':'preserved', 'cwd':directory+'/old-name'}})+'\n')
            with patch.object(backends, 'pane_processes', AsyncMock(return_value=[str(os.getpid())])), \
                 patch.object(backends, 'command', AsyncMock(return_value='n'+str(path))):
                self.assertEqual(await backends.codex_transcript('%39',str(Path.cwd())),('preserved',str(path)))
                self.assertIsNone(await backends.codex_transcript('%39',directory))
            # If the recorded directory still exists, do not guess a rename.
            path.write_text(json.dumps({'type':'session_meta','payload':{'id':'preserved','cwd':directory}})+'\n')
            with patch.object(backends, 'pane_processes', AsyncMock(return_value=[str(os.getpid())])), \
                 patch.object(backends, 'command', AsyncMock(return_value='n'+str(path))):
                self.assertIsNone(await backends.codex_transcript('%39',str(Path.cwd())))
