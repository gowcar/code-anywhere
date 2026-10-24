"""本地 issue：项目还没有远端时的 issue 流程，推到远端后迁移没关闭的。"""
import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import issues, local_issues


class LocalIssueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "myapp"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        for p in (patch.object(local_issues, "DB", Path(self.tmp.name) / "issues.sqlite3"),
                  patch.object(local_issues, "FILES", Path(self.tmp.name) / "files")):
            p.start()
            self.addCleanup(p.stop)

    def cli(self, *args) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            local_issues.main([*args, "-R", str(self.repo)] if args[0] != "list" or "--all-projects" not in args
                              else list(args))
        return out.getvalue().strip()

    def test_cli_like_gh_issue(self):
        self.assertEqual(self.cli("create", "--title", "首页标题错了", "--label", "agent:todo,area:app"), "local:myapp#1")
        self.cli("comment", "1", "-b", "myapp 接手处理")
        self.cli("edit", "1", "--remove-label", "agent:todo", "--add-label", "agent:doing")
        self.assertIn("agent:doing", self.cli("list"))
        view = self.cli("view", "1", "--comments")
        self.assertIn("myapp 接手处理", view)
        self.cli("close", "1", "--reason", "not_planned")
        self.assertIn("没有符合条件", self.cli("list"))
        self.assertIn("closed", self.cli("list", "--state", "all"))

    def test_issues_page_and_buttons_work_on_local(self):
        self.cli("create", "--title", "登录失败", "--label", "agent:to-verify")
        self.cli("comment", "1", "-b", "验证方法：打开 App 登录")
        found = local_issues.open_issues()
        self.assertEqual(found[0]["references"]["full"], "local:myapp#1")
        self.assertLess(found[0]["project_id"], 0)                   # 负数 = 本地
        self.assertEqual(found[0]["verify_hint"], "打开 App 登录")
        self.assertEqual(issues.owner_cwd(found[0]), str(self.repo.resolve()))
        issues.verify_pass(found[0]["project_id"], 1)
        self.assertEqual(issues.get_issue(found[0]["project_id"], 1)["state"], "closed")

    def test_remote_added_means_auto_migrate_and_switch(self):
        """有远端就一定用远端：再用 local-issue 时先自动迁移没关闭的，然后要求改用 gh。"""
        self.cli("create", "--title", "还没修", "--label", "agent:todo")
        self.cli("create", "--title", "已经关了")
        self.cli("close", "2")
        subprocess.run(["git", "-C", str(self.repo), "remote", "add", "origin", "git@github.com:alan/myapp.git"], check=True)
        created = []
        real_run = subprocess.run

        def fake_run(cmd, *a, **kw):
            if cmd[:3] == ["gh", "issue", "create"]:
                created.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "https://github.com/alan/myapp/issues/7\n", "")
            return real_run(cmd, *a, **kw)
        with patch.object(local_issues.subprocess, "run", fake_run):
            with self.assertRaisesRegex(SystemExit, "请改用 gh issue"):
                self.cli("comment", "1", "-b", "接手")
            self.assertEqual(len(created), 1)                    # 只迁移没关闭的那一条
            self.assertIn("agent:todo", created[0])
            self.assertIn("已迁移到", self.cli("view", "1", "--comments"))   # 查看历史照常可用
            with self.assertRaises(SystemExit):
                self.cli("create", "--title", "新问题")          # 有远端后不再建本地 issue
        self.assertEqual(len(created), 1)                        # 已迁移的不会重复迁移

if __name__ == "__main__":
    unittest.main()
