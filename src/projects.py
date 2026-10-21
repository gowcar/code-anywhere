"""项目发现 & Topic 绑定

项目来源：~/.claude/projects/（Claude 自动管理）+ ~/.codex/sessions/（Codex rollout）
两边按项目路径合并——只用过 Codex、从没跑过 Claude 的目录同样算项目
我们只维护：topic_id ↔ 项目路径 的绑定关系
"""

import json
import logging
import os
import re
from pathlib import Path

from src.config import PROJECT_ROOT

log = logging.getLogger("code-anywhere.projects")

# topic_id → project_path 的绑定
BINDINGS_FILE = PROJECT_ROOT / ".bindings.json"
_bindings: dict[int, str] = {}   # topic_id → project_path


def _load():
    global _bindings
    if BINDINGS_FILE.exists():
        try:
            data = json.loads(BINDINGS_FILE.read_text())
            _bindings = {int(k): v for k, v in data.items()}
            log.info(f"[bindings] loaded {len(_bindings)} bindings")
        except Exception as e:
            log.warning(f"[bindings] load failed: {e}")


def _save():
    BINDINGS_FILE.write_text(
        json.dumps({str(k): v for k, v in _bindings.items()}, ensure_ascii=False, indent=2)
    )


def bind(topic_id: int, project_path: str):
    _bindings[topic_id] = project_path
    _save()
    log.info(f"[bind] topic {topic_id} → {project_path}")


def unbind(topic_id: int):
    _bindings.pop(topic_id, None)
    _save()


def reset():
    """清除所有绑定"""
    _bindings.clear()
    _save()


def get_path(topic_id: int) -> str | None:
    return _bindings.get(topic_id)


def get_topic(project_path: str) -> int | None:
    for tid, path in _bindings.items():
        if path == project_path:
            return tid
    return None


def all_bindings() -> dict[int, str]:
    return dict(_bindings)


# ============ Claude transcript 目录名 ↔ 真实路径 ============
# Claude 把项目路径里的 / _ . 统统编码成 -，字面上无法区分三者。
# 历史上各模块自己拼 `cwd.replace("/", "-")`，漏掉 _ 和 . ——
# 结果含下划线的项目（如 mywork/newbee_intro）永远找不到 transcript 目录。
# 编解码统一收敛到这里。

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"


def codex_sessions_dir() -> Path:
    """Codex rollout 根目录（尊重 CODEX_HOME，运行时读取以便测试覆盖）"""
    return Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"


# rollout 首行是 session_meta，cwd 排在超长的 base_instructions 之前，
# 读前 8KB 足够拿到；整行可能几十 KB，全读没必要。
_CWD_RE = re.compile(r'"cwd"\s*:\s*"((?:[^"\\]|\\.)*)"')
_CODEX_HEAD_BYTES = 8192


def discover_codex() -> dict[str, tuple[int, float]]:
    """扫 Codex rollout，返回 {项目路径: (会话数, 最新 mtime)}"""
    base = codex_sessions_dir()
    if not base.is_dir():
        return {}
    found: dict[str, tuple[int, float]] = {}
    for f in base.rglob("*.jsonl"):
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                head = fh.read(_CODEX_HEAD_BYTES)
            m = _CWD_RE.search(head)
            if not m:
                continue
            cwd = json.loads(f'"{m.group(1)}"')
            if not cwd or not Path(cwd).is_dir():
                continue
            cwd = str(Path(cwd).resolve())
            mtime = f.stat().st_mtime
            count, prev = found.get(cwd, (0, 0.0))
            found[cwd] = (count + 1, max(prev, mtime))
        except Exception as e:
            log.debug(f"[codex] 跳过 {f.name}: {e}")
            continue
    return found


def _match_child(parent: Path, seg: str) -> Path | None:
    """在 parent 下找名字匹配 seg 的条目。seg 里的 - 可能是原路径的 - _ 或 . """
    literal = parent / seg
    if literal.exists():
        return literal
    if "-" not in seg:
        return None
    pattern = re.compile("^" + "[-_.]".join(re.escape(x) for x in seg.split("-")) + "$")
    try:
        for child in parent.iterdir():
            if pattern.match(child.name):
                return child
    except OSError:
        pass
    return None


def reverse_path(hash_name: str) -> str:
    """transcript 目录名 → 真实路径（逐段去父目录实际匹配，贪心吃最长段组合）"""
    parts = hash_name.lstrip("-").split("-")
    result = Path("/")
    i = 0
    while i < len(parts):
        found = False
        for j in range(len(parts), i, -1):
            matched = _match_child(result, "-".join(parts[i:j]))
            if matched:
                result = matched
                i = j
                found = True
                break
        if not found:
            result = result / parts[i]
            i += 1
    return str(result)


def claude_dir(project_dir: str) -> Path | None:
    """真实路径 → 该项目的 transcript 目录；不存在返回 None"""
    encoded = re.sub(r"[/_.]", "-", str(project_dir))
    direct = CLAUDE_PROJECTS / encoded
    if direct.is_dir():
        return direct
    # 兜底：编码规则若还有别的字符（空格等），反查一遍
    target = str(Path(project_dir))
    try:
        for child in CLAUDE_PROJECTS.iterdir():
            if child.is_dir() and reverse_path(child.name) == target:
                return child
    except OSError:
        pass
    return None


_TMP_ROOTS = ("/tmp", "/var/tmp")


def _is_ignored(path: str) -> bool:
    """home 本身和临时目录只是"跑过 CLI 的地方"，不是项目——否则每个 topic 列表都会被它们刷屏

    .projectignore 中绝对路径排除子树；=绝对路径仅排除目录本身，# 为注释。
    """
    p = Path(path)
    if p == Path.home():
        return True
    for tmp in _TMP_ROOTS:
        tmp_path = Path(tmp)
        if p == tmp_path or p.is_relative_to(tmp_path):
            return True
    ignore_file = PROJECT_ROOT / ".projectignore"
    if ignore_file.exists():
        try:
            for line in ignore_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("="):
                    if p == Path(line[1:].strip()):
                        return True
                    continue
                if p == Path(line) or p.is_relative_to(Path(line)):
                    return True
        except Exception as e:
            log.warning(f"[projectignore] 读取失败: {e}")
    return False


def discover() -> list[dict]:
    """发现所有项目 = Claude（~/.claude/projects/）∪ Codex（~/.codex/sessions/）

    同一路径两边都有时合并：会话数相加，mtime 取较新。
    """
    claude_projects = CLAUDE_PROJECTS
    _reverse_path = reverse_path
    bound_paths = set(_bindings.values())
    merged: dict[str, tuple[int, float]] = {}

    if claude_projects.is_dir():
        for d in sorted(claude_projects.iterdir()):
            if not d.is_dir():
                continue
            real_path = _reverse_path(d.name)
            if not Path(real_path).is_dir():
                continue
            sessions = list(d.glob("*.jsonl"))
            if not sessions:
                continue
            latest_mtime = max(s.stat().st_mtime for s in sessions)
            merged[str(Path(real_path).resolve())] = (len(sessions), latest_mtime)

    for cwd, (count, mtime) in discover_codex().items():
        prev_count, prev_mtime = merged.get(cwd, (0, 0.0))
        merged[cwd] = (prev_count + count, max(prev_mtime, mtime))

    all_paths = [(p, c, m) for p, (c, m) in merged.items() if not _is_ignored(p)]

    def _repo_root(p: str) -> str | None:
        """向上找 .git，返回所属仓库根；不在任何仓库内则 None"""
        path = Path(p)
        for d in (path, *path.parents):
            if (d / ".git").exists():
                return str(d)
        return None

    # 过滤子目录：只过滤「与父项目同属一个 git 仓库」的子目录（如 repo/poc），
    # 容器型目录下的独立项目（如 ~/workspace/mywork/newbee_intro）要各自成项目
    all_paths.sort(key=lambda x: x[0])
    results = []
    for path, count, mtime in all_paths:
        repo = _repo_root(path)
        is_child = any(
            path.startswith(r["path"] + "/") and repo is not None and repo == _repo_root(r["path"])
            for r in results
        )
        if not is_child:
            results.append({
                "name": Path(path).name,
                "path": path,
                "sessions": count,
                "mtime": mtime,
                "bound": path in bound_paths,
            })

    return results


_load()
