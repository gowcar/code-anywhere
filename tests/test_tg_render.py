"""Markdown → TG HTML：表格渲染成卡片。"""
import unittest


class TableCardTests(unittest.TestCase):
    def test_table_becomes_cards_with_inline_markdown(self):
        from src.renderer import md_to_tg_html
        out = md_to_tg_html("| 项目 | 说明 | 状态 |\n|---|---|---|\n| **zip** | 网站 `m.x/zip` | ✅ 保留 |\n")
        self.assertIn("<b>zip</b>\n说明：网站 <code>m.x/zip</code>\n状态：✅ 保留", out)
        self.assertNotIn("<pre>", out)
        self.assertNotIn("**", out)

    def test_two_column_table_is_one_line_per_row(self):
        from src.renderer import md_to_tg_html
        self.assertEqual(md_to_tg_html("| 命令 | 作用 |\n|---|---|\n| ca status | 看状态 |\n"), "<b>ca status</b>：看状态")


if __name__ == "__main__":
    unittest.main()
