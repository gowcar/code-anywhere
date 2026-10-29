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
    def test_picks_latest_human_message(self):
        updates = [
            {"update_id": 1, "message": {"chat": {"id": 5, "type": "private"}, "from": {"id": 9}}},
            {"update_id": 2, "message": {"chat": {"id": -100, "type": "supergroup", "title": "G", "is_forum": True},
                                         "from": {"id": 9, "username": "me"}}},
            {"update_id": 3, "message": {"chat": {"id": -100, "type": "supergroup"}, "from": {"id": 7, "is_bot": True}}},
        ]
        self.assertEqual(setup.find_group_message(updates),
                         {"chat_id": -100, "title": "G", "is_forum": True, "private": False, "user_id": 9, "user": "me", "thread_id": None})

    def test_private_message_selects_private_thread_mode(self):
        found = setup.find_group_message([{"update_id": 1, "message": {"chat": {"id": 9, "type": "private"},
                                                                       "from": {"id": 9, "username": "me"}}}])
        self.assertEqual((found["chat_id"], found["private"], found["is_forum"]), (9, True, True))
        found = setup.find_group_message([{"update_id": 2, "message": {"chat": {"id": 9, "type": "private"},
                                                                       "from": {"id": 9}, "message_thread_id": 77}}])
        self.assertEqual(found["thread_id"], 77)   # 识别完删掉这个自动新建的临时 Thread

    def test_private_mode_requires_threaded_mode(self):
        from unittest.mock import patch
        with patch.object(setup, "tg", return_value={"has_topics_enabled": False}):
            self.assertIn("Threaded Mode", setup.check_group("t", 9, 1)[0])
        with patch.object(setup, "tg", return_value={"has_topics_enabled": True}):
            self.assertEqual(setup.check_group("t", 9, 1), [])


class AvatarTests(unittest.TestCase):
    def test_new_bot_gets_avatar_existing_left_alone(self):
        from unittest.mock import patch
        with patch.object(setup, "tg", return_value={"total_count": 0}), patch.object(setup, "ask", return_value=True), \
             patch.object(setup, "tg_upload", return_value=True) as upload:
            setup.ensure_avatar("t", {"id": 1})
        self.assertEqual(upload.call_args.args[1], "setMyProfilePhoto")
        self.assertEqual(upload.call_args.kwargs["photo"], {"type": "static", "photo": "attach://avatar"})
        with patch.object(setup, "tg", return_value={"total_count": 1}), patch.object(setup, "tg_upload") as upload:
            setup.ensure_avatar("t", {"id": 1})
        upload.assert_not_called()
        self.assertTrue(setup.AVATAR.exists())


class UninstallTests(unittest.TestCase):
    def test_never_stops_a_service_installed_elsewhere(self):
        # 临时 HOME 下卸载时，不能按服务名把机器上真正在跑的桥接停掉（2026-10-24 事故）
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as home, patch.object(setup, "HOME", Path(home)), \
             patch.object(setup, "BIN", Path(home) / ".local/bin"), patch.object(setup, "IS_MAC", False), \
             patch.object(setup, "which", return_value=None), patch.object(setup, "run") as run:
            setup.uninstall()
        self.assertFalse(any("systemctl" in c.args[0] for c in run.call_args_list))


class RenderTests(unittest.TestCase):
    def test_service_unit_has_no_placeholders(self):
        unit = setup.render("code-anywhere.service", REPO="/r", PATH="/p")
        self.assertNotIn("{{", unit)
        self.assertIn("ExecStart=/r/.venv/bin/python -m src.main", unit)
        self.assertIn("KillMode=process", unit)

    def test_issue_desk_config_renders_template_completely(self):
        # issue-desk 的规则由 scripts/issue-desk-config 按平台和仓库表生成，不能留占位符
        import os, subprocess, tempfile
        with tempfile.TemporaryDirectory() as home:
            subprocess.run([str(setup.REPO / "scripts" / "issue-desk-config"), "--platform", "github",
                            "--repo", "alan/app|App|area:app|app", "--workspace", f"{home}/ws"],
                           env={**os.environ, "HOME": home, "PATH": "/usr/bin:/bin"}, check=True, capture_output=True)
            text = Path(home, "ws/issue-desk/CLAUDE.md").read_text()
        self.assertNotIn("{{", text)
        self.assertIn(f"{home}/.codex/AGENTS.md", text)
        self.assertIn("gh issue create -R", text)
        self.assertIn("| App | `alan/app` | `area:app` | app |", text)


class DependencyTests(unittest.TestCase):
    def test_package_manager_detection(self):
        only = lambda *names: (lambda n: n in names)
        self.assertEqual(setup.package_install_command(["tmux"], False, only("dnf", "yum")), "sudo dnf install -y tmux")
        self.assertTrue(setup.package_install_command(["tmux", "git"], False, only("apt-get")).endswith("install -y tmux git"))
        self.assertEqual(setup.package_install_command(["tmux"], True, only("brew")), "brew install tmux")
        self.assertIsNone(setup.package_install_command(["tmux"], True, only()))
        self.assertIsNone(setup.package_install_command(["tmux"], False, only()))

    def test_node_picks_latest_of_major(self):
        index = [{"version": "v23.1.0"}, {"version": "v22.23.4"}, {"version": "v22.1.0"}]
        self.assertEqual(setup.node_dist_name(index, "linux", "x86_64"),
                         ("v22.23.4", "node-v22.23.4-linux-x64.tar.xz"))
        self.assertEqual(setup.node_dist_name(index, "darwin", "arm64")[1], "node-v22.23.4-darwin-arm64.tar.xz")


if __name__ == "__main__":
    unittest.main()
