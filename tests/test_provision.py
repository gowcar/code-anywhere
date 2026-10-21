"""provision/setup.py pure helpers; no network, no filesystem outside temp dirs."""
import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "provision_setup", Path(__file__).resolve().parent.parent / "provision" / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

BODY = "## 规则\n- 一句话 + md"


class MergeBlockTests(unittest.TestCase):
    def test_empty_file_gets_block(self):
        merged = setup.merge_block("", BODY)
        self.assertTrue(merged.startswith(setup.BEGIN) and merged.rstrip().endswith(setup.END))

    def test_user_content_preserved_and_rerun_is_stable(self):
        merged = setup.merge_block("# 我的规则\n自己写的\n", BODY)
        self.assertIn("自己写的", merged)
        self.assertEqual(setup.merge_block(merged, BODY), merged)

    def test_block_replaced_in_place(self):
        merged = setup.merge_block(f"前\n{setup.BEGIN}\n旧\n{setup.END}\n后\n", BODY)
        self.assertEqual(merged, f"前\n{setup.BEGIN}\n{BODY}\n{setup.END}\n后\n")

    def test_unmarked_copy_is_adopted_not_duplicated(self):
        merged = setup.merge_block(f"# 全局规则\n\n{BODY}\n", BODY)
        self.assertEqual(merged.count("一句话 + md"), 1)
        self.assertIn(setup.BEGIN, merged)


class EnvTests(unittest.TestCase):
    def test_update_in_place_including_commented_key(self):
        text = "# Token\nBOT_TOKEN=old\n# CHAT_ID=\nALLOWED_USERS=0\n"
        updated = setup.update_env(text, {"BOT_TOKEN": "new", "CHAT_ID": "-100", "BOT_PORT": "5000"})
        self.assertEqual(updated, "# Token\nBOT_TOKEN=new\nCHAT_ID=-100\nALLOWED_USERS=0\nBOT_PORT=5000\n")


class GroupMessageTests(unittest.TestCase):
    def test_picks_latest_human_group_message(self):
        updates = [
            {"update_id": 1, "message": {"chat": {"id": 5, "type": "private"}, "from": {"id": 9}}},
            {"update_id": 2, "message": {"chat": {"id": -100, "type": "supergroup", "title": "G", "is_forum": True},
                                         "from": {"id": 9, "username": "me"}}},
            {"update_id": 3, "message": {"chat": {"id": -100, "type": "supergroup"}, "from": {"id": 7, "is_bot": True}}},
        ]
        self.assertEqual(setup.find_group_message(updates),
                         {"chat_id": -100, "title": "G", "is_forum": True, "user_id": 9, "user": "me"})

    def test_none_without_group_message(self):
        self.assertIsNone(setup.find_group_message([{"update_id": 1, "message": {"chat": {"type": "private"}}}]))


class RenderTests(unittest.TestCase):
    def test_service_unit_has_no_placeholders(self):
        unit = setup.render("code-anywhere.service", REPO="/r", PATH="/p")
        self.assertNotIn("{{", unit)
        self.assertIn("ExecStart=/r/.venv/bin/python -m src.main", unit)

    def test_issue_desk_template_uses_home(self):
        text = setup.render("issue-desk.CLAUDE.md", HOME="/h")
        self.assertIn("/h/.codex/AGENTS.md", text)
        self.assertNotIn("{{", text)


if __name__ == "__main__":
    unittest.main()
