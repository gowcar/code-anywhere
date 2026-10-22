"""/issues summary formatting; no network."""
import unittest

from src import issues


def issue(iid, labels, title="t", repo="g/repo", created="2026-10-22"):
    return {"iid": iid, "labels": labels, "title": title, "created_at": created,
            "web_url": f"https://h/{repo}/-/issues/{iid}", "references": {"full": f"{repo}#{iid}"}}


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.found = [issue(1, ["agent:todo"], repo="g/digital-buddy"),
                      dict(issue(2, ["agent:to-verify", "area:ios"], "修键盘", repo="g/digital-buddy-ios"), verify_hint="装 0.2.67"),
                      issue(3, ["area:web"], repo="g/digital-buddy"),
                      issue(4, ["agent:doing"], "a <b> & c", repo="g/digital-buddy-ios")]
        for i in self.found:
            i["project_id"] = 9

    def test_aliases_drop_shared_prefix(self):
        self.assertEqual(issues.aliases(self.found), {"g/digital-buddy": "buddy", "g/digital-buddy-ios": "ios"})

    def test_overview_buttons_match_lines_in_priority_order(self):
        text, kb = issues.overview(self.found)
        self.assertIn("共 3 个", text)                                # 不带 agent 状态的不列
        labels = [b.text for row in kb.inline_keyboard for b in row]
        self.assertEqual(labels, ["🧪 ios#2", "🔧 ios#4", "📥 buddy#1", "🔄 刷新"])
        self.assertLess(text.index("ios#2"), text.index("ios#4"))
        self.assertIn("a &lt;b&gt; &amp; c", text)
        datas = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertTrue(all(len(d.encode()) <= 64 for d in datas))

    def test_detail_actions_only_for_this_issue_with_navigation(self):
        text, kb = issues.detail(self.found, 9, 2)
        self.assertIn("验证：装 0.2.67", text)
        rows = [[b.callback_data for b in row] for row in kb.inline_keyboard]
        self.assertEqual(rows, [["is:pass:9:2", "is:fail:9:2"], ["is:list:0:0", "is:open:9:4"]])
        text, kb = issues.detail(self.found, 9, 1)                     # 最后一个：没有“下一个”
        rows = [[b.callback_data for b in row] for row in kb.inline_keyboard]
        self.assertEqual(rows, [["is:drop:9:1"], ["is:open:9:4", "is:list:0:0"]])
        self.assertIsNone(issues.detail(self.found, 9, 99))

    def test_next_to_verify(self):
        self.assertEqual(issues.next_to_verify(self.found)["iid"], 2)
        self.assertEqual(issues.overview([])[0], "📋 没有未关闭的 agent issue")


class VerifyHintTests(unittest.TestCase):
    def test_latest_marked_line_wins_and_markdown_is_stripped(self):
        notes = [{"created_at": "1", "body": "- 如何验证：旧说明"},
                 {"created_at": "3", "body": "已发布\n**验证方法**：装 0.2.67，需重启 App"},
                 {"created_at": "2", "body": "验证方式: 中间说明"}]
        self.assertEqual(issues.verify_hint(notes), "装 0.2.67，需重启 App")

    def test_none_without_marker(self):
        self.assertIsNone(issues.verify_hint([{"created_at": "1", "body": "修好了"}]))

    def test_detail_warns_when_hint_missing(self):
        item = dict(issue(1, ["agent:to-verify"]), project_id=9)
        self.assertIn("还没写验证方法", issues.detail([item], 9, 1)[0])



class RepoTests(unittest.TestCase):
    def test_repo_path_parsing(self):
        self.assertEqual(issues.repo_path("gitlab@git.garena.com:shopee/platform/digital-buddy-ios.git"),
                         "shopee/platform/digital-buddy-ios")
        self.assertEqual(issues.repo_path("https://git.garena.com/kong/seatalk-workspace.git"), "kong/seatalk-workspace")


if __name__ == "__main__":
    unittest.main()
