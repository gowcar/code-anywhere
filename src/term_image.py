"""把 tmux 窗格（带 ANSI 颜色）画成 PNG：手机上看终端，比一大段被折行的文本好读得多。

按单元格排版：东亚宽字符占两格；英文和框线用等宽字体，中文用 CJK 字体，找不到就退回等宽字体。
"""
import io
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_SIZE = 26
BG = (30, 31, 34)
FG = (212, 212, 212)
_MONO = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/System/Library/Fonts/Menlo.ttc",
         "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"]
_MONO_BOLD = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", "/System/Library/Fonts/Menlo.ttc"]
_CJK = [str(Path.home() / ".local/share/fonts/NotoSansCJK-Regular.ttc"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc", "/System/Library/Fonts/PingFang.ttc",
        # macOS 13 起苹方挪进 AssetsV2（路径随机器而变），系统目录里固定存在的是冬青黑体 / 华文黑体
        "/System/Library/Fonts/Hiragino Sans GB.ttc", "/System/Library/Fonts/STHeiti Light.ttc"]
_BASIC = [(0, 0, 0), (205, 49, 49), (13, 188, 121), (229, 229, 16), (36, 114, 200), (188, 63, 188),
          (17, 168, 205), (229, 229, 229), (102, 102, 102), (241, 76, 76), (35, 209, 139), (245, 245, 67),
          (59, 142, 234), (214, 112, 214), (41, 184, 219), (255, 255, 255)]
# Claude/Codex 界面常用、但等宽字体里没有的符号：换成形状相近的字符，免得显示成方框
_SUBSTITUTE = str.maketrans({"⎿": "└", "⏵": "▸", "⏸": "‖", "⏺": "●", "✻": "✱", "✢": "✱", "✶": "✱", "✽": "✱",
                             "⧉": "□", "↯": "↓"})
_SGR = re.compile(r"\x1b\[([0-9;]*)m")
# OSC 有两种结尾：BEL，或 ST（ESC \）——Claude Code 的超链接（OSC 8）用的是后者
_OTHER_ESC = re.compile(r"\x1b\[[0-9;?]*[A-Za-ln-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


@lru_cache
def _font(kind: str):
    candidates = {"mono": _MONO, "bold": _MONO_BOLD, "cjk": _CJK}[kind]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, FONT_SIZE)
    return ImageFont.load_default(size=FONT_SIZE) if kind != "cjk" else _font("mono")


def color_256(n: int) -> tuple[int, int, int]:
    if n < 16:
        return _BASIC[n]
    if n < 232:
        n -= 16
        steps = [0, 95, 135, 175, 215, 255]
        return steps[n // 36], steps[n // 6 % 6], steps[n % 6]
    gray = 8 + (n - 232) * 10
    return gray, gray, gray


def _apply_sgr(params: str, style: dict) -> None:
    codes = [int(c) if c else 0 for c in params.split(";")] if params else [0]
    i = 0
    while i < len(codes):
        c = codes[i]
        if c == 0:
            style.update(fg=None, bg=None, bold=False, dim=False, reverse=False)
        elif c == 1:
            style["bold"] = True
        elif c == 2:
            style["dim"] = True
        elif c == 22:
            style.update(bold=False, dim=False)
        elif c == 7:
            style["reverse"] = True
        elif c == 27:
            style["reverse"] = False
        elif 30 <= c <= 37 or 90 <= c <= 97:
            style["fg"] = _BASIC[c - 30 if c < 90 else c - 82]
        elif 40 <= c <= 47 or 100 <= c <= 107:
            style["bg"] = _BASIC[c - 40 if c < 100 else c - 92]
        elif c == 39:
            style["fg"] = None
        elif c == 49:
            style["bg"] = None
        elif c in (38, 48) and i + 1 < len(codes):
            key = "fg" if c == 38 else "bg"
            if codes[i + 1] == 5 and i + 2 < len(codes):
                style[key] = color_256(codes[i + 2])
                i += 2
            elif codes[i + 1] == 2 and i + 4 < len(codes):
                style[key] = tuple(codes[i + 2:i + 5])
                i += 4
        i += 1


def parse(ansi: str) -> list[list[tuple[str, dict]]]:
    """ANSI 文本 → 每行的 (字符, 样式) 列表。"""
    rows, style = [], {"fg": None, "bg": None, "bold": False, "dim": False, "reverse": False}
    for line in ansi.split("\n"):
        line = _OTHER_ESC.sub("", line).translate(_SUBSTITUTE)
        row, pos = [], 0
        for match in _SGR.finditer(line):
            row += [(ch, dict(style)) for ch in line[pos:match.start()]]
            _apply_sgr(match.group(1), style)
            pos = match.end()
        row += [(ch, dict(style)) for ch in line[pos:]]
        rows.append(row)
    while rows and not "".join(ch for ch, _ in rows[-1]).strip():
        rows.pop()
    return rows


def _width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def render(ansi: str) -> bytes:
    rows = parse(ansi)
    mono = _font("mono")
    cell_w = round(mono.getlength("M"))
    cell_h = round(FONT_SIZE * 1.35)
    cols = max((sum(_width(ch) for ch, _ in row) for row in rows), default=1)
    pad = 24
    image = Image.new("RGB", (pad * 2 + cols * cell_w, pad * 2 + max(len(rows), 1) * cell_h), BG)
    draw = ImageDraw.Draw(image)
    for y, row in enumerate(rows):
        x = 0
        for ch, style in row:
            w = _width(ch)
            fg, bg = style["fg"] or FG, style["bg"]
            if style["reverse"]:
                fg, bg = bg or BG, fg
            if style["dim"]:
                fg = tuple(int(c * 0.6 + BG[i] * 0.4) for i, c in enumerate(fg))
            left, top = pad + x * cell_w, pad + y * cell_h
            if bg:
                draw.rectangle([left, top, left + w * cell_w, top + cell_h], fill=bg)
            if ch.strip():
                font = _font("cjk") if w == 2 else (_font("bold") if style["bold"] else mono)
                draw.text((left, top + (cell_h - FONT_SIZE) // 2), ch, font=font, fill=fg)
            x += w
    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()
