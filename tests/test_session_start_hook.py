"""SessionStart hook: headless claude launched by other programs must not reach the bot."""

import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "session_start.py"


class _Recorder(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.calls.append((self.path, self.rfile.read(length)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        pass


class SessionStartHookTests(unittest.TestCase):
    def setUp(self):
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Recorder)
        self.server.calls = []
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cwd = tmp.name

    def run_hook(self, **extra):
        env = {k: v for k, v in os.environ.items()
               if k not in ("TMUX_PANE", "CODE_ANYWHERE_SKIP", "CODE_ANYWHERE_BOT_SESSION",
                            "TELEGRAM_CHAT_ID", "CLAUDE_CODE_ENTRYPOINT")}
        env.update(CA_BOT_PORT=str(self.server.server_port), **extra)
        subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"session_id": "s1", "cwd": self.cwd}),
                       text=True, env=env, capture_output=True, timeout=20, check=True)
        return [path for path, _ in self.server.calls]

    def test_headless_claude_from_other_program_is_ignored(self):
        self.assertEqual(self.run_hook(CLAUDE_CODE_ENTRYPOINT="sdk-cli"), [])

    def test_bot_spawned_headless_claude_still_notifies_bot(self):
        calls = self.run_hook(CLAUDE_CODE_ENTRYPOINT="sdk-cli", CODE_ANYWHERE_BOT_SESSION="1")
        self.assertIn("/session_start", calls)

    def test_interactive_terminal_session_notifies_bot(self):
        self.assertIn("/session_start", self.run_hook(CLAUDE_CODE_ENTRYPOINT="cli"))


if __name__ == "__main__":
    unittest.main()
