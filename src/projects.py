"""项目发现 & Topic 绑定

项目来源：~/.claude/projects/（Claude 自动管理）
我们只维护：topic_id ↔ 项目路径 的绑定关系
"""

import json
import logging
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


def discover() -> list[dict]:
    """从 ~/.claude/projects/ 发现所有 Claude 项目"""
    claude_projects = CLAUDE_PROJECTS
    if not claude_projects.is_dir():
        return []

    _reverse_path = reverse_path
    bound_paths = set(_bindings.values())
    all_paths = []

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
        all_paths.append((real_path, len(sessions), latest_mtime))

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
