"""统一配置文件 ~/.config/code-anywhere/config.env：生成、合并旧 .env、权限、hooks 读端口。"""
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "provision"))
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))
import setup  # noqa: E402
import common  # noqa: E402


class ConfigFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "cfg" / "config.env"
        self.legacy = Path(self.tmp.name) / ".env"
        for p in (patch.object(setup, "CONFIG_FILE", self.config), patch.object(setup, "LEGACY_ENV", self.legacy),
                  patch.object(setup, "CHECK_ONLY", False)):
            p.start()
            self.addCleanup(p.stop)

    def test_creates_from_template_and_merges_legacy_env(self):
        self.legacy.write_text("BOT_TOKEN=123:abc\nCHAT_ID=42\nANTHROPIC_API_KEY=sk-x\n# COMMENTED=1\n")
        setup.step_config()
        text = self.config.read_text()
        self.assertIn("BOT_TOKEN=123:abc", text)
        self.assertIn("ANTHROPIC_API_KEY=sk-x", text)        # 原来被注释的模板项被启用并填值
        self.assertIn("# ───────────────── Telegram", text)     # 模板注释保留
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        self.assertTrue(self.legacy.exists())                # 旧文件不删

    def test_config_file_wins_over_legacy(self):
        self.legacy.write_text("BOT_TOKEN=old\nBOT_PORT=5000\n")
        setup.write_env({"BOT_TOKEN": "new"})
        self.assertEqual(setup.read_config()["BOT_TOKEN"], "new")
        self.assertEqual(setup.read_config()["BOT_PORT"], "5000")

    def test_hooks_read_port_from_config(self):
        setup.write_env({"BOT_PORT": "5123"})
        with patch.dict(os.environ, {"CODE_ANYWHERE_CONFIG": str(self.config)}, clear=False):
            os.environ.pop("CA_BOT_PORT", None)
            self.assertEqual(common.get_bot_url(), "http://127.0.0.1:5123")


if __name__ == "__main__":
    unittest.main()
