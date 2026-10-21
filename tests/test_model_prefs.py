"""/model 偏好：解析、存取、落到 Claude / Codex 启动参数；/new 参数校验。无网络、不起 CLI。"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src import backends, bot, model_prefs

CATALOG = [{"slug": "gpt-6-sol", "name": "GPT-6-Sol", "efforts": ["low", "high", "xhigh", "ultra"], "default": "medium"},
           {"slug": "gpt-5.5", "name": "GPT-5.5", "efforts": ["low", "high"], "default": "medium"}]


class ParseTests(unittest.TestCase):
    def test_any_order_and_engine_inferred_from_model(self):
        req = model_prefs.parse(["high", "Sonnet", "full"], "codex", CATALOG)
        self.assertEqual((req.engine, req.model, req.effort, req.permission),
                         ("claude", "claude-sonnet-5", "high", "full"))
        req = model_prefs.parse(["gpt-6-sol", "ultra"], "claude", CATALOG)
        self.assertEqual((req.engine, req.model, req.effort), ("codex", "gpt-6-sol", "ultra"))

    def test_effort_only_keeps_engine_and_model(self):
        req = model_prefs.parse(["max"], "claude", CATALOG)
        self.assertEqual((req.engine, req.model, req.effort), (None, None, "max"))

    def test_rejections(self):
        cases = [(["foo"], "不认识"), (["claude", "gpt-6-sol"], "Codex 的模型"),
                 (["opus", "workspace"], "权限只能是"), (["gpt-5.5", "ultra"], "不可用"),
                 (["claude", "ultra"], "不可用")]
        for args, message in cases:
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, message):
                model_prefs.parse(args, "claude", CATALOG)


class LaunchArgsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.cwd = str(root / "proj")
        Path(self.cwd).mkdir()
        for p in (patch.object(model_prefs, "PREFS_FILE", root / "prefs.json"),
                  patch.object(backends, "SESSIONS_FILE", root / "sessions.json"),
                  patch("src.permissions.get_mode", return_value="full")):
            p.start(); self.addCleanup(p.stop)

    def test_update_merges_and_ignores_none(self):
        model_prefs.update(self.cwd, "claude", model="claude-sonnet-5", effort="high")
        model_prefs.update(self.cwd, "claude", effort=None, permission="full")
        self.assertEqual(model_prefs.get(self.cwd, "claude"),
                         {"model": "claude-sonnet-5", "effort": "high", "permission": "full"})
        self.assertEqual(model_prefs.get(self.cwd, "codex"), {})

    def test_claude_args_from_prefs_with_resume(self):
        model_prefs.update(self.cwd, "claude", model="claude-sonnet-5", effort="high", permission="full")
        self.assertEqual(backends.claude_launch_args(self.cwd, resume_id="sid"),
                         ["claude", "--resume", "sid", "--model", "claude-sonnet-5", "--effort", "high",
                          "--dangerously-skip-permissions", "--settings", backends.CLAUDE_FULL_SETTINGS])
        settings = json.loads(backends.CLAUDE_FULL_SETTINGS)
        self.assertTrue(settings["skipDangerousModePermissionPrompt"])

    def test_legacy_nas_permission_file_is_honored_until_overridden(self):
        legacy = Path(self.cwd).parent / "claude-permissions.json"
        legacy.write_text(json.dumps({str(Path(self.cwd).resolve()): "full"}))
        with patch.object(model_prefs, "LEGACY_CLAUDE_PERMISSIONS", legacy):
            self.assertEqual(model_prefs.get(self.cwd, "claude"), {"permission": "full"})
            model_prefs.update(self.cwd, "claude", permission="default")
            self.assertEqual(model_prefs.get(self.cwd, "claude")["permission"], "default")

    def test_claude_default_permission_overrides_settings_bypass(self):
        (Path(self.cwd) / ".claude").mkdir()
        (Path(self.cwd) / ".claude" / "settings.local.json").write_text(
            '{"permissions": {"defaultMode": "bypassPermissions"}}')
        self.assertIn("--dangerously-skip-permissions", backends.claude_launch_args(self.cwd))
        model_prefs.update(self.cwd, "claude", permission="default")
        self.assertNotIn("--dangerously-skip-permissions", backends.claude_launch_args(self.cwd))

    def test_codex_prefs_win_over_running_model(self):
        model_prefs.update(self.cwd, "codex", model="gpt-6-sol", effort="xhigh")
        args = backends.codex_launch_args(self.cwd, resume_id="r1", model="gpt-5.5")
        self.assertEqual(args[args.index("-m") + 1], "gpt-6-sol")
        self.assertIn('model_reasoning_effort="xhigh"', args)
        self.assertEqual(args[-2:], ["resume", "r1"])


class NewCommandTests(unittest.TestCase):
    def test_rejects_bad_names_without_creating_anything(self):
        for args in ([], ["../etc"], ["a b"], ["ok", "vim"]):
            message = SimpleNamespace(reply_text=AsyncMock(), message_thread_id=None)
            update = SimpleNamespace(effective_user=SimpleNamespace(id=1), message=message,
                                     effective_chat=SimpleNamespace(id=-1001))
            context = SimpleNamespace(args=args, bot=SimpleNamespace(create_forum_topic=AsyncMock()))
            with patch.object(bot, "auth_check", return_value=True):
                asyncio.run(bot.cmd_new(update, context))
            context.bot.create_forum_topic.assert_not_awaited()
            self.assertIn("用法", message.reply_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
