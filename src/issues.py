"""/issues：按 agent:* 状态汇总 GitLab issue，直接调 API，不经过模型。

数据源是“我创建的、未关闭的 issue”（issue-desk 用的就是这个账号的令牌），
所以新仓库无需配置；按 agent:* 标签分组展示。
"""
import json
import os
import re
import urllib.parse
import urllib.request
from html import escape
from pathlib import Path

TOKEN_FILE = Path.home() / ".config" / "gitlab-token"
STATES = [  # (标签, 标题)，按展示顺序
    ("agent:to-verify", "🧪 待验证"),
    ("agent:blocked", "⛔ 阻塞，需要你决定"),
    ("agent:doing", "🔧 处理中"),
    ("agent:todo", "📥 待处理"),
]


# 待验证 issue 的评论里约定有一行「验证方法：…」（兼容早期的“如何验证/验证方式”）
VERIFY_LINE = re.compile(r"^[\s>*_\-]*(?:验证方法|如何验证|验证方式)[*_]*\s*[:：]\s*(.+)$")


def _get(host: str, token: str, path: str, **params) -> list | dict:
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"https://{host}/api/v4/{path}?{query}", headers={"PRIVATE-TOKEN": token})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def verify_hint(notes: list[dict]) -> str | None:
    """最新一条带「验证方法：」的评论里的那一行。"""
    for note in sorted(notes, key=lambda n: n.get("created_at", ""), reverse=True):
        for line in note.get("body", "").splitlines():
            match = VERIFY_LINE.match(line)
            if match:
                return match.group(1).strip().strip("*_ ")
    return None


def fetch_open_issues(host: str | None = None) -> list[dict]:
    host = host or os.getenv("GITLAB_HOST", "git.garena.com")
    token = TOKEN_FILE.read_text().strip()
    issues, page = [], 1
    while True:
        batch = _get(host, token, "issues", scope="created_by_me", state="opened", per_page=100, page=page)
        issues += batch
        if len(batch) < 100:
            break
        page += 1
    for issue in issues:
        if "agent:to-verify" in issue.get("labels", []):
            notes = _get(host, token, f"projects/{issue['project_id']}/issues/{issue['iid']}/notes", per_page=100)
            issue["verify_hint"] = verify_hint([n for n in notes if not n.get("system")])
    return issues


def format_summary(issues: list[dict]) -> str:
    groups = {label: [] for label, _ in STATES}
    for issue in issues:
        label = next((l for l, _ in STATES if l in issue.get("labels", [])), None)
        if label:
            groups[label].append(issue)
    total = sum(len(v) for v in groups.values())
    if not total:
        return "📋 没有未关闭的 agent issue"
    lines = [f"📋 <b>Issue 状态</b>（共 {total} 个未关闭）"]
    for label, title in STATES:
        if not groups[label]:
            continue
        lines.append(f"\n<b>{title}</b>（{len(groups[label])}）")
        for issue in sorted(groups[label], key=lambda i: i.get("created_at", "")):
            ref = issue.get("references", {}).get("full", f"#{issue.get('iid')}")
            repo, _, num = ref.rpartition("#")
            short = f"{repo.rsplit('/', 1)[-1]}#{num}"
            lines.append(f'• <a href="{escape(issue["web_url"], quote=True)}">{escape(short)}</a> '
                         f'{escape(issue.get("title", ""))}')
            if label == "agent:to-verify":
                hint = issue.get("verify_hint")
                lines.append(f"　↳ 验证：{escape(hint[:200])}" if hint else "　↳ ⚠️ 未写验证方法，可在 issue-desk 追问")
    lines.append("\n验证通过/不通过，在 issue-desk 话题里说即可。")
    return "\n".join(lines)


# ---------------------------------------------------------------- 按钮操作（/issues 汇总消息上的按钮）

ACTION_LABELS = ("agent:todo", "agent:doing", "agent:to-verify", "agent:blocked")


def _write(method: str, path: str, host: str | None = None, **params) -> dict:
    host = host or os.getenv("GITLAB_HOST", "git.garena.com")
    token = TOKEN_FILE.read_text().strip()
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"https://{host}/api/v4/{path}", data=data, method=method,
                                 headers={"PRIVATE-TOKEN": token})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def get_issue(project_id: int, iid: int) -> dict:
    host = os.getenv("GITLAB_HOST", "git.garena.com")
    return _get(host, TOKEN_FILE.read_text().strip(), f"projects/{project_id}/issues/{iid}")


def note(project_id: int, iid: int, body: str) -> None:
    _write("POST", f"projects/{project_id}/issues/{iid}/notes", body=body)


def verify_pass(project_id: int, iid: int) -> dict:
    note(project_id, iid, "✅ 用户在 TG 验证通过，关闭。")
    return _write("PUT", f"projects/{project_id}/issues/{iid}", state_event="close",
                  remove_labels="agent:to-verify")


def verify_fail(project_id: int, iid: int) -> dict:
    note(project_id, iid, "↩️ 用户在 TG 验证未通过，退回待处理（原因见后续评论）。")
    return _write("PUT", f"projects/{project_id}/issues/{iid}",
                  remove_labels="agent:to-verify", add_labels="agent:todo")


def abandon(project_id: int, iid: int) -> dict:
    note(project_id, iid, "🗑 用户在 TG 决定放弃处理，关闭。")
    return _write("PUT", f"projects/{project_id}/issues/{iid}", state_event="close",
                  remove_labels=",".join(ACTION_LABELS), add_labels="agent:wontfix")


def short_ref(issue: dict) -> str:
    repo, _, num = issue.get("references", {}).get("full", f"#{issue.get('iid')}").rpartition("#")
    return f"{repo.rsplit('/', 1)[-1]}#{num}"


def keyboard(issues: list[dict]):
    """待验证：[✅ 通过][↩️ 不通过]；待处理/处理中/阻塞：[🗑 放弃]；最后一行 [🔄 刷新]。"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    rows = []
    ordered = sorted(issues, key=lambda i: ([l for l, _ in STATES].index(_state(i)) if _state(i) else 9,
                                            i.get("created_at", "")))
    for issue in ordered:
        state, ref = _state(issue), short_ref(issue)
        key = f"{issue['project_id']}:{issue['iid']}"
        if state == "agent:to-verify":
            rows.append([InlineKeyboardButton(f"✅ {ref} 通过", callback_data=f"is:pass:{key}"),
                         InlineKeyboardButton(f"↩️ {ref} 不通过", callback_data=f"is:fail:{key}")])
        elif state:
            rows.append([InlineKeyboardButton(f"🗑 放弃 {ref}", callback_data=f"is:drop:{key}")])
    rows.append([InlineKeyboardButton("🔄 刷新", callback_data="is:refresh:0:0")])
    return InlineKeyboardMarkup(rows[:30])


def confirm_keyboard(project_id: int, iid: int, ref: str):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"确认放弃 {ref}", callback_data=f"is:dropok:{project_id}:{iid}"),
        InlineKeyboardButton("取消", callback_data="is:refresh:0:0"),
    ]])


def _state(issue: dict) -> str | None:
    return next((l for l, _ in STATES if l in issue.get("labels", [])), None)


def repo_path(url: str) -> str | None:
    """git 远端地址 → group/repo（兼容 ssh 与 https）。"""
    match = re.search(r"(?:@[^:/]+[:/]|://[^/]+/)(.+?)(?:\.git)?/?$", url.strip())
    return match.group(1) if match else None


def owner_cwd(issue: dict) -> str | None:
    """issue 所在仓库对应的已绑定项目目录（按各项目 git 远端匹配；同仓库多个 worktree 取主目录）。"""
    import subprocess
    from src import projects
    repo = issue.get("references", {}).get("full", "").rpartition("#")[0]
    matches = []
    for cwd in projects.all_bindings().values():
        try:
            url = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"], capture_output=True,
                                 text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if repo and repo_path(url) == repo:
            matches.append(cwd)
    return min(matches, key=len) if matches else None


async def notify_owner(issue: dict, text: str) -> str:
    """用一句话通知负责该仓库的会话；返回给用户看的结果说明。"""
    from src import agent_sessions
    cwd = owner_cwd(issue)
    if not cwd:
        return "没找到负责这个仓库的会话，它下次查 issue 时会看到"
    sessions = [s for s in await agent_sessions.discover() if s.get("cwd") == cwd]
    if not sessions:
        return f"{Path(cwd).name} 的会话没有在运行，它下次查 issue 时会看到"
    request_id = f"issues-{issue['project_id']}-{issue['iid']}-{int(__import__('time').time())}"
    result = await agent_sessions.send_message(sessions[0]["session_id"], text, request_id, sender="TG /issues")
    status = result.get("status")
    if status in ("received", "unconfirmed"):
        return f"已通知 {Path(cwd).name}"
    return f"{Path(cwd).name} 正忙，没打断它；它做完手头的事会查到"
