"""Deliver images explicitly referenced in public Codex replies."""

import logging
import os
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import mistune

log = logging.getLogger("code-anywhere.images")
_markdown = mistune.create_markdown(renderer="ast")
_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_temporary_roots = (Path("/tmp"), Path("/var/tmp"))
_max_bytes = 50 * 1024 * 1024


def linked_images(text: str, cwd: str) -> list[Path]:
    """Existing deliverable images explicitly linked in a public reply."""
    return [path for path, error in _linked_image_candidates(text, cwd) if not error]


def _linked_image_candidates(text: str, cwd: str) -> list[tuple[Path, str | None]]:
    """Return local image links with actionable errors instead of silently dropping them."""
    if not cwd:
        return []
    root = Path(cwd).resolve()
    images = {}

    def visit(nodes):
        for node in nodes:
            # A codespan containing only a path is an explicit public attachment.
            # Plain text fences containing one path are also explicit attachments.
            # Keep executable code, Markdown examples and multi-line blocks local.
            plain_fence = (node["type"] == "block_code"
                           and node.get("style") == "fenced"
                           and node.get("attrs", {}).get("info", "").strip().lower()
                           in {"", "text", "plaintext", "txt"})
            if node["type"] == "codespan" or plain_fence:
                raw = node.get("raw", "").strip()
                if raw.startswith(("/", "./", ".attachments/", "file:///")) and "\n" not in raw:
                    visit([{"type": "link", "attrs": {"url": raw}}])
            if node["type"] in {"image", "link"}:
                try:
                    url = urlsplit(node.get("attrs", {}).get("url", ""))
                    if url.scheme in {"", "file"} and url.netloc in {"", "localhost"}:
                        requested = root / unquote(url.path)
                        if requested.suffix.lower() in _extensions:
                            path = requested.resolve()
                            in_project = requested.is_relative_to(root) and path.is_relative_to(root)
                            explicit_temp = not requested.is_relative_to(root) and any(
                                requested.is_relative_to(t) and path.is_relative_to(t.resolve())
                                for t in _temporary_roots)
                            error = None
                            if not (in_project or explicit_temp):
                                error = "路径不在项目或允许的临时目录内，请复制到项目 .attachments/ 后重新引用"
                            elif not path.is_file():
                                error = "文件不存在或不是普通文件，请重新生成后引用"
                            elif explicit_temp and path.stat().st_uid != os.getuid():
                                error = "临时文件不属于当前服务用户，请复制到项目 .attachments/ 后重新引用"
                            elif path.stat().st_size > _max_bytes:
                                error = "文件超过 50 MiB，请缩小后重试"
                            images[path] = error
                except (OSError, ValueError):
                    # Keep the original target local: do not attempt remote fetching.
                    target = node.get("attrs", {}).get("url", "")
                    if any(target.lower().endswith(ext) for ext in _extensions):
                        images[Path(target)] = "文件无法读取，请检查路径和权限"
            visit(node.get("children", []))

    visit(_markdown(text))
    return list(images.items())


async def send_linked_images(bot, text: str, cwd: str, chat_id: int, topic_id: int):
    """Upload originals, return receipts, and make failures visible to the user."""
    receipts = []
    for path, error in _linked_image_candidates(text, cwd):
        if error:
            log.warning("[img] skipped explicit attachment path=%s reason=%s", path, error)
            await bot.send_message(
                chat_id=chat_id, message_thread_id=topic_id,
                text=f"⚠️ 图片未发送：{path.name}。{error}。",
            )
            continue
        try:
            with path.open("rb") as document:
                message = await bot.send_document(
                    chat_id=chat_id, message_thread_id=topic_id,
                    document=document, filename=path.name,
                )
            receipts.append((str(path), message.message_id))
            log.info("[img] codex document topic=%s message=%s path=%s",
                     topic_id, message.message_id, path)
        except Exception:
            log.exception("[img] codex upload failed path=%s", path)
            await bot.send_message(
                chat_id=chat_id, message_thread_id=topic_id,
                text=f"⚠️ 图片发送失败：{path.name}，请稍后重试。",
            )
    return receipts


async def send_viewed_attachment(bot, image_path: str, cwd: str, chat_id: int, topic_id: int):
    """.attachments is the explicit delivery directory; other ImageViews stay local."""
    if not cwd or not isinstance(image_path, str):
        return []
    try:
        url = urlsplit(image_path)
        if url.scheme not in {"", "file"} or url.netloc not in {"", "localhost"}:
            return []
        root = Path(cwd).resolve()
        directory = root / ".attachments"
        # A symlinked attachment directory must not expand the delivery scope.
        if directory.is_symlink():
            return []
        path = (root / unquote(url.path)).resolve()
        if not path.is_relative_to(directory):
            return []
    except (OSError, ValueError):
        return []
    return await send_linked_images(
        bot, f"![attachment]({quote(str(path), safe='/')})", cwd, chat_id, topic_id,
    )
