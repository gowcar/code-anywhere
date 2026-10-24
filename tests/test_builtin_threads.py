"""内置 Thread：code-anywhere（总体协调）和 issue-desk（反馈问题）一定存在。"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src import bot


class BuiltinThreadTests(unittest.TestCase):
    def test_missing_builtins_are_created_existing_kept(self):
        with tempfile.TemporaryDirectory() as root:
            bound = {str(bot.config.PROJECT_ROOT.resolve()): 4699}
            with patch.object(bot, "WORKSPACE_ROOT", Path(root)), \
                 patch.object(bot.projects, "get_topic", side_effect=lambda p: bound.get(p)), \
                 patch.object(bot, "create_project", AsyncMock(return_value=1)) as create:
                asyncio.run(bot.ensure_builtin_projects(object(), 9))
            self.assertEqual([c.args[2] for c in create.await_args_list], ["issue-desk"])   # code-anywhere 已有，不重复建
            desk = Path(root) / "issue-desk"
            self.assertIn("issue 流程还没有配置", (desk / "CLAUDE.md").read_text())
            self.assertTrue((desk / "AGENTS.md").exists())


if __name__ == "__main__":
    unittest.main()
