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
