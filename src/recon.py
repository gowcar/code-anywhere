"""recon — 项目"外脑"机制：防上下文压缩失忆

三态分层（对齐全局协作规范）：
- recon/PROGRESS.md  ：活的状态（当前位置 / 活跃分支 / 最近决策 / 资产索引）
- recon/wiki/        ：稳定知识（每篇一主题，递进式按需加载）
- recon/decisions/   ：旧决策归档（给 PROGRESS 瘦身）

本模块零第三方依赖（只用 stdlib），因此 bot 进程和 SessionStart hook 都能安全 import。

职责：
- ensure_skeleton(cwd)          : 项目无 recon/ 时生成骨架（绝不覆盖已有文件）
- build_session_context(cwd)    : SessionStart 注入用——只给「当前位置」+ wiki 索引，控量、递进式
- cleanup_attachments(cwd)      : 清理 .attachments 旧文件，避免目录膨胀
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# —— 防膨胀红线 & 注入控量 ——
PROGRESS_SOFT_LIMIT_LINES = 250      # PROGRESS.md 软上限，超了提示精简
SESSION_CTX_MAX_BYTES = 2048         # 单次 SessionStart 注入上限（控制 session 上下文总量）
ATTACHMENTS_KEEP = 20                # .attachments 保留最近 N 个附件

RECON_DIR = "recon"


def resolve_project_root(cwd: str) -> Path:
    """Resolve a session cwd to the project root used by recon.

    Prefer the nearest ancestor that already owns a recon/PROGRESS.md.  This
    preserves nested projects with their own external brain.  For a new
    project, fall back to the Git worktree root; only non-Git directories use
    cwd itself.
    """
    root = Path(cwd).expanduser()
    try:
        root = root.resolve()
    except OSError:
        root = root.absolute()

    if not root.is_dir():
        return root

    for candidate in (root, *root.parents):
        if (candidate / RECON_DIR / "PROGRESS.md").is_file():
            return candidate

    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        git_root = Path(result.stdout.strip()).resolve()
        if git_root.is_dir():
            return git_root
    except (OSError, subprocess.SubprocessError):
        pass

    return root


# ————————————————————————————————————————————————————————————————
# 模板（用 {{NAME}} 占位，避免与 markdown 的 {} 冲突）
# ————————————————————————————————————————————————————————————————

_PROGRESS_TMPL = """# PROGRESS — {{NAME}} 外脑

> 单一真相源。新 session 第一件事读「📍 当前位置」。
> **只放"活的"状态：稳定知识进 `wiki/`，旧决策/已废分支归档，全文 ≤ {{LIMIT}} 行。**

## 📍 当前位置
<一两句话：现在在干什么、最近一次结论、下一步动作>

## 🎯 目标
<一句话终极目标>

## 🌲 活跃分支
<只列进行中 🟡 / 未启 ⚪。已结论 🟢 写一句话结论 + 资产路径后迁入 wiki；已废 🔴 写原因后归档>

## 📒 决策日志
> 滚动保留最近 ~10 条；更旧的移入 `recon/decisions/archive.md`，此处只留指针。

| 日期 | 决策 | 背景 | 影响 |
|---|---|---|---|

## 🗂 资产索引
<重要文件 / 脚本 / 物料路径速查>
"""

_CLAUDE_TMPL = """# {{NAME}} — 项目协约

本项目启用 `recon/` 外脑机制（防上下文压缩失忆）。**单一真相源 = `recon/PROGRESS.md`。**

## Session 纪律
- 开局先读 `recon/PROGRESS.md` 的「📍 当前位置」（Claude 由 SessionStart 自动注入摘要；Codex 需自己先读）。
- 稳定知识查 `recon/wiki/`——**按需 Read 单篇，不要一次全读**（递进式加载省上下文）。
- 做完一步实质工作 → **先回 PROGRESS 落一行再继续**（事件驱动，别攒到最后一次性写）。
- 出结论标 🟢 + 资产路径；走不通标 🔴 + 原因；用户做决策 → 写决策日志（带日期）。

## 防膨胀（重要，别重蹈"PROGRESS 越滚越大"的覆辙）
- `PROGRESS.md` 只放"活的"状态，全文 **≤ {{LIMIT}} 行**。
- 稳定 / 领域知识 → 迁 `recon/wiki/<主题>.md`。
- 已结论 🟢 分支的正文、旧决策日志 → 归档到 `recon/decisions/archive.md`，PROGRESS 里只留一行指针。
- 定期精简，别让 PROGRESS 只增不减——否则外脑退化成又一坨大上下文。

## 图片 / 大产物（控上下文）
- 图片、截图**看过后把关键结论提炼成文字**写进 PROGRESS/wiki；图本身可随 compact 丢弃。
- 不要依赖"上下文里还留着那张图"——落盘的文字才是长期记忆。
"""

_WIKI_README_TMPL = """# wiki — {{NAME}} 稳定知识库

每篇一个主题（协议 / 架构 / 领域规则 / 环境配置）。

**递进式加载**：需要某主题时再 Read 对应文件，不要一次全读——这是控制 session 上下文总量的关键。

PROGRESS.md 里逐渐稳定下来的知识请迁到这里，给 PROGRESS 持续瘦身。
"""


def _render(tmpl: str, name: str) -> str:
    return tmpl.replace("{{NAME}}", name).replace("{{LIMIT}}", str(PROGRESS_SOFT_LIMIT_LINES))


# ————————————————————————————————————————————————————————————————
# 骨架生成
# ————————————————————————————————————————————————————————————————

def ensure_skeleton(cwd: str) -> bool:
    """项目无 recon/ 时生成骨架。绝不覆盖任何已存在的文件。

    Returns: True 表示本次新建了 PROGRESS.md（首次建骨架）。
    """
    try:
        root = resolve_project_root(cwd)
        if not root.is_dir():
            return False
        recon = root / RECON_DIR
        progress = recon / "PROGRESS.md"
        created = False

        if not progress.exists():
            (recon / "wiki").mkdir(parents=True, exist_ok=True)
            (recon / "decisions").mkdir(parents=True, exist_ok=True)
            name = root.name
            progress.write_text(_render(_PROGRESS_TMPL, name), encoding="utf-8")
            created = True

            wiki_readme = recon / "wiki" / "README.md"
            if not wiki_readme.exists():
                wiki_readme.write_text(_render(_WIKI_README_TMPL, name), encoding="utf-8")

            archive = recon / "decisions" / "archive.md"
            if not archive.exists():
                archive.write_text(f"# {name} — 决策归档\n\n> 从 PROGRESS.md 决策日志滚动移出的旧决策。\n", encoding="utf-8")

        # 项目 CLAUDE.md：仅当完全不存在时创建，避免破坏用户已有协约
        claude_md = root / "CLAUDE.md"
        if not claude_md.exists():
            claude_md.write_text(_render(_CLAUDE_TMPL, root.name), encoding="utf-8")

        return created
    except Exception:
        return False


# ————————————————————————————————————————————————————————————————
# SessionStart 注入（控量 + 递进式）
# ————————————————————————————————————————————————————————————————

def _extract_current_position(progress_text: str) -> str:
    """截取「## 📍 当前位置」到下一个 `## ` 之间的内容。"""
    m = re.search(r"^##\s*📍[^\n]*\n(.*?)(?=^##\s)", progress_text, re.S | re.M)
    if not m:
        return ""
    body = m.group(1)
    # 只取第一个横向分隔线之前——`---` 之后通常是"上一分支/历史"，不属于当前位置
    body = re.split(r"\n-{3,}\s*\n", body, maxsplit=1)[0]
    return body.strip()


def _wiki_index(wiki_dir: Path) -> list[str]:
    """列 wiki/*.md（除 README），每篇取一行描述（首个 # 标题或首个非空行）。"""
    items: list[str] = []
    if not wiki_dir.is_dir():
        return items
    for f in sorted(wiki_dir.glob("*.md")):
        if f.name.lower() == "readme.md":
            continue
        desc = ""
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip().lstrip("#").strip()
                if s:
                    desc = s
                    break
        except Exception:
            pass
        items.append(f"- {f.name} — {desc}" if desc else f"- {f.name}")
    return items


def build_session_context(cwd: str) -> str | None:
    """构造 SessionStart 注入文本（陈述句，避免触发 prompt-injection 防御）。

    只给「当前位置」摘要 + wiki 索引（不给 wiki 全文，递进式按需 Read），
    并控制在 SESSION_CTX_MAX_BYTES 以内，避免注入本身变成"一大堆上下文"。
    无 recon/ 返回 None。
    """
    try:
        recon = resolve_project_root(cwd) / RECON_DIR
        progress = recon / "PROGRESS.md"
        if not progress.exists():
            return None

        text = progress.read_text(encoding="utf-8", errors="replace")
        n_lines = text.count("\n") + 1
        pos = _extract_current_position(text)
        wiki = _wiki_index(recon / "wiki")

        parts: list[str] = []
        parts.append("本项目使用 recon/ 外脑（防上下文压缩失忆）。以下是 recon/PROGRESS.md 的「当前位置」摘要：")
        parts.append("")
        parts.append(pos if pos else "（PROGRESS.md 尚未填写当前位置，是一份空白模板。）")
        if wiki:
            parts.append("")
            parts.append("recon/wiki/ 稳定知识索引（需要时用 Read 读对应文件，不必现在全读）：")
            parts.extend(wiki)
        parts.append("")
        parts.append("完整进度在 recon/PROGRESS.md。按项目协约：做完一步先回 PROGRESS 落一行；稳定知识写入 wiki 以控制 PROGRESS 体量。")

        if n_lines > PROGRESS_SOFT_LIMIT_LINES:
            parts.append("")
            parts.append(
                f"注意：PROGRESS.md 当前 {n_lines} 行，已超过 {PROGRESS_SOFT_LIMIT_LINES} 行软上限，"
                "宜精简——把已结论分支与旧决策移入 recon/wiki 或 recon/decisions/archive.md。"
            )

        out = "\n".join(parts)
        if len(out.encode("utf-8")) > SESSION_CTX_MAX_BYTES:
            # 超限：按字节安全截断（不破坏 UTF-8）
            out = out.encode("utf-8")[:SESSION_CTX_MAX_BYTES].decode("utf-8", errors="ignore")
            out += "\n…（摘要过长已截断，完整见 recon/PROGRESS.md）"
        return out
    except Exception:
        return None


# ————————————————————————————————————————————————————————————————
# 附件清理（控上下文的磁盘侧配合）
# ————————————————————————————————————————————————————————————————

def cleanup_attachments(cwd: str, keep: int = ATTACHMENTS_KEEP) -> int:
    """保留 .attachments 最近 keep 个文件，删更旧的。返回删除数量。

    图片处理完的价值应已由 claude 落盘成文字（见项目 CLAUDE.md 协约），
    这里清磁盘避免目录膨胀；上下文里的旧图由 Claude CLI 的 compact 自然回收。
    """
    try:
        d = Path(cwd) / ".attachments"
        if not d.is_dir():
            return 0
        files = [f for f in d.iterdir() if f.is_file()]
        if len(files) <= keep:
            return 0
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
        removed = 0
        for f in files[keep:]:
            try:
                f.unlink()
                removed += 1
            except Exception:
                pass
        return removed
    except Exception:
        return 0
