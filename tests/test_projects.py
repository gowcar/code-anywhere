"""项目发现：Claude ∪ Codex 合并、忽略规则。不触网、不碰真实 HOME。"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import projects


def write_rollout(sessions_root: Path, name: str, cwd: str, *, day="2026/09/22", pad=0):
    """造一条 Codex rollout：首行 session_meta 带 cwd，base_instructions 故意很长"""
    d = sessions_root / day
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"rollout-{name}.jsonl"
    meta = {"timestamp": "2026-09-22T12:00:00.000Z", "type": "session_meta",
            "payload": {"session_id": name, "cwd": cwd,
                        "base_instructions": {"text": "x" * pad}}}
    f.write_text(json.dumps(meta, ensure_ascii=False) + "\n", encoding="utf-8")
    return f


def encode(path: str) -> str:
    """claude 的项目目录名：路径分隔符换成 -（模块只提供反向解码）"""
    return str(path).replace("/", "-")


def write_claude_project(projects_root: Path, path: str, n=1):
    d = projects_root / encode(path)
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (d / f"sid-{i}.jsonl").write_text("{}\n", encoding="utf-8")
    return d


class DiscoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.home = self.root / "home"
        self.claude = self.home / ".claude" / "projects"
        self.codex_home = self.home / ".codex"
        self.claude.mkdir(parents=True)
        (self.codex_home / "sessions").mkdir(parents=True)
        # 两个互不嵌套的真实项目目录
        self.proj_a = self.root / "work" / "alpha"
        self.proj_b = self.root / "work" / "beta"
        for p in (self.proj_a, self.proj_b):
            p.mkdir(parents=True)

        self.env = patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)})
        self.env.start()
        self.p_claude = patch.object(projects, "CLAUDE_PROJECTS", self.claude)
        self.p_claude.start()
        self.p_bind = patch.object(projects, "_bindings", {})
        self.p_bind.start()
        self.p_root = patch.object(projects, "PROJECT_ROOT", self.root)
        self.p_root.start()
        self.p_home = patch.object(Path, "home", staticmethod(lambda: self.home))
        self.p_home.start()
        # 测试目录本身就在 /tmp 下，真实的临时目录规则会把它们全吃掉——
        # 单独测忽略规则时再按需打开
        self.p_tmp = patch.object(projects, "_TMP_ROOTS", ())
        self.p_tmp.start()

    def tearDown(self):
        for p in (self.p_tmp, self.p_home, self.p_root, self.p_bind, self.p_claude, self.env):
            p.stop()
        self.tmp.cleanup()

    def names(self):
        return {p["name"]: p for p in projects.discover()}

    def test_codex_only_project_is_discovered(self):
        """从没跑过 claude 的目录，只要有 codex rollout 就该是项目"""
        write_rollout(self.codex_home / "sessions", "a1", str(self.proj_a))
        found = self.names()
        self.assertIn("alpha", found)
        self.assertEqual(found["alpha"]["path"], str(self.proj_a))

    def test_merge_counts_and_mtime(self):
        """同一路径两边都有：会话数相加，mtime 取较新"""
        d = write_claude_project(self.claude, str(self.proj_a), n=2)
        old = 1_600_000_000
        for f in d.glob("*.jsonl"):
            os.utime(f, (old, old))
        r = write_rollout(self.codex_home / "sessions", "a1", str(self.proj_a))
        new = 1_700_000_000
        os.utime(r, (new, new))

        found = self.names()["alpha"]
        self.assertEqual(found["sessions"], 3)          # 2 claude + 1 codex
        self.assertEqual(int(found["mtime"]), new)      # 取较新那个

    def test_cwd_found_even_when_first_line_is_huge(self):
        """base_instructions 可达几十 KB；cwd 在它前面，只读头部也要能命中"""
        write_rollout(self.codex_home / "sessions", "big", str(self.proj_b), pad=200_000)
        self.assertIn("beta", self.names())

    def test_ignores_home(self):
        write_rollout(self.codex_home / "sessions", "h", str(self.home))
        write_rollout(self.codex_home / "sessions", "a1", str(self.proj_a))
        names = self.names()
        self.assertIn("alpha", names)
        self.assertNotIn(self.home.name, names)

    def test_ignores_tmp_roots(self):
        scratch = self.root / "scratch"
        (scratch / "inner").mkdir(parents=True)
        write_rollout(self.codex_home / "sessions", "s", str(scratch / "inner"))
        write_rollout(self.codex_home / "sessions", "a1", str(self.proj_a))
        with patch.object(projects, "_TMP_ROOTS", (str(scratch),)):
            names = self.names()
        self.assertIn("alpha", names)
        self.assertNotIn("inner", names)

    def test_projectignore_file(self):
        write_rollout(self.codex_home / "sessions", "a1", str(self.proj_a))
        write_rollout(self.codex_home / "sessions", "b1", str(self.proj_b))
        (self.root / ".projectignore").write_text(
            f"# 注释行\n\n{self.proj_b}\n", encoding="utf-8")
        names = self.names()
        self.assertIn("alpha", names)
        self.assertNotIn("beta", names)

    def test_exact_ignore_keeps_child_projects(self):
        container = self.proj_a.parent
        write_rollout(self.codex_home / "sessions", "container", str(container))
        write_rollout(self.codex_home / "sessions", "a1", str(self.proj_a))
        write_rollout(self.codex_home / "sessions", "b1", str(self.proj_b))
        (self.root / ".projectignore").write_text(f"={container}\n")
        paths = {p['path'] for p in projects.discover()}
        self.assertNotIn(str(container), paths)
        self.assertIn(str(self.proj_a), paths)
        self.assertIn(str(self.proj_b), paths)

    def test_missing_dir_and_corrupt_rollout_are_skipped(self):
        """指向已删目录的 rollout、坏 JSON 都不能让发现流程炸掉"""
        write_rollout(self.codex_home / "sessions", "gone", str(self.root / "deleted"))
        bad = self.codex_home / "sessions" / "2026/09/22" / "rollout-bad.jsonl"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("not json at all\n", encoding="utf-8")
        write_rollout(self.codex_home / "sessions", "a1", str(self.proj_a))

        names = self.names()
        self.assertIn("alpha", names)
        self.assertNotIn("deleted", names)

    def test_no_codex_dir_still_works(self):
        """没有 ~/.codex 时，claude 那条路不受影响"""
        write_claude_project(self.claude, str(self.proj_a))
        with patch.dict(os.environ, {"CODEX_HOME": str(self.root / "nope")}):
            self.assertIn("alpha", self.names())


if __name__ == "__main__":
    unittest.main()
