"""/abort 的屏幕解析：只在看到对应状态时才按键，丢弃的排队消息原文可回报。屏幕取自真实 CLI。"""
import unittest

from src import abort

CLAUDE_RUNNING = """❯ 排队的第二条消息 QUEUED-MARK
✢ Proofing… (8s · ↓ 238 tokens)
────────────────────────
❯ Press up to edit queued messages
────────────────────────
  -- INSERT -- ⏵⏵ bypass permissions on"""
CLAUDE_PULLED = """────────────────────────
❯ 排队消息 A
  排队消息 B
────────────────────────
  -- INSERT --"""
CLAUDE_IDLE = """● 收到
✻ Churned for 2s · done 2:16 AM
────────────────────────
❯ 
────────────────────────"""
CODEX_RUNNING = """◦ Working (16s • esc to interrupt) · 1 background terminal running
• Messages to be submitted after next tool call
  ↳ QUEUED-A
› run the shell command
› QUEUED-A
  QUEUED-B
  GPT-6-Sol medium · ~/workspace/x"""


class ScreenTests(unittest.TestCase):
    def test_claude_states(self):
        self.assertTrue(abort.claude_running(CLAUDE_RUNNING))
        self.assertTrue(abort.claude_has_queue(CLAUDE_RUNNING))
        self.assertEqual(abort.claude_input(CLAUDE_RUNNING), "")  # 占位提示不算内容
        self.assertEqual(abort.claude_input(CLAUDE_PULLED), "排队消息 A\n排队消息 B")
        self.assertFalse(abort.claude_running(CLAUDE_IDLE))  # “✻ Churned for 2s”是完成态
        self.assertEqual(abort.claude_input(CLAUDE_IDLE), "")

    def test_codex_states(self):
        self.assertTrue(abort.codex_running(CODEX_RUNNING))
        self.assertEqual(abort.codex_input(CODEX_RUNNING), "QUEUED-A\nQUEUED-B")  # 取最后一个 › 输入框
        self.assertEqual(abort.codex_input("› Ask Codex to do anything\n  GPT · x"), "")
        self.assertFalse(abort.codex_running("› Ask Codex to do anything"))


if __name__ == "__main__":
    unittest.main()
