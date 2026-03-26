"""项目发现 & Topic 绑定

项目来源：~/.claude/projects/（Claude 自动管理）
我们只维护：topic_id ↔ 项目路径 的绑定关系
"""

import json
import logging
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


def discover() -> list[dict]:
    """从 ~/.claude/projects/ 发现所有 Claude 项目"""
    claude_projects = Path.home() / ".claude" / "projects"
    if not claude_projects.is_dir():
        return []

    def _reverse_path(hash_name: str) -> str:
        parts = hash_name.lstrip("-").split("-")
        result = "/"
        i = 0
        while i < len(parts):
            found = False
            for j in range(len(parts), i, -1):
                candidate = result + "-".join(parts[i:j])
                if Path(candidate).exists():
                    result = candidate + "/"
                    i = j
                    found = True
                    break
            if not found:
                result += parts[i] + "/"
                i += 1
        return result.rstrip("/")

    bound_paths = set(_bindings.values())
    all_paths = []

    for d in sorted(claude_projects.iterdir()):
        if not d.is_dir():
            continue
        real_path = _reverse_path(d.name)
        if not Path(real_path).is_dir():
            continue
        # 跳过 Bot 自身项目（避免自己给自己创建话题）
        if Path(real_path).resolve() == PROJECT_ROOT.resolve():
            # 清理残留的自身绑定
            self_topic = get_topic(real_path)
            if self_topic:
                log.info(f"[discover] 清理 Bot 自身项目的残留绑定 (topic {self_topic})")
                unbind(self_topic)
            continue
        sessions = list(d.glob("*.jsonl"))
        if not sessions:
            continue
        latest_mtime = max(s.stat().st_mtime for s in sessions)
        all_paths.append((real_path, len(sessions), latest_mtime))

    # 过滤子目录
    all_paths.sort(key=lambda x: x[0])
    results = []
    for path, count, mtime in all_paths:
        is_child = any(path.startswith(r["path"] + "/") for r in results)
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
