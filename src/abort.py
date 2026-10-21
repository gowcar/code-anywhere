"""/abort：中断当前回合，并丢弃已发出但还没被处理的排队消息。

两种 CLI 的实测行为（2026-10-22）：
- Claude：排队消息在 Esc 中断后会被立刻提交。先按 ↑ 把排队消息取回输入框并清空，再 Esc 中断；
  vim 模式下第一次 Esc 只是退出插入模式，要再按一次，中断后按 i 回到插入模式，
  否则下一条注入的文字会被当成 vim 命令。
- Codex：Esc 会“中断并立即发送”排队消息；Ctrl+C 中断时把排队消息退回输入框，清空即可。
  中断不会停掉它开的后台终端，看到提示时再发 /stop。
"""
import asyncio
import logging
import re

log = logging.getLogger("code-anywhere.abort")

SEPARATOR = "────"


def claude_input(screen: str) -> str:
    """Claude 输入框（最后两条分隔线之间）的文字，去掉提示符和占位提示。"""
    lines = screen.splitlines()
    seps = [i for i, line in enumerate(lines) if SEPARATOR in line]
    if len(seps) < 2:
        return ""
    body = [line.strip() for line in lines[seps[-2] + 1:seps[-1]]]
    body = [line.removeprefix("❯").strip() for line in body]
    text = "\n".join(line for line in body if line)
    return "" if text.startswith("Press up to edit queued messages") else text


def codex_input(screen: str) -> str:
    """Codex 输入框（最后一个 › 开头的行，到状态栏或提示行之前）的文字；占位提示视为空。"""
    lines = screen.splitlines()
    starts = [i for i, line in enumerate(lines) if line.lstrip().startswith("›")]
    if not starts:
        return ""
    body = [lines[starts[-1]].lstrip().removeprefix("›").strip()]
    for line in lines[starts[-1] + 1:]:
        stripped = line.strip()
        if not stripped or " · " in stripped or "to queue message" in stripped or "context left" in stripped:
            break
        body.append(stripped)
    text = "\n".join(body).strip()
    return "" if text == "Ask Codex to do anything" else text


# Claude 运行中的状态行形如「✢ Proofing… (8s · ↓ 238 tokens)」
_CLAUDE_SPINNER = re.compile(r"^\s*\S\s+\S+…\s*\(", re.MULTILINE)


def claude_running(screen: str) -> bool:
    return bool(_CLAUDE_SPINNER.search(screen))


def claude_has_queue(screen: str) -> bool:
    return "Press up to edit queued messages" in screen


def codex_running(screen: str) -> bool:
    return "esc to interrupt" in screen or "Messages to be submitted" in screen


async def _screen(pane: str) -> str:
    from src import backends
    try:
        return await backends.command("tmux", "capture-pane", "-p", "-t", pane)
    except Exception:
        return ""


async def _keys(pane: str, *keys: str, pause: float = 0.3):
    from src import backends
    for key in keys:
        await backends.command("tmux", "send-keys", "-t", pane, key)
        await asyncio.sleep(pause)


async def _clear_input(pane: str, read_input) -> None:
    for _ in range(8):
        if not read_input(await _screen(pane)):
            return
        await _keys(pane, "C-u", "BSpace", pause=0.1)


async def abort(pane: str, backend: str) -> tuple[bool, str]:
    """执行中断。返回 (是否有正在运行的回合, 被丢弃的排队消息原文)。只在看到对应状态时才按键。"""
    screen = await _screen(pane)
    if backend == "codex":
        running = codex_running(screen)
        await _clear_input(pane, codex_input)  # 先清掉没发出的草稿，免得和退回的排队消息混在一起
        dropped = ""
        if running:
            await _keys(pane, "C-c", pause=1.5)
            dropped = codex_input(await _screen(pane))
            await _clear_input(pane, codex_input)
        if "background terminal" in await _screen(pane):
            from src import backends
            await backends.command("tmux", "send-keys", "-t", pane, "-l", "/stop")
            await asyncio.sleep(0.5)
            for _ in range(3):  # 第一次回车可能只是在命令补全菜单里选中，要看到输入框清空才算执行
                await _keys(pane, "Enter", pause=1.0)
                if codex_input(await _screen(pane)) != "/stop":
                    break
            await _clear_input(pane, codex_input)
        return running, dropped
    vim = "-- INSERT --" in screen
    running = claude_running(screen)
    dropped = ""
    if claude_has_queue(screen):
        await _keys(pane, "Up", pause=0.8)
        dropped = claude_input(await _screen(pane))
    await _clear_input(pane, claude_input)
    if running:
        await _keys(pane, *(["Escape", "Escape"] if vim else ["Escape"]), pause=0.5)
    if vim and "-- INSERT --" not in await _screen(pane):
        await _keys(pane, "i")
    return running, dropped
