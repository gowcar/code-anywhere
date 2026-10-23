"""Markdown → Telegram HTML 渲染 + 智能分段

Telegram 支持的 HTML 标签：
<b>, <i>, <code>, <pre>, <a href>, <blockquote>, <s>, <u>
<blockquote expandable> — 可折叠引用块
"""

import html
from pathlib import Path
import re
import logging

log = logging.getLogger("code-anywhere.renderer")

# Telegram 单条消息最大长度
TG_MAX_LEN = 4096


def md_to_tg_html(text: str) -> str:
    """将 Markdown 转为 Telegram 支持的 HTML 子集

    处理顺序：
    1. 提取代码块/表格 → 占位符（保护内部不被转义）
    2. HTML 转义剩余文本
    3. 逐步转换 Markdown 语法 → HTML 标签
    4. 还原占位符
    """

    # 先提取代码块，避免内部被转义/转换
    code_blocks: list[str] = []

    def _stash_code(m):
        lang = m.group(1) or ""
        code = m.group(2)
        idx = len(code_blocks)
        escaped = html.escape(code)
        if lang:
            code_blocks.append(f'<pre><code class="language-{lang}">{escaped}</code></pre>')
        else:
            code_blocks.append(f'<pre>{escaped}</pre>')
        return f"\x00CODEBLOCK{idx}\x00"

    text = re.sub(r'```(\w*)\n(.*?)```', _stash_code, text, flags=re.DOTALL)

    # Markdown 表格 → <pre> 等宽格式（TG 不支持 <table>）
    def _format_table(m):
        lines = m.group(0).strip().split('\n')
        rows = []
        for line in lines:
            if re.match(r'^\|[\s\-:|]+$', line.strip()):
                continue
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            rows.append(cells)
        if not rows:
            return m.group(0)

        def _display_width(s):
            """中文字符算 2 宽度，英文算 1"""
            w = 0
            for c in s:
                w += 2 if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f' else 1
            return w

        ncols = max(len(r) for r in rows)
        col_widths = [0] * ncols
        for row in rows:
            for i, cell in enumerate(row):
                if i < ncols:
                    col_widths[i] = max(col_widths[i], _display_width(cell))
        formatted = []
        for ri, row in enumerate(rows):
            parts = []
            for i in range(ncols):
                cell = row[i] if i < len(row) else ""
                pad = col_widths[i] - _display_width(cell)
                parts.append(cell + ' ' * pad)
            formatted.append('  '.join(parts))
            if ri == 0:
                formatted.append('  '.join('─' * w for w in col_widths))
        idx = len(code_blocks)
        code_blocks.append(f'<pre>{html.escape(chr(10).join(formatted))}</pre>')
        return f"\x00CODEBLOCK{idx}\x00"

    text = re.sub(r'(?:^\|.+\|$\n?){2,}', _format_table, text, flags=re.MULTILINE)

    # 转义剩余 HTML 特殊字符
    text = html.escape(text)

    # 行内代码 `...` → <code>...</code>
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)

    # 粗体 **...** → <b>...</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)

    # 斜体 *...* → <i>...</i>（避免匹配列表项 *）
    text = re.sub(r'(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)', r'<i>\1</i>', text)

    # 删除线 ~~...~~ → <s>...</s>
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)

    # 链接 [text](url) → <a href="url">text</a>
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)

    # 标题 # → 粗体 + 下划线
    text = re.sub(r'^#{1,6}\s+(.+)$', r'<b><u>\1</u></b>', text, flags=re.MULTILINE)

    # 引用 > → blockquote（html.escape 后 > 变成 &gt;）
    def _blockquote(m):
        lines = m.group(0).split('\n')
        content = '\n'.join(re.sub(r'^&gt;\s?', '', line) for line in lines)
        return f'<blockquote>{content}</blockquote>'
    text = re.sub(r'^(?:&gt;.*\n?)+', _blockquote, text, flags=re.MULTILINE)

    # 无序列表 - / * → •
    text = re.sub(r'^[\-\*]\s+', '• ', text, flags=re.MULTILINE)

    # 有序列表保留数字
    text = re.sub(r'^(\d+)\.\s+', r'\1. ', text, flags=re.MULTILINE)

    # 分隔线 --- / *** → ——
    text = re.sub(r'^[-\*]{3,}\s*$', '———————————', text, flags=re.MULTILINE)

    # 还原代码块
    for i, block in enumerate(code_blocks):
        text = text.replace(f'\x00CODEBLOCK{i}\x00', block)

    return text.strip()


def split_user_message(text: str) -> list[str]:
    """Plain-text chunks; simplify transport headers without changing the body."""
    prefix = "› "
    agent = re.match(
        r'\A\[Agent message [A-Za-z0-9_.:-]{1,128} from ([^\[\]\r\n]{1,80})\]\r?\n',
        text,
    )
    if agent:
        sender = agent.group(1)
        if sender.lower() in {"agent", "unknown", "bot"}:
            sender = "来源未识别（旧客户端）"
        prefix = f"⇢ from {sender}\n\n"
        text = text[agent.end():]
    budget = 4000 - len(prefix.encode("utf-16-le")) // 2
    chunks = []
    start = 0
    units = 0
    for index, char in enumerate(text):
        width = 2 if ord(char) > 0xFFFF else 1
        if units + width > budget:
            chunks.append(prefix + text[start:index])
            start = index
            units = 0
        units += width
    if start < len(text):
        chunks.append(prefix + text[start:])
    return chunks


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
        "WebSearch": "🌐", "WebFetch": "🌐",
    }
    icon = icons.get(tool_name, "🔧")

    name = tool_name.lower()
    if name in ("bash", "shell"):
        cmd = tool_input.get("command", "")
        return f"{icon} <code>{html.escape(cmd[:150])}</code>"
    elif name in ("read", "edit", "write"):
        path = tool_input.get("file_path", tool_input.get("filePath", ""))
        return f"{icon} <code>{html.escape(path)}</code>"
    elif name in ("glob", "grep"):
        pattern = tool_input.get("pattern", "")
        return f"{icon} <code>{html.escape(pattern[:100])}</code>"
    elif name in ("websearch", "webfetch"):
        query = tool_input.get("query", tool_input.get("searchTerm", tool_input.get("url", "")))
        return f"{icon} <code>{html.escape(query[:100])}</code>"
    else:
        import json as _json
        summary = _json.dumps(tool_input, ensure_ascii=False)[:80]
        return f"{icon} {tool_name}: <code>{html.escape(summary)}</code>"


def tool_card_line(tool_name: str, tool_input: dict, width: int = 26) -> str:
    """状态卡里的一行“在干什么”（HTML）：手机上一行放得下，优先用工具自带的说明。
    命令原文前面的 cd 前缀、长路径都没有信息量，在手机上一条要折三行（2026-10-23 用户反馈）。"""
    import re as _re

    def cut(text: str) -> str:
        text = " ".join(text.split())
        return text if len(text) <= width else text[:width - 1] + "…"

    name = tool_name.lower()
    short = tool_name.split("__")[-1]
    if name in ("bash", "shell", "exec_command", "local_shell"):
        cmd = tool_input.get("command", "")
        if isinstance(cmd, list):
            cmd = " ".join(map(str, cmd[-1:] if cmd[:2] in (["bash", "-lc"], ["bash", "-c"]) else cmd))
        if tool_input.get("description"):
            return f"⚡ {html.escape(cut(tool_input['description']))}"
        cmd = _re.sub(r"^\s*cd\s+\S+\s*(;|&&)\s*", "", str(cmd))
        return f"⚡ <code>{html.escape(cut(cmd.splitlines()[0] if cmd else ''))}</code>"
    if name in ("read", "edit", "write", "multiedit", "notebookedit"):
        verb = {"read": "读", "write": "写"}.get(name, "改")
        path = str(tool_input.get("file_path", tool_input.get("filePath", "")))
        return f"{'📖' if name == 'read' else '✏️'} {verb} <code>{html.escape(cut(Path(path).name or path))}</code>"
    if name in ("glob", "grep"):
        return f"🔎 搜 <code>{html.escape(cut(str(tool_input.get('pattern', ''))))}</code>"
    if name in ("websearch", "webfetch"):
        return f"🌐 {html.escape(cut(str(tool_input.get('query') or tool_input.get('url') or '')))}"
    if name in ("task", "agent"):
        return f"🤖 {html.escape(cut(str(tool_input.get('description') or '子任务')))}"
    if short.startswith("browser_"):
        target = tool_input.get("url") or tool_input.get("element") or tool_input.get("text") or ""
        return f"🖥 {html.escape(cut(short[8:] + (' ' + str(target) if target else '')))}"
    return f"🔧 {html.escape(cut(short))}"


async def send_ephemeral(bot, chat_id: int, text: str, ttl: float = 5.0,
                         message_thread_id: int | None = None,
                         parse_mode: str = "HTML"):
    """发送过程性消息，ttl 秒后自动删除

    用于工具调用通知、终端注入确认等非最终内容。
    """
    import asyncio
    try:
        msg = await bot.send_message(
            chat_id=chat_id,
            text=text,
            message_thread_id=message_thread_id,
            parse_mode=parse_mode,
        )

        async def _delete_later():
            await asyncio.sleep(ttl)
            try:
                await msg.delete()
            except Exception:
                pass

        asyncio.create_task(_delete_later())
        return msg
    except Exception as e:
        log.warning(f"[ephemeral] 发送失败: {e}")
        return None
