import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from src import permissions, backends, bot, api_server


class PermissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = self.tmp.name
        patches = [patch.object(permissions, 'PERMISSIONS_FILE', Path(self.cwd) / 'permissions.json'),
                   patch.object(backends, 'PREFERENCES_FILE', Path(self.cwd) / 'backends.json'),
                   patch.object(backends, 'SESSIONS_FILE', Path(self.cwd) / 'sessions.json'),
                   patch.object(api_server, 'terminal_sessions', {}),
                   patch('src.model_prefs.PREFS_FILE', Path(self.cwd) / 'prefs.json'),
                   patch('src.model_prefs.codex_models', return_value=[])]
        for p in patches: p.start(); self.addCleanup(p.stop)
        self.message = SimpleNamespace(chat_id=34, message_thread_id=12, reply_text=AsyncMock())
        self.update = SimpleNamespace(effective_user=SimpleNamespace(id=77), message=self.message)

    def test_full_is_explicit_project_only_and_survives_reload(self):
        permissions.set_mode(self.cwd, 'full')
        with patch('src.state.is_trusted', return_value=False):
            self.assertEqual(permissions.get_mode('/another-project'), 'read-only')
            self.assertEqual(permissions.get_mode(self.cwd), 'full')
        args = backends.codex_launch_args(self.cwd, resume_id='native-id', model='gpt-test')
        self.assertEqual(args[args.index('-s') + 1], 'danger-full-access')
        self.assertEqual(args[-2:], ['resume', 'native-id'])
        self.assertEqual(args[args.index('-m') + 1], 'gpt-test')
        self.assertEqual(args[args.index('-a') + 1], 'never')

    def test_workspace_enables_network_and_read_only_stays_restricted(self):
        permissions.set_mode(self.cwd, 'workspace')
        self.assertIn('sandbox_workspace_write.network_access=true', backends.codex_launch_args(self.cwd))
        permissions.set_mode(self.cwd, 'read-only')
        self.assertNotIn('sandbox_workspace_write.network_access=true', backends.codex_launch_args(self.cwd))

    def test_invalid_mode_and_corrupt_storage_fail_closed(self):
        with self.assertRaises(ValueError): permissions.set_mode(self.cwd, 'unsafe-typo')
        permissions.PERMISSIONS_FILE.write_text('{')
        with self.assertRaises(ValueError): backends.codex_launch_args(self.cwd)

    async def test_model_codex_full_applies_immediately(self):
        backends.set_backend(self.cwd, 'codex')
        with patch.object(bot, 'auth_check', return_value=True), \
             patch.object(bot, '_update_cwd', return_value=self.cwd), \
             patch.object(bot, '_find_pane_for_project', AsyncMock(return_value='%1')), \
             patch('src.model_switch.restart', AsyncMock(return_value='已重启终端并接回原会话。')) as restart:
            await bot.cmd_model(self.update, SimpleNamespace(args=['codex', 'full']))
        self.assertEqual(permissions.get_mode(self.cwd), 'full')
        restart.assert_awaited_once_with(self.cwd, 12)
        self.assertIn('Full', self.message.reply_text.call_args.args[0])

    async def test_invalid_model_mode_does_not_change_backend(self):
        with patch.object(bot, 'auth_check', return_value=True), \
             patch.object(bot, '_update_cwd', return_value=self.cwd), \
             patch.object(permissions, 'apply_mode', AsyncMock()) as apply:
            await bot.cmd_model(self.update, SimpleNamespace(args=['claude', 'full']))
        self.assertEqual(backends.get_backend(self.cwd), 'claude')
        apply.assert_not_awaited()

    async def test_permissions_rejects_unauthorized_user(self):
        with patch.object(bot, 'auth_check', return_value=False), \
             patch.object(permissions, 'apply_mode', AsyncMock()) as apply:
            await bot.cmd_permissions(self.update, SimpleNamespace(args=['full']))
        apply.assert_not_awaited()

    async def test_permissions_status_does_not_restart(self):
        backends.set_backend(self.cwd, 'codex')
        with patch.object(bot, 'auth_check', return_value=True), \
             patch.object(bot, '_update_cwd', return_value=self.cwd), \
             patch.object(permissions, 'apply_mode', AsyncMock()) as apply:
            await bot.cmd_permissions(self.update, SimpleNamespace(args=[]))
        apply.assert_not_awaited()

    async def test_missing_session_id_refuses_to_kill_terminal(self):
        backends.set_backend(self.cwd, 'codex')
        with patch.object(bot, '_find_pane_for_project', AsyncMock(return_value='%1')), \
             patch.object(backends, 'codex_transcript', AsyncMock(return_value=None)), \
             patch.object(backends, 'command', AsyncMock()) as command:
            with self.assertRaises(RuntimeError): await permissions.apply_mode(self.cwd, 'full', 34, 12)
        command.assert_not_awaited()
        self.assertIsNone(permissions.get_override(self.cwd))

    async def test_restart_preserves_pane_and_exact_native_session(self):
        backends.set_backend(self.cwd, 'codex')
        api_server.terminal_sessions['native-id'] = {'model': 'gpt-test'}
        with patch.object(bot, '_find_pane_for_project', AsyncMock(return_value='%1')), \
             patch.object(backends, 'codex_transcript', AsyncMock(return_value=('native-id', '/rollout'))), \
             patch.object(backends, 'codex_env_keys', return_value=[]), \
             patch.object(backends, 'command', AsyncMock(return_value='')) as command, \
             patch.object(api_server, 'register_pane_watcher', AsyncMock(return_value=True)), \
             patch.object(permissions.asyncio, 'sleep', AsyncMock()):
            self.assertEqual(await permissions.apply_mode(self.cwd, 'full', 34, 12), '%1')
        respawn = next(c.args for c in command.await_args_list if c.args[1] == 'respawn-pane')
        self.assertEqual(respawn[respawn.index('-t') + 1], '%1')
        self.assertEqual(respawn[-2:], ('resume', 'native-id'))
        self.assertIn('danger-full-access', respawn)
        self.assertEqual(api_server.terminal_sessions['native-id']['permission_mode'], 'full')

    async def test_switch_to_new_engine_accepts_folder_trust_prompt(self):
        # 第一次在这个目录跑 Claude 会弹信任框；切换引擎时要自动接受，否则卡住、随后的消息还会把 CLI 退掉
        from src import model_switch
        backends.set_backend(self.cwd, 'claude')
        accept = AsyncMock(return_value=True)
        with patch.object(bot, '_find_pane_for_project', AsyncMock(return_value='%1')), \
             patch.object(backends, 'pane_processes', AsyncMock(return_value=[123])), \
             patch.object(backends, 'command', AsyncMock(return_value='')), \
             patch.object(bot, '_accept_trust_prompt', accept), \
             patch.object(bot, '_hook_session_for_pane', AsyncMock(return_value=True)), \
             patch.object(model_switch.asyncio, 'sleep', AsyncMock()):
            self.assertIn('新会话', await model_switch.restart(self.cwd, 12))
        accept.assert_awaited_with('%1', wait=0.5)

    async def test_failed_restart_restores_saved_preference(self):
        backends.set_backend(self.cwd, 'codex')
        permissions.set_mode(self.cwd, 'workspace')
        with patch.object(bot, '_find_pane_for_project', AsyncMock(return_value='%1')), \
             patch.object(backends, 'codex_transcript', AsyncMock(return_value=('native-id', '/rollout'))), \
             patch.object(backends, 'codex_env_keys', return_value=[]), \
             patch.object(backends, 'command', AsyncMock(side_effect=RuntimeError('tmux failed'))):
            with self.assertRaises(RuntimeError): await permissions.apply_mode(self.cwd, 'full', 34, 12)
        self.assertEqual(permissions.get_mode(self.cwd), 'workspace')


if __name__ == '__main__': unittest.main()
