import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from src import backends

class LiveBackendInferenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.cwd=self.temp.name
        p=patch.object(backends,'PREFERENCES_FILE',Path(self.cwd)/'preferences.json')
        p.start();self.addCleanup(p.stop)

    async def infer(self, live, directory=None):
        async def processes(pane,backend):return ['123'] if backend in live else []
        with patch.object(backends,'command',AsyncMock(return_value='%44\t'+(directory or self.cwd))), \
             patch.object(backends,'pane_processes',AsyncMock(side_effect=processes)):
            await backends.infer_live_backend(self.cwd)

    async def test_codex_only_is_detected_and_saved(self):
        await self.infer({'codex'})
        self.assertEqual(backends.get_backend(self.cwd),'codex')

    async def test_explicit_claude_is_preserved(self):
        backends.set_backend(self.cwd,'claude')
        await self.infer({'codex'})
        self.assertEqual(backends.get_backend(self.cwd),'claude')

    async def test_mixed_engines_not_guessed(self):
        await self.infer({'codex','claude'})
        self.assertFalse(backends.PREFERENCES_FILE.exists())

    async def test_child_directory_not_used(self):
        await self.infer({'codex'},self.cwd+'/child')
        self.assertFalse(backends.PREFERENCES_FILE.exists())

    async def test_no_process_does_not_set_preference(self):
        await self.infer(set())
        self.assertFalse(backends.PREFERENCES_FILE.exists())
