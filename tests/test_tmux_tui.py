import curses
import importlib.util
from importlib.machinery import SourceFileLoader
import os
from pathlib import Path
from unittest import TestCase, mock


TOOL = Path(__file__).resolve().parents[1] / "tools" / "tmux-tui"


def load_tool():
    loader = SourceFileLoader("tmux_tui_for_test", str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TerminfoTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    def test_supported_term_is_kept(self):
        with mock.patch.dict(os.environ, {"TERM": "known-term"}), \
             mock.patch.object(curses, "setupterm") as setupterm:
            self.assertEqual(self.tool.ensure_terminfo(), "known-term")
        setupterm.assert_called_once_with(term="known-term")

    def test_unknown_term_falls_back_without_traceback(self):
        missing = curses.error("setupterm: could not find terminal")
        with mock.patch.dict(os.environ, {"TERM": "xterm-ghostty"}), \
             mock.patch.object(curses, "setupterm", side_effect=[missing, None]) as setupterm:
            self.assertEqual(self.tool.ensure_terminfo(), "xterm-256color")
            self.assertEqual(os.environ["TERM"], "xterm-256color")
        self.assertEqual(
            setupterm.call_args_list,
            [mock.call(term="xterm-ghostty"), mock.call(term="xterm-256color")],
        )

    def test_missing_all_terminfo_entries_has_concise_error(self):
        missing = curses.error("setupterm: could not find terminal")
        with mock.patch.dict(os.environ, {"TERM": "unknown-client"}), \
             mock.patch.object(curses, "setupterm", side_effect=missing):
            with self.assertRaisesRegex(SystemExit, "TERM=unknown-client"):
                self.tool.ensure_terminfo()
