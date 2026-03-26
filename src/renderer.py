"""Markdown → Telegram HTML 渲染 + 智能分段

Telegram 支持的 HTML 标签：
<b>, <i>, <code>, <pre>, <a href>, <blockquote>, <s>, <u>
"""

import html
import re
import logging

log = logging.getLogger("code-anywhere.renderer")

# Telegram 单条消息最大长度
TG_MAX_LEN = 4096


def md_to_tg_html(text: str) -> str:
    """将 Markdown 转为 Telegram 支持的 HTML 子集"""
    # 先 escape HTML 特殊字符
    text = html.escape(text)

    # 代码块 ```...``` → <pre>...</pre>
    text = re.sub(
        r'```(\w*)\n(.*?)```',
        lambda m: f'<pre>{m.group(2)}</pre>',
        text, flags=re.DOTALL
    )

    # 行内代码 `...` → <code>...</code>
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)

    # 粗体 **...** → <b>...</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)

    # 斜体 *...* → <i>...</i>
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)

    # 删除线 ~~...~~ → <s>...</s>
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)

    # 链接 [text](url) → <a href="url">text</a>
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)

    # 标题 # → 粗体
    text = re.sub(r'^#{1,6}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)

    # 引用 > → blockquote
    lines = text.split('\n')
    result = []
    in_quote = False
    for line in lines:
        if line.startswith('&gt; '):
            if not in_quote:
                result.append('<blockquote>')
                in_quote = True
            result.append(line[5:])
        else:
            if in_quote:
                result.append('</blockquote>')
                in_quote = False
            result.append(line)
    if in_quote:
        result.append('</blockquote>')
    text = '\n'.join(result)

    # 列表 - → •
    text = re.sub(r'^- ', '• ', text, flags=re.MULTILINE)

    return text.strip()


def split_message(text: str, render: bool = True) -> list[tuple[str, str]]:
    """将长文本拆分为 ≤4096 字符的段，返回 [(html, raw), ...]"""
    if render:
        full_html = md_to_tg_html(text)
    else:
        full_html = text

    if len(full_html) <= TG_MAX_LEN:
        return [(full_html, text)]

    # 按双换行分段
    chunks = []
    blocks = text.split("\n\n")
    current_raw = ""

    for block in blocks:
        test_raw = current_raw + ("\n\n" if current_raw else "") + block
        test_html = md_to_tg_html(test_raw) if render else test_raw
        if len(test_html) <= TG_MAX_LEN:
            current_raw = test_raw
        else:
            if current_raw:
                h = md_to_tg_html(current_raw) if render else current_raw
                chunks.append((h, current_raw))
            # 单块超长 → 硬切
            if len(md_to_tg_html(block) if render else block) > TG_MAX_LEN:
                for i in range(0, len(block), TG_MAX_LEN - 100):
                    sub = block[i:i + TG_MAX_LEN - 100]
                    chunks.append((sub, sub))
                current_raw = ""
            else:
                current_raw = block

    if current_raw:
        h = md_to_tg_html(current_raw) if render else current_raw
        chunks.append((h, current_raw))

    return chunks


def format_tool_use(tool_name: str, tool_input: dict) -> str:
    """格式化工具调用的展示文本"""
    icons = {
        "Bash": "⚡", "Read": "📖", "Edit": "✏️", "Write": "📝",
        "Glob": "🔍", "Grep": "🔎", "Task": "🧩", "Agent": "🤖",
    }
    icon = icons.get(tool_name, "🔧")

    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return f"{icon} <code>{html.escape(cmd[:150])}</code>"
    elif tool_name in ("Read", "Edit", "Write"):
        path = tool_input.get("file_path", "")
        return f"{icon} <code>{html.escape(path)}</code>"
    elif tool_name in ("Glob", "Grep"):
        pattern = tool_input.get("pattern", "")
        return f"{icon} <code>{html.escape(pattern[:100])}</code>"
    else:
        import json
        summary = json.dumps(tool_input, ensure_ascii=False)[:80]
        return f"{icon} {tool_name}: <code>{html.escape(summary)}</code>"
