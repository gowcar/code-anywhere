"""/model 按钮菜单：当前设置一行说明 + 按引擎分组的模型按钮 + effort 行 + 权限行。

只负责“现状 → 文字与键盘”和“按钮 → 请求”，真正的切换由 bot._apply_model 完成。
回调数据（≤ 64 字节）：md:m:<engine>:<别名或 slug> / md:e:<effort> / md:p:<权限> / md:n（分组标题，点了无动作）
"""
import html
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src import model_prefs

PREFIX = "md:"
CHECK = "✅ "


@dataclass
class Current:
    engine: str
    model: str | None        # Claude 模型 ID / Codex slug；None = 不知道（用 CLI 默认）
    effort: str | None       # None = CLI 默认
    effort_is_default: bool
    permission: str


def claude_name(model_id: str | None) -> str | None:
    return next((n for _, mid, n in model_prefs.CLAUDE_MODELS if mid == model_id), None)


def codex_name(slug: str | None, catalog: list[dict]) -> str | None:
    return next((m["name"] for m in catalog if m["slug"] == slug), None)


def efforts_for(engine: str, model: str | None, catalog: list[dict]) -> list[str]:
    if engine == "claude":
        return model_prefs.CLAUDE_EFFORTS
    found = next((m["efforts"] for m in catalog if m["slug"] == model), None)
    if found:
        return found
    union = {e for m in catalog for e in m["efforts"]}
    return [e for e in ["minimal", "low", "medium", "high", "xhigh", "max", "ultra"] if e in union] \
        or model_prefs.CLAUDE_EFFORTS


def permissions_for(engine: str) -> tuple[str, ...]:
    return model_prefs.CODEX_PERMISSIONS if engine == "codex" else tuple(model_prefs.CLAUDE_PERMISSIONS)


def _button(label: str, data: str, selected: bool = False) -> InlineKeyboardButton:
    return InlineKeyboardButton((CHECK if selected else "") + label, callback_data=PREFIX + data)


def _header(title: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(f"── {title} ──", callback_data=PREFIX + "n")]


def _rows(buttons: list[InlineKeyboardButton], width: int) -> list[list[InlineKeyboardButton]]:
    return [buttons[i:i + width] for i in range(0, len(buttons), width)]


def render(project: str, cur: Current, catalog: list[dict], note: str = "") -> tuple[str, InlineKeyboardMarkup]:
    claude_default = model_prefs.default_model("claude")
    codex_default = model_prefs.default_model("codex")

    def label(name: str, is_default: bool) -> str:
        return f"{name}（默认）" if is_default else name

    if cur.engine == "claude":
        model_label = claude_name(cur.model) or cur.model or "配置默认"
    else:
        model_label = codex_name(cur.model, catalog) or cur.model or "配置默认"
    effort_label = cur.effort or "默认"
    if cur.effort and cur.effort_is_default:
        effort_label += "（默认）"
    text = (f"<b>{html.escape(project)}</b>\n"
            f"当前：{html.escape(model_label)}（{'Codex' if cur.engine == 'codex' else 'Claude'}）"
            f" · effort {effort_label} · 权限 {model_prefs.PERMISSION_BUTTONS[cur.permission]}\n"
            "点按钮立即切换，终端会重启并接回原会话。同一引擎内换模型，上下文保留；"
            "换到另一个引擎（Claude / Codex）会接回那边上次的会话，两边上下文不共享。")
    if note:
        text += f"\n\n{note}"

    rows = [_header("Claude")]
    rows += _rows([_button(label(name, mid == claude_default), f"m:claude:{alias}",
                           cur.engine == "claude" and cur.model == mid)
                   for alias, mid, name in model_prefs.CLAUDE_MODELS], 2)
    rows.append(_header("Codex (GPT)"))
    if catalog:
        rows += _rows([_button(label(m["name"], m["slug"] == codex_default), f"m:codex:{m['slug']}",
                               cur.engine == "codex" and cur.model == m["slug"])
                       for m in catalog], 2)
    else:
        rows.append([InlineKeyboardButton("读取 Codex 模型目录失败", callback_data=PREFIX + "n")])
    efforts = efforts_for(cur.engine, cur.model, catalog)
    rows.append(_header("思考强度 effort"))
    rows += _rows([_button(e, f"e:{e}", e == cur.effort) for e in efforts], 5 if len(efforts) <= 5 else 3)
    rows.append(_header("权限"))
    rows.append([_button(model_prefs.PERMISSION_BUTTONS[p], f"p:{p}", p == cur.permission)
                 for p in permissions_for(cur.engine)])
    return text, InlineKeyboardMarkup(rows)


def parse_callback(data: str, cur: Current, catalog: list[dict]) -> model_prefs.Request | None:
    """按钮 → 请求；分组标题、已是当前值返回 None；过期按钮（模型已不在清单里）抛 ValueError。"""
    parts = data.removeprefix(PREFIX).split(":", 2)
    kind = parts[0]
    if kind == "m" and len(parts) == 3:
        engine, key = parts[1], parts[2]
        if engine == "claude":
            model = next((mid for alias, mid, _ in model_prefs.CLAUDE_MODELS if alias == key), None)
        else:
            model = next((m["slug"] for m in catalog if m["slug"] == key), None)
        if not model:
            raise ValueError("这个模型已不在清单里，请重新发 /model")
        if engine == cur.engine and model == cur.model:
            return None
        req = model_prefs.Request(engine=engine, model=model)
        if engine == "codex" and cur.engine == "codex" and cur.effort and not cur.effort_is_default \
                and cur.effort not in efforts_for("codex", model, catalog):
            req.effort = ""   # 新模型不支持原 effort：清掉，回到模型默认
        return req
    if kind == "e" and len(parts) == 2:
        if parts[1] == cur.effort:
            return None
        if parts[1] not in efforts_for(cur.engine, cur.model, catalog):
            raise ValueError(f"当前模型不支持 effort「{parts[1]}」")
        return model_prefs.Request(effort=parts[1])
    if kind == "p" and len(parts) == 2:
        if parts[1] == cur.permission:
            return None
        if parts[1] not in permissions_for(cur.engine):
            raise ValueError("这个权限选项不适用于当前引擎，请重新发 /model")
        return model_prefs.Request(permission=parts[1])
    return None
