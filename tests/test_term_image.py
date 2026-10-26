"""终端截图渲染：ANSI 颜色解析、宽字符、缺字形替换、输出 PNG。"""
import unittest

from src import term_image


class ParseTests(unittest.TestCase):
    def test_256_and_truecolor_and_reset(self):
        rows = term_image.parse("\x1b[38;5;196mR\x1b[39mN\x1b[38;2;1;2;3mT\x1b[0m")
        styles = [s["fg"] for _, s in rows[0]]
        self.assertEqual(styles, [term_image.color_256(196), None, (1, 2, 3)])

    def test_missing_glyphs_substituted_and_trailing_blank_rows_dropped(self):
        rows = term_image.parse("⎿ ok\n⏵⏵ on\n\n\n")
        self.assertEqual(["".join(ch for ch, _ in r) for r in rows], ["└ ok", "▸▸ on"])

    def test_osc_hyperlinks_stripped_with_either_terminator(self):
        st = "\x1b]8;id=1;https://a.example/release\x1b\\https://a.example/release\x1b]8;;\x1b\\ OK"
        bel = "\x1b]8;;https://b.example\x07link\x1b]8;;\x07"
        rows = term_image.parse(st + "\n" + bel)
        self.assertEqual(["".join(ch for ch, _ in r) for r in rows], ["https://a.example/release OK", "link"])

    def test_cjk_font_has_chinese_glyphs(self):
        # 退回无中文字形的等宽字体时，所有汉字都会画成同一个方框
        font = term_image._font("cjk")
        self.assertNotEqual(font.getmask("中").getbbox(), font.getmask("文").getbbox())

    def test_wide_chars_take_two_cells(self):
        self.assertEqual(term_image._width("中"), 2)
        self.assertEqual(term_image._width("a"), 1)


class RenderTests(unittest.TestCase):
    def test_png_output(self):
        png = term_image.render("\x1b[1mhello\x1b[0m 世界\n\x1b[7mreverse\x1b[27m")
        self.assertTrue(png.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
