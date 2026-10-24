"""本地 issue：项目还没有 GitHub / GitLab 远端时也能用 issue 流程（2026-10-24 用户需求）。

存在 code-anywhere 自己的数据目录（不进项目仓库，免得 issue 的增删改变成一堆提交），按项目目录区分。
命令行 `local-issue` 的子命令和 `gh issue` 对齐（create / list / view / comment / edit / close），
标签与远端一样用 agent:todo → doing → to-verify。项目有了远端就一定用远端（2026-10-24 用户决定）：只要项目已经有 GitHub / GitLab 远端，再对它执行任何 local-issue 命令，
都会先把没关闭的本地 issue 自动迁移过去，再提示改用 gh / glab；不做定时扫描，也不问要不要迁移。

/issues 用到的字段与远端统一：project_id 为负数（-项目编号），iid 为项目内编号，references.full 为 local:<目录名>#N。
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

DB = Path(os.getenv("CODE_ANYWHERE_DATA", str(Path.home() / ".local/share/code-anywhere"))) / "issues.sqlite3"
FILES = Path(os.getenv("ISSUE_FILES", str(Path.home() / ".local/share/issue-files"))) / "local"
STATE_LABELS = ["agent:todo", "agent:doing", "agent:to-verify", "agent:blocked"]


def db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS issues (id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, number INTEGER NOT NULL,
            title TEXT NOT NULL, body TEXT DEFAULT '', labels TEXT DEFAULT '[]', state TEXT DEFAULT 'open',
            state_reason TEXT DEFAULT '', created_at TEXT, updated_at TEXT, migrated_to TEXT DEFAULT '',
            UNIQUE(project_id, number));
        CREATE TABLE IF NOT EXISTS comments (id INTEGER PRIMARY KEY, issue_id INTEGER NOT NULL, body TEXT NOT NULL,
            created_at TEXT);
    """)
    DB.chmod(0o600)
    return conn


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def project_root(path: str | None = None) -> str:
    """-R 可以是目录、local:<目录名>，或省略（当前目录所在的 git 仓库根）。"""
    path = (path or "").removeprefix("local:")
    if path and not os.path.sep in path and not Path(path).exists():
        # 只给了目录名：在已登记的项目和 ~/workspace 下找
        with db() as conn:
            row = conn.execute("SELECT path FROM projects WHERE path LIKE ?", (f"%/{path}",)).fetchone()
        if row:
            return row["path"]
        candidate = Path.home() / "workspace" / path
        if candidate.exists():
            path = str(candidate)
    start = Path(path or os.getcwd()).resolve()
    try:
        top = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"], capture_output=True,
                             text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        top = ""
    return top or str(start)


def project_id(path: str, create: bool = True) -> int | None:
    with db() as conn:
        row = conn.execute("SELECT id FROM projects WHERE path=?", (path,)).fetchone()
        if row:
            return row["id"]
        if not create:
            return None
        return conn.execute("INSERT INTO projects(path) VALUES (?)", (path,)).lastrowid


def _issue(conn, pid: int, number: int):
    row = conn.execute("SELECT * FROM issues WHERE project_id=? AND number=?", (pid, number)).fetchone()
    if not row:
        raise SystemExit(f"没有这个本地 issue：#{number}")
    return row


def normalize(row, path: str, comments: list | None = None) -> dict:
    """与 GitLab / GitHub 相同的字段，供 /issues 使用。"""
    issue = {"project_id": -row["project_id"], "iid": row["number"], "title": row["title"], "body": row["body"],
             "labels": json.loads(row["labels"]), "state": "opened" if row["state"] == "open" else "closed",
             "references": {"full": f"local:{Path(path).name}#{row['number']}"}, "web_url": "",
             "created_at": row["created_at"], "local_path": path, "migrated_to": row["migrated_to"]}
    if comments is not None:
        issue["comments"] = comments
    return issue


# ---------------------------------------------------------------- 供 issues.py 调用

def open_issues() -> list[dict]:
    from src.issues import verify_hint
    result = []
    with db() as conn:
        for row in conn.execute("SELECT i.*, p.path FROM issues i JOIN projects p ON p.id=i.project_id "
                                "WHERE i.state='open' ORDER BY i.created_at"):
            issue = normalize(row, row["path"])
            if "agent:to-verify" in issue["labels"]:
                notes = [{"body": c["body"], "created_at": c["created_at"]}
                         for c in conn.execute("SELECT * FROM comments WHERE issue_id=?", (row["id"],))]
                issue["verify_hint"] = verify_hint(notes)
            result.append(issue)
    return result


def get(project: int, number: int) -> dict:
    with db() as conn:
        path = conn.execute("SELECT path FROM projects WHERE id=?", (-project,)).fetchone()["path"]
        return normalize(_issue(conn, -project, number), path)


def comment(project: int, number: int, body: str) -> None:
    with db() as conn:
        row = _issue(conn, -project, number)
        conn.execute("INSERT INTO comments(issue_id, body, created_at) VALUES (?,?,?)", (row["id"], body, now()))
        conn.execute("UPDATE issues SET updated_at=? WHERE id=?", (now(), row["id"]))


def relabel(project: int, number: int, remove: list[str], add: list[str], close: str | None = None) -> dict:
    with db() as conn:
        row = _issue(conn, -project, number)
        labels = [l for l in json.loads(row["labels"]) if l not in remove]
        labels += [l for l in add if l not in labels]
        conn.execute("UPDATE issues SET labels=?, updated_at=? WHERE id=?", (json.dumps(labels), now(), row["id"]))
        if close:
            conn.execute("UPDATE issues SET state='closed', state_reason=? WHERE id=?", (close, row["id"]))
    return {}


# ---------------------------------------------------------------- 迁移到远端

def remote_of(path: str) -> tuple[str, str] | None:
    """项目的 git 远端 → (平台, owner/repo)；没有远端返回 None。"""
    try:
        url = subprocess.run(["git", "-C", path, "remote", "get-url", "origin"], capture_output=True, text=True,
                             timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not url:
        return None
    from src.issues import repo_path
    repo = repo_path(url)
    if not repo:
        return None
    return ("github" if "github.com" in url else "gitlab"), repo


def _localize_images(text: str, platform: str, repo: str) -> str:
    """local-issue 附件（local-issue-file://…）换成远端可见的图片。"""
    import re
    tool = "issue-upload"

    def replace(match):
        name, rel = match.group(1), match.group(2)
        local = FILES / rel
        if not local.exists():
            return match.group(0)
        out = subprocess.run([tool, repo, str(local)], capture_output=True, text=True, timeout=120,
                             env={**os.environ, "ISSUE_PLATFORM_OVERRIDE": platform})
        return out.stdout.strip() or match.group(0)
    return re.sub(r"!\[([^\]]*)\]\(local-issue-file://([^)]+)\)", replace, text)


def migrate(project: int) -> dict:
    """没关闭的本地 issue 逐条建到远端；成功的本地关闭并记下远端编号，失败的留在本地。"""
    with db() as conn:
        path = conn.execute("SELECT path FROM projects WHERE id=?", (project,)).fetchone()["path"]
        rows = list(conn.execute("SELECT * FROM issues WHERE project_id=? AND state='open' ORDER BY number",
                                 (project,)))
        all_comments = {r["id"]: list(conn.execute("SELECT * FROM comments WHERE issue_id=? ORDER BY id", (r["id"],)))
                        for r in rows}
    remote = remote_of(path)
    if not remote:
        return {"migrated": [], "failed": [], "detail": "项目还没有远端"}
    platform, repo = remote
    migrated, failed = [], []
    for row in rows:
        body = row["body"] or ""
        notes = all_comments[row["id"]]
        if notes:
            body += "\n\n---\n**本地 issue 的评论（迁移时合并）**\n\n" + "\n\n".join(
                f"> {c['created_at']}\n\n{c['body']}" for c in notes)
        body += f"\n\n---\n由本地 issue local:{Path(path).name}#{row['number']} 迁移"
        try:
            body = _localize_images(body, platform, repo)
        except Exception:
            pass
        labels = ",".join(json.loads(row["labels"]))
        if platform == "github":
            cmd = ["gh", "issue", "create", "-R", repo, "--title", row["title"], "--body", body]
            if labels:
                cmd += ["--label", labels]
        else:
            cmd = ["glab", "issue", "create", "-R", repo, "--title", row["title"], "--description", body, "--yes"]
            if labels:
                cmd += ["--label", labels]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        url = next((w for w in result.stdout.split() if w.startswith("http")), "")
        if result.returncode or not url:
            failed.append({"number": row["number"], "title": row["title"],
                           "error": (result.stderr or result.stdout).strip()[-200:]})
            continue
        with db() as conn:
            conn.execute("UPDATE issues SET state='closed', state_reason='migrated', migrated_to=?, updated_at=? "
                         "WHERE id=?", (url, now(), row["id"]))
            conn.execute("INSERT INTO comments(issue_id, body, created_at) VALUES (?,?,?)",
                         (row["id"], f"已迁移到 {url}", now()))
        migrated.append({"number": row["number"], "url": url})
    return {"migrated": migrated, "failed": failed, "platform": platform, "repo": repo}


# ---------------------------------------------------------------- 命令行（与 gh issue 对齐）

def _print_issue(row, path, comments=None) -> None:
    labels = ", ".join(json.loads(row["labels"]))
    print(f"local:{Path(path).name}#{row['number']}  {row['title']}")
    print(f"状态：{row['state']}{'（' + row['state_reason'] + '）' if row['state_reason'] else ''}   标签：{labels or '—'}")
    if row["migrated_to"]:
        print(f"已迁移到：{row['migrated_to']}")
    print(f"创建：{row['created_at']}\n\n{row['body']}")
    for c in comments or []:
        print(f"\n--- 评论 {c['created_at']}\n{c['body']}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="local-issue", description="本地 issue（项目还没有远端时用；用法同 gh issue）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    def repo_arg(p):
        p.add_argument("-R", "--repo", help="项目目录或 local:<目录名>；省略为当前目录所在仓库")
    p = sub.add_parser("create"); repo_arg(p)
    p.add_argument("--title", "-t", required=True); p.add_argument("--body", "-b", default="")
    p.add_argument("--label", "-l", default="agent:todo")
    p = sub.add_parser("list"); repo_arg(p)
    p.add_argument("--label", "-l"); p.add_argument("--state", "-s", default="open", choices=["open", "closed", "all"])
    p.add_argument("--all-projects", action="store_true")
    p = sub.add_parser("view"); repo_arg(p); p.add_argument("number", type=int); p.add_argument("--comments", "-c", action="store_true")
    p = sub.add_parser("comment"); repo_arg(p); p.add_argument("number", type=int); p.add_argument("--body", "-b", required=True)
    p = sub.add_parser("edit"); repo_arg(p); p.add_argument("number", type=int)
    p.add_argument("--add-label", default=""); p.add_argument("--remove-label", default="")
    p = sub.add_parser("close"); repo_arg(p); p.add_argument("number", type=int)
    p.add_argument("--reason", "-r", default="completed", choices=["completed", "not_planned"])
    p.add_argument("--comment", "-c", default="")
    p = sub.add_parser("migrate", help="把没关闭的本地 issue 迁移到项目的 GitHub / GitLab（一般不用手动跑）"); repo_arg(p)
    args = parser.parse_args(argv)

    path = project_root(getattr(args, "repo", None))
    if not (args.cmd == "list" and args.all_projects):
        remote = remote_of(path)
        if remote:   # 有远端就一定用远端：先把没关闭的迁过去
            platform, repo = remote
            cli = "gh" if platform == "github" else "glab"
            pid = project_id(path, create=False)
            if pid is not None:
                with db() as conn:
                    open_count = conn.execute("SELECT COUNT(*) FROM issues WHERE project_id=? AND state='open'",
                                              (pid,)).fetchone()[0]
                if open_count:
                    result = migrate(pid)
                    print(f"这个项目已经有远端（{repo}），没关闭的本地 issue 已自动迁移：")
                    for m in result["migrated"]:
                        print(f"  local:{Path(path).name}#{m['number']} → {m['url']}")
                    for f in result["failed"]:
                        print(f"  ⚠️ local:{Path(path).name}#{f['number']} 没迁移成功，仍在本地：{f['error']}")
            if args.cmd in ("view", "list"):
                pass   # 查看历史（含“已迁移到”记录）照常可用
            else:
                sys.exit(f"这个项目已经有远端（{repo}），请改用 {cli} issue（参数相同）。")
    if args.cmd == "list" and args.all_projects:
        with db() as conn:
            rows = list(conn.execute("SELECT i.*, p.path FROM issues i JOIN projects p ON p.id=i.project_id ORDER BY p.path, i.number"))
    else:
        pid = project_id(path, create=args.cmd == "create")
        if pid is None:
            print("这个项目还没有本地 issue")
            return
        if args.cmd == "create":
            labels = [l.strip() for l in args.label.split(",") if l.strip()]
            with db() as conn:
                number = (conn.execute("SELECT MAX(number) FROM issues WHERE project_id=?", (pid,)).fetchone()[0] or 0) + 1
                conn.execute("INSERT INTO issues(project_id, number, title, body, labels, created_at, updated_at) "
                             "VALUES (?,?,?,?,?,?,?)", (pid, number, args.title, args.body, json.dumps(labels), now(), now()))
            print(f"local:{Path(path).name}#{number}")
            return
        if args.cmd == "migrate":
            print(json.dumps(migrate(pid), ensure_ascii=False, indent=2))
            return
        with db() as conn:
            if args.cmd == "list":
                rows = list(conn.execute("SELECT i.*, ? AS path FROM issues i WHERE project_id=? ORDER BY number", (path, pid)))
            else:
                row = _issue(conn, pid, args.number)
                if args.cmd == "view":
                    notes = list(conn.execute("SELECT * FROM comments WHERE issue_id=? ORDER BY id", (row["id"],))) if args.comments else None
                    _print_issue(row, path, notes)
                    return
        if args.cmd == "comment":
            comment(-pid, args.number, args.body)
        elif args.cmd == "edit":
            relabel(-pid, args.number, [l for l in args.remove_label.split(",") if l], [l for l in args.add_label.split(",") if l])
        elif args.cmd == "close":
            if args.comment:
                comment(-pid, args.number, args.comment)
            relabel(-pid, args.number, STATE_LABELS, ["agent:wontfix"] if args.reason == "not_planned" else [], close=args.reason)
        if args.cmd != "list":
            print(f"ok local:{Path(path).name}#{args.number}")
            return
    shown = 0
    for row in rows:
        labels = json.loads(row["labels"])
        if args.state != "all" and row["state"] != args.state:
            continue
        if args.label and args.label not in labels:
            continue
        shown += 1
        print(f"local:{Path(row['path']).name}#{row['number']}\t{row['state']}\t{','.join(labels)}\t{row['title']}")
    if not shown:
        print("没有符合条件的本地 issue")


if __name__ == "__main__":
    main(sys.argv[1:])
