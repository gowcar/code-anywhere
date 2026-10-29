"""Project-to-tmux-session mapping regressions."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src import bot


class TmuxProjectSessionTests(unittest.TestCase):
    def test_human_facing_name_uses_project_basename(self):
        with tempfile.TemporaryDirectory(prefix="code.anywhere ") as tmp:
            self.assertEqual(
                bot._tmux_project_session_base(tmp),
                Path(tmp).name.replace(".", "-").replace(" ", "-"),
            )

    def test_reuses_session_owned_by_same_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows = f"kin\t\nmy-project\t{Path(tmp).resolve()}\n"
            with patch.object(bot.backends, "command", AsyncMock(return_value=rows)):
                result = asyncio.run(bot._tmux_project_session(tmp))
            self.assertEqual(result, ("my-project", True))

    def test_does_not_take_over_unowned_same_name_session(self):
        with tempfile.TemporaryDirectory(prefix="same-name-") as parent:
            project = Path(parent) / "app"
            project.mkdir()
            rows = "app\t\napp-2\t/other/app\n"
            with patch.object(bot.backends, "command", AsyncMock(return_value=rows)):
                result = asyncio.run(bot._tmux_project_session(str(project)))
            self.assertEqual(result, ("app-3", False))

    def test_no_tmux_server_creates_project_named_session(self):
        with tempfile.TemporaryDirectory(prefix="fresh-project-") as tmp:
            command = AsyncMock(side_effect=RuntimeError("no server running"))
            with patch.object(bot.backends, "command", command):
                result = asyncio.run(bot._tmux_project_session(tmp))
            self.assertEqual(result, (Path(tmp).name, False))


if __name__ == "__main__":
    unittest.main()
