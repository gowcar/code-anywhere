"""/issues summary formatting; no network."""
import unittest

from src import issues


def issue(iid, labels, title="t", repo="g/repo", created="2026-10-22"):
    return {"iid": iid, "labels": labels, "title": title, "created_at": created,
            "web_url": f"https://h/{repo}/-/issues/{iid}", "references": {"full": f"{repo}#{iid}"}}


class SummaryTests(unittest.TestCase):
    def test_groups_by_state_in_priority_order_and_skips_unlabeled(self):
        text = issues.format_summary([
            issue(1, ["agent:todo"]), issue(2, ["agent:to-verify", "area:ios"]),
            issue(3, ["area:web"]), issue(4, ["agent:doing"])])
        self.assertIn("共 3 个未关闭", text)
        self.assertLess(text.index("待验证"), text.index("处理中"))
        self.assertLess(text.index("处理中"), text.index("待处理"))
        self.assertNotIn("repo#3", text)
        self.assertIn('<a href="https://h/g/repo/-/issues/2">repo#2</a>', text)

    def test_title_is_html_escaped(self):
        self.assertIn("a &lt;b&gt; &amp; c", issues.format_summary([issue(1, ["agent:todo"], "a <b> & c")]))

    def test_empty(self):
        self.assertEqual(issues.format_summary([]), "📋 没有未关闭的 agent issue")


if __name__ == "__main__":
    unittest.main()
