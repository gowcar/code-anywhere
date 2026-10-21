"""每个项目的模型 / effort / 权限偏好（/model 设定，启动和重启 CLI 时生效）。

存于 .model-prefs.json：{项目路径: {"claude": {...}, "codex": {...}}}。
Codex 的权限仍由 src/permissions.py 管理（/permissions 与 /model 共用）。
"""
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from src.config import PROJECT_ROOT

PREFS_FILE = PROJECT_ROOT / ".model-prefs.json"

# Claude 没有可查询的模型目录，内置清单：(别名, 模型 ID, 显示名)
CLAUDE_MODELS = [
    ("opus", "claude-opus-5-5[1m]", "Opus 5.5 · 1M 上下文"),
    ("opus-200k", "claude-opus-5-5", "Opus 5.5 · 200K 上下文"),
    ("sonnet", "claude-sonnet-5", "Sonnet 5"),
    ("fable", "claude-fable-5-1", "Fable 5.1"),
    ("haiku", "claude-haiku-4-5-20251001", "Haiku 4.5"),
]
CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]
CLAUDE_PERMISSIONS = {"full": "bypass（跳过所有审批）", "default": "逐项审批（TG 弹按钮）"}
CODEX_PERMISSIONS = ("full", "workspace", "read-only")
ALL_EFFORTS = {"minimal", "low", "medium", "high", "xhigh", "max", "ultra"}

_catalog_cache: tuple[float, list[dict]] | None = None


def codex_models(ttl: float = 600) -> list[dict]:
    """`codex debug models` 中可见的模型：[{slug, name, efforts, default}]；取不到返回空列表。"""
    global _catalog_cache
    if _catalog_cache and time.time() - _catalog_cache[0] < ttl:
        return _catalog_cache[1]
    try:
        out = subprocess.run(["codex", "debug", "models"], capture_output=True, text=True, timeout=30).stdout
        data = json.loads(out[out.index("{"):])
        models = [{"slug": m["slug"], "name": m.get("display_name") or m["slug"],
                   "efforts": [e["effort"] for e in m.get("supported_reasoning_levels", [])],
                   "default": m.get("default_reasoning_level")}
                  for m in data.get("models", []) if m.get("visibility") == "list"]
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        models = []
    _catalog_cache = (time.time(), models)
    return models


def _load() -> dict:
    try:
        return json.loads(PREFS_FILE.read_text())
    except (OSError, ValueError):
        return {}


LEGACY_CLAUDE_PERMISSIONS = PROJECT_ROOT / ".claude-permissions.json"  # nas 旧版 /model claude full 的存档


def get(cwd: str, engine: str) -> dict:
    key = str(Path(cwd).resolve())
    prefs = dict(_load().get(key, {}).get(engine, {}))
    if engine == "claude" and "permission" not in prefs:
        try:
            legacy = json.loads(LEGACY_CLAUDE_PERMISSIONS.read_text()).get(key)
        except (OSError, ValueError, AttributeError):
            legacy = None
        if legacy in CLAUDE_PERMISSIONS:
            prefs["permission"] = legacy
    return prefs


def update(cwd: str, engine: str, **changes) -> dict:
    data = _load()
    entry = data.setdefault(str(Path(cwd).resolve()), {}).setdefault(engine, {})
    entry.update({k: v for k, v in changes.items() if v is not None})
    tmp = PREFS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(PREFS_FILE)
    return dict(entry)


@dataclass
class Request:
    engine: str | None = None
    model: str | None = None        # 解析后的模型 ID / slug
    effort: str | None = None
    permission: str | None = None


def _claude_model(token: str) -> str | None:
    for alias, model_id, name in CLAUDE_MODELS:
        if token in {alias, model_id.lower(), name.lower().replace(" ", "")}:
            return model_id
    return None


def _codex_model(token: str, catalog: list[dict]) -> str | None:
    for m in catalog:
        if token in {m["slug"].lower(), m["name"].lower()}:
            return m["slug"]
    return None


def parse(args: list[str], current_engine: str, catalog: list[dict]) -> Request:
    """把 /model 的参数（顺序不限）解析成请求；不认识的词抛 ValueError（中文说明）。"""
    req = Request()
    for raw in args:
        token = raw.strip().lower()
        if token in {"claude", "codex", "gpt"}:
            req.engine = "codex" if token == "gpt" else token
        elif token in ALL_EFFORTS:
            req.effort = token
        elif token in set(CLAUDE_PERMISSIONS) | set(CODEX_PERMISSIONS):
            req.permission = token
        elif model := _claude_model(token):
            req.model, req.engine = model, req.engine or "claude"
            if req.engine != "claude":
                raise ValueError(f"{raw} 是 Claude 的模型，不能用于 Codex")
        elif model := _codex_model(token, catalog):
            req.model, req.engine = model, req.engine or "codex"
            if req.engine != "codex":
                raise ValueError(f"{raw} 是 Codex 的模型，不能用于 Claude")
        else:
            raise ValueError(f"不认识「{raw}」；发 /model list 查看可选的模型、effort 和权限")
    engine = req.engine or current_engine
    if req.permission:
        allowed = CODEX_PERMISSIONS if engine == "codex" else tuple(CLAUDE_PERMISSIONS)
        if req.permission not in allowed:
            raise ValueError(f"{engine} 的权限只能是：{' / '.join(allowed)}")
    if req.effort:
        if engine == "claude":
            efforts = CLAUDE_EFFORTS
        elif req.model:
            efforts = next((m["efforts"] for m in catalog if m["slug"] == req.model), sorted(ALL_EFFORTS))
        else:  # 没指定模型：任一模型支持即可，应用时再按项目当前模型校验
            efforts = sorted({e for m in catalog for e in m["efforts"]} or ALL_EFFORTS)
        if req.effort not in efforts:
            raise ValueError(f"effort「{req.effort}」不可用，可选：{' / '.join(efforts)}")
    return req


def format_list(catalog: list[dict]) -> str:
    lines = ["<b>Claude</b>（effort：" + " / ".join(CLAUDE_EFFORTS) + "）"]
    lines += [f"• <code>{alias}</code> {name}" for alias, _, name in CLAUDE_MODELS]
    lines.append("权限：" + "，".join(f"<code>{k}</code> {v}" for k, v in CLAUDE_PERMISSIONS.items()))
    lines.append("\n<b>Codex</b>")
    if catalog:
        lines += [f"• <code>{m['slug']}</code>（effort：{' / '.join(m['efforts'])}；默认 {m['default']}）" for m in catalog]
    else:
        lines.append("• 读取 codex 模型目录失败（本机没有 codex 或网关不可用）")
    lines.append("权限：<code>full</code> / <code>workspace</code> / <code>read-only</code>")
    lines.append("\n例：<code>/model claude sonnet high full</code>　<code>/model gpt-6-sol xhigh</code>　<code>/model max</code>")
    return "\n".join(lines)
