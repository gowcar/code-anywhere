"""/issues：按 agent:* 状态汇总 issue，直接调 API，不经过模型。支持 GitLab 和 GitHub。

数据源是“我创建的、未关闭的 issue”（issue-desk 用的就是这个账号的令牌），
所以新仓库无需配置；按 agent:* 标签分组展示。

平台：.env 的 ISSUE_PLATFORM=github|gitlab；不写时有 GitLab 令牌文件就是 gitlab，否则 github。
GitHub 的 issue 也整理成和 GitLab 一样的字段（project_id=仓库数字 ID、iid=编号、references.full、web_url），
界面和按钮两边通用。
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from html import escape
from pathlib import Path

TOKEN_FILE = Path.home() / ".config" / "gitlab-token"
GITHUB_TOKEN_FILE = Path.home() / ".config" / "github-token"


def platform() -> str:
    explicit = os.getenv("ISSUE_PLATFORM", "").lower()
    if explicit in ("github", "gitlab"):
        return explicit
    return "gitlab" if TOKEN_FILE.exists() else "github"


def platform_name() -> str:
    return "GitHub" if platform() == "github" else "GitLab"


def configured() -> bool:
    return bool(_github_token()) if platform() == "github" else TOKEN_FILE.exists()


def _github_token() -> str:
    if GITHUB_TOKEN_FILE.exists():
        return GITHUB_TOKEN_FILE.read_text().strip()
    import shutil
    import subprocess
    gh = shutil.which("gh") or str(Path.home() / ".local/bin/gh")
    try:
        return subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _gh(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(f"https://api.github.com/{path.lstrip('/')}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {_github_token()}",
                                          "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


def _gh_normalize(issue: dict, full: str) -> dict:
    return {"project_id": issue.get("repository", {}).get("id") or _gh_repo_id(full), "iid": issue["number"],
            "title": issue.get("title", ""), "labels": [l["name"] for l in issue.get("labels", [])],
            "references": {"full": f"{full}#{issue['number']}"}, "web_url": issue.get("html_url", ""),
            "created_at": issue.get("created_at", ""), "state": issue.get("state")}


_repo_names: dict[int, str] = {}


def _gh_repo_id(full: str) -> int:
    repo_id = _gh("GET", f"repos/{full}")["id"]
    _repo_names[repo_id] = full
    return repo_id


def _gh_full(project_id: int) -> str:
    if project_id not in _repo_names:
        _repo_names[project_id] = _gh("GET", f"repositories/{project_id}")["full_name"]
    return _repo_names[project_id]


def _gh_fetch_open() -> list[dict]:
    issues, page = [], 1
    while True:
        batch = _gh("GET", f"issues?filter=created&state=open&per_page=100&page={page}")
        for item in batch:
            if "pull_request" in item:
                continue
            full = item["repository"]["full_name"]
            _repo_names[item["repository"]["id"]] = full
            issue = _gh_normalize(item, full)
            if "agent:to-verify" in issue["labels"]:
                comments = _gh("GET", f"repos/{full}/issues/{item['number']}/comments?per_page=100")
                issue["verify_hint"] = verify_hint([{"body": c.get("body", ""), "created_at": c.get("created_at", "")}
                                                    for c in comments])
            issues.append(issue)
        if len(batch) < 100:
            return issues
        page += 1


def _gh_relabel(project_id: int, iid: int, remove: list[str], add: list[str], close: str | None = None) -> dict:
    full = _gh_full(project_id)
    for label in remove:
        try:
            _gh("DELETE", f"repos/{full}/issues/{iid}/labels/{urllib.parse.quote(label, safe='')}")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
    if add:
        _gh("POST", f"repos/{full}/issues/{iid}/labels", {"labels": add})
    if close:
        return _gh("PATCH", f"repos/{full}/issues/{iid}", {"state": "closed", "state_reason": close})
    return {}
STATES = [  # (标签, 标题)，按展示顺序
    ("agent:to-verify", "🧪 待验证"),
    ("agent:blocked", "⛔ 阻塞，需要你决定"),
    ("agent:doing", "🔧 处理中"),
    ("agent:todo", "📥 待处理"),
]
ACTION_LABELS = [label for label, _ in STATES]   # 放弃时一并摘掉的状态标签


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
    if platform() == "github":
        return _gh_fetch_open()
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


def _write(method: str, path: str, host: str | None = None, **params) -> dict:
    host = host or os.getenv("GITLAB_HOST", "git.garena.com")
    token = TOKEN_FILE.read_text().strip()
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"https://{host}/api/v4/{path}", data=data, method=method,
                                 headers={"PRIVATE-TOKEN": token})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def get_issue(project_id: int, iid: int) -> dict:
    if platform() == "github":
        full = _gh_full(project_id)
        return _gh_normalize(_gh("GET", f"repos/{full}/issues/{iid}"), full)
    host = os.getenv("GITLAB_HOST", "git.garena.com")
    return _get(host, TOKEN_FILE.read_text().strip(), f"projects/{project_id}/issues/{iid}")


def note(project_id: int, iid: int, body: str) -> None:
    if platform() == "github":
        _gh("POST", f"repos/{_gh_full(project_id)}/issues/{iid}/comments", {"body": body})
        return
    _write("POST", f"projects/{project_id}/issues/{iid}/notes", body=body)


def verify_pass(project_id: int, iid: int) -> dict:
    note(project_id, iid, "✅ 用户在 TG 验证通过，关闭。")
    if platform() == "github":
        return _gh_relabel(project_id, iid, ["agent:to-verify"], [], close="completed")
    return _write("PUT", f"projects/{project_id}/issues/{iid}", state_event="close",
                  remove_labels="agent:to-verify")


def verify_fail(project_id: int, iid: int) -> dict:
    note(project_id, iid, "↩️ 用户在 TG 验证未通过，退回待处理（原因见后续评论）。")
    if platform() == "github":
        return _gh_relabel(project_id, iid, ["agent:to-verify"], ["agent:todo"])
    return _write("PUT", f"projects/{project_id}/issues/{iid}",
                  remove_labels="agent:to-verify", add_labels="agent:todo")


def abandon(project_id: int, iid: int) -> dict:
    note(project_id, iid, "🗑 用户在 TG 决定放弃处理，关闭。")
    if platform() == "github":
        return _gh_relabel(project_id, iid, list(ACTION_LABELS), ["agent:wontfix"], close="not_planned")
    return _write("PUT", f"projects/{project_id}/issues/{iid}", state_event="close",
                  remove_labels=",".join(ACTION_LABELS), add_labels="agent:wontfix")


def short_ref(issue: dict) -> str:
    repo, _, num = issue.get("references", {}).get("full", f"#{issue.get('iid')}").rpartition("#")
    return f"{repo.rsplit('/', 1)[-1]}#{num}"


def confirm_keyboard(project_id: int, iid: int, ref: str):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(f"确认放弃 {ref}", callback_data=f"is:dropok:{project_id}:{iid}", style="danger"),
        InlineKeyboardButton("取消", callback_data=f"is:open:{project_id}:{iid}"),
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


# ---------------------------------------------------------------- 列表 + 详情两层视图（手机上按钮与 issue 一一对应）

STATE_ICON = {"agent:to-verify": "🧪", "agent:blocked": "⛔", "agent:doing": "🔧", "agent:todo": "📥"}
STATE_NAME = {"agent:to-verify": "待验证", "agent:blocked": "阻塞，需要你决定", "agent:doing": "处理中", "agent:todo": "待处理"}


def ordered(issues: list[dict]) -> list[dict]:
    """只保留带 agent:* 状态的 issue，按 待验证 → 阻塞 → 处理中 → 待处理、再按创建时间排序。"""
    order = [label for label, _ in STATES]
    tagged = [i for i in issues if _state(i)]
    return sorted(tagged, key=lambda i: (order.index(_state(i)), i.get("created_at", "")))


def aliases(issues: list[dict]) -> dict[str, str]:
    """仓库简称：去掉各仓库名共同的 “-” 前缀（digital-buddy-ios → ios）；去光了就用最后一段（digital-buddy → buddy）。"""
    names = {i.get("references", {}).get("full", "").rpartition("#")[0] for i in issues}
    names.discard("")
    parts = {n: n.rsplit("/", 1)[-1].split("-") for n in names}
    common = 0
    if len(parts) > 1:
        for segs in zip(*parts.values()):
            if len(set(segs)) != 1:
                break
            common += 1
    result = {}
    for name, segs in parts.items():
        rest = segs[common:] if common else segs
        result[name] = "-".join(rest) if rest and common else ("-".join(segs) if not common else segs[-1])
    return result


def alias_ref(issue: dict, names: dict[str, str]) -> str:
    repo, _, num = issue.get("references", {}).get("full", "").rpartition("#")
    return f"{names.get(repo, repo.rsplit('/', 1)[-1])}#{num}"


def _key(issue: dict) -> str:
    return f"{issue['project_id']}:{issue['iid']}"


def overview(issues: list[dict]):
    """第一层：计数 + 每个 issue 一个按钮（状态图标 + 简称），点开看详情。"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    items = ordered(issues)
    if not items:
        return "📋 没有未关闭的 agent issue", InlineKeyboardMarkup([[InlineKeyboardButton("🔄 刷新", callback_data="is:list:0:0")]])
    names = aliases(items)
    counts = " · ".join(f"{STATE_ICON[label]} {STATE_NAME[label].split('，')[0]} {n}"
                        for label, _ in STATES if (n := sum(1 for i in items if _state(i) == label)))
    lines = [f"📋 <b>Issue</b>（共 {len(items)} 个）", counts, ""]
    lines += [f"{STATE_ICON[_state(i)]} <b>{escape(alias_ref(i, names))}</b> {escape(_clip(i.get('title', ''), 26))}"
              for i in items]
    lines.append("\n点下面的按钮打开某一条，按钮只作用于打开的那一条。")
    buttons = [InlineKeyboardButton(f"{STATE_ICON[_state(i)]} {alias_ref(i, names)}", callback_data=f"is:open:{_key(i)}")
               for i in items]
    rows = [buttons[k:k + 3] for k in range(0, len(buttons), 3)][:30]
    rows.append([InlineKeyboardButton("🔄 刷新", callback_data="is:list:0:0")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def detail(issues: list[dict], project_id: int, iid: int):
    """第二层：单条 issue 的完整信息，操作按钮只针对这一条；上一个 / 下一个 / 返回列表。"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    items = ordered(issues)
    index = next((k for k, i in enumerate(items) if i["project_id"] == project_id and i["iid"] == iid), None)
    if index is None:
        return None
    issue, names = items[index], aliases(items)
    state, key = _state(issue), _key(issue)
    lines = [f"{STATE_ICON[state]} <b>{STATE_NAME[state]}</b> · {escape(issue.get('references', {}).get('full', ''))}",
             f"<b>{escape(issue.get('title', ''))}</b>"]
    if state == "agent:to-verify":
        hint = issue.get("verify_hint")
        lines += ["", f"验证：{escape(hint)}" if hint else "⚠️ 负责会话还没写验证方法，可在 issue-desk 追问"]
    lines += ["", f'<a href="{escape(issue["web_url"], quote=True)}">在 {platform_name()} 打开</a> · 第 {index + 1}/{len(items)} 个']
    if state == "agent:to-verify":
        actions = [InlineKeyboardButton("✅ 通过", callback_data=f"is:pass:{key}", style="success"),
                   InlineKeyboardButton("↩️ 不通过", callback_data=f"is:fail:{key}", style="primary")]
    else:
        actions = [InlineKeyboardButton(f"🗑 放弃 {alias_ref(issue, names)}", callback_data=f"is:drop:{key}", style="danger")]
    nav = []
    if index > 0:
        nav.append(InlineKeyboardButton("◀ 上一个", callback_data=f"is:open:{_key(items[index - 1])}"))
    nav.append(InlineKeyboardButton("返回列表", callback_data="is:list:0:0"))
    if index < len(items) - 1:
        nav.append(InlineKeyboardButton("下一个 ▶", callback_data=f"is:open:{_key(items[index + 1])}"))
    return "\n".join(lines), InlineKeyboardMarkup([actions, nav])


def next_to_verify(issues: list[dict]) -> dict | None:
    return next((i for i in ordered(issues) if _state(i) == "agent:to-verify"), None)


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[:width - 1] + "…"
