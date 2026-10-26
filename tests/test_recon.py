"""recon project-root resolution and SessionStart context tests."""

import subprocess
import tempfile
import unittest
from pathlib import Path

from src import recon


class ReconRootTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.child = self.root / "services" / "server"
        self.child.mkdir(parents=True)

    def test_existing_recon_is_found_from_repository_subdirectory(self):
        progress = self.root / "recon" / "PROGRESS.md"
        progress.parent.mkdir()
        progress.write_text(
            "# PROGRESS\n\n## 📍 当前位置\n根项目的真实进度。\n\n## 🎯 目标\n完成。\n",
            encoding="utf-8",
        )

        created = recon.ensure_skeleton(str(self.child))
        context = recon.build_session_context(str(self.child))

        self.assertFalse(created)
        self.assertEqual(recon.resolve_project_root(str(self.child)), self.root.resolve())
        self.assertFalse((self.child / "recon").exists())
        self.assertFalse((self.child / "CLAUDE.md").exists())
        self.assertTrue((self.root / "CLAUDE.md").exists())
        self.assertIn("根项目的真实进度。", context or "")
        self.assertNotIn("空白模板", context or "")

    def test_git_root_is_used_when_recon_does_not_exist_yet(self):
        subprocess.run(
            ["git", "init", "-q", str(self.root)],
            check=True,
            capture_output=True,
            text=True,
        )

        created = recon.ensure_skeleton(str(self.child))

        self.assertTrue(created)
        self.assertEqual(recon.resolve_project_root(str(self.child)), self.root.resolve())
        self.assertTrue((self.root / "recon" / "PROGRESS.md").exists())
        self.assertTrue((self.root / "CLAUDE.md").exists())
        self.assertFalse((self.child / "recon").exists())
        self.assertFalse((self.child / "CLAUDE.md").exists())


if __name__ == "__main__":
    unittest.main()
