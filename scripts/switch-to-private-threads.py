#!/usr/bin/env python3
"""同一个 bot 原地切换：群组话题 → 和 bot 私聊里的 Thread。

用法：.venv/bin/python scripts/switch-to-private-threads.py [--yes]
- 不带 --yes 只预览：检查 Threaded Mode、私聊可达，列出要建的 Thread，不做任何改动；
- 带 --yes 执行：要求服务已停（否则运行中的桥接会把旧状态写回去）；
  备份状态与配置到 .migration-backup-<时间>/ → 为每个已绑定项目在私聊建同名 Thread →
  改写按话题号记录的状态 → CHAT_ID 改成用户私聊 → 每个 Thread 发欢迎消息 → 旧群发一条存档说明；
- 中途失败可直接重跑：已建的 Thread 记在备份目录的 thread-map.json 里，不会重复创建；
- 不启停服务（交给开发者）。回滚：停服务，把备份目录里的文件拷回原处，再启动。
"""
import asyncio
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from src import config  # noqa: E402  读 config.env

TOPIC_KEYED = [".bindings.json", ".topic-names.json", ".liveness-cards.json", ".state.json", ".reply-keyboard.json"]
CONFIG_FILES = [config.CONFIG_FILE, REPO / ".env"]
BUILTINS = {"code-anywhere", "issue-desk"}   # 最后建，排在 Thread 列表最上面


def load(name: str, default):
    path = REPO / name
    return json.loads(path.read_text()) if path.exists() else default


def dump(name: str, data) -> None:
    (REPO / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def service_running() -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{config.BOT_PORT}/health", timeout=2)
        return True
    except OSError:
        return False


def set_chat_id(path: Path, chat_id: int) -> bool:
    if not path.exists():
        return False
    lines = path.read_text().splitlines()
    hit = [i for i, line in enumerate(lines) if line.split("=", 1)[0].strip() == "CHAT_ID"]
    for i in hit:
        lines[i] = f"CHAT_ID={chat_id}"
    if hit:
        path.write_text("\n".join(lines) + "\n")
    return bool(hit)


def plan_names(bindings: dict[int, str]) -> list[tuple[int, str, str]]:
    """(旧话题号, 路径, Thread 名)；同名项目用父目录区分，与 sync_all_projects 的规则一致。"""
    counts: dict[str, int] = {}
    for path in bindings.values():
        counts[Path(path).name] = counts.get(Path(path).name, 0) + 1
    plan = []
    for tid, path in bindings.items():
        name = Path(path).name
        if counts[name] > 1:
            name = f"{name} ({Path(path).parent.name})"
        plan.append((tid, path, name))
    return sorted(plan, key=lambda x: (x[2] in BUILTINS, x[0]))


def backup_dir() -> Path:
    """沿用上次没跑完的备份目录（里面的 thread-map.json 记着已建的 Thread），否则新建。"""
    for old in sorted(REPO.glob(".migration-backup-*"), reverse=True):
        if (old / "thread-map.json").exists() and not (old / "DONE").exists():
            return old
    path = REPO / f".migration-backup-{time.strftime('%Y%m%d-%H%M%S')}"
    path.mkdir()
    for name in TOPIC_KEYED:
        if (REPO / name).exists():
            shutil.copy2(REPO / name, path / name)
    for cfg in CONFIG_FILES:
        if cfg.exists():
            shutil.copy2(cfg, path / f"config--{cfg.name}")
    (path / "SOURCES.json").write_text(json.dumps({f"config--{c.name}": str(c) for c in CONFIG_FILES}, indent=2))
    return path


async def main(execute: bool) -> None:
    from telegram import Bot
    users = sorted(u for u in config.ALLOWED_USERS if u)
    if len(users) != 1:
        sys.exit(f"ALLOWED_USERS 需要恰好一个用户（私聊的对象），现在是 {users}")
    user_id = users[0]
    old_chat = int(config.CHAT_ID)
    if old_chat > 0:
        sys.exit(f"CHAT_ID={old_chat} 已经是私聊，无需切换")
    bindings = {int(k): v for k, v in load(".bindings.json", {}).items()}
    plan = plan_names(bindings)

    bot = Bot(config.BOT_TOKEN)
    async with bot:
        me = await bot.get_me()
        if not me.has_topics_enabled:
            sys.exit(f"@{me.username} 还没开启 Threaded Mode（@BotFather → Bot Settings → Threaded Mode）")
        await bot.get_chat(user_id)   # 用户必须先私聊过 bot
        print(f"@{me.username}：群组 {old_chat} → 私聊 {user_id}，共 {len(plan)} 个 Thread")
        for tid, path, name in plan:
            print(f"  topic {tid:>6} → {name:28} {path}")
        if not execute:
            print("\n预览完毕，未做改动。停服务后加 --yes 执行。")
            return
        if service_running():
            sys.exit("服务还在运行：先停服务再执行（运行中的桥接会把旧状态写回去）")

        backup = backup_dir()
        map_file = backup / "thread-map.json"
        mapping = json.loads(map_file.read_text()) if map_file.exists() else {}
        print(f"备份：{backup.name}/" + (f"（续跑，已建 {len(mapping)} 个）" if mapping else ""))

        for tid, path, name in plan:
            if str(tid) in mapping:
                continue
            while True:
                try:
                    topic = await bot.create_forum_topic(user_id, name=name)
                    break
                except Exception as exc:   # RetryAfter 等限频：按提示等待后重试
                    wait = getattr(exc, "retry_after", None)
                    if not wait:
                        raise
                    wait = wait.total_seconds() if hasattr(wait, "total_seconds") else wait
                    print(f"  … 限频，等 {wait:.0f}s")
                    await asyncio.sleep(float(wait) + 1)
            mapping[str(tid)] = {"thread": topic.message_thread_id, "name": name, "path": path}
            map_file.write_text(json.dumps(mapping, ensure_ascii=False, indent=2))
            print(f"  ✓ {name:28} → Thread {topic.message_thread_id}")
            await asyncio.sleep(1.2)   # 私聊约 1 条/秒

        new_of = {int(k): v["thread"] for k, v in mapping.items()}
        dump(".bindings.json", {str(v["thread"]): v["path"] for v in mapping.values()})
        dump(".topic-names.json", {str(v["thread"]): {"base": v["name"], "applied": v["name"]} for v in mapping.values()})
        (REPO / ".liveness-cards.json").unlink(missing_ok=True)    # 旧状态卡在群里，与私聊无关
        (REPO / ".reply-keyboard.json").unlink(missing_ok=True)
        st = load(".state.json", {})
        st["topic_dirs"] = {str(new_of[int(k)]): v for k, v in st.get("topic_dirs", {}).items() if int(k) in new_of}
        dump(".state.json", st)
        changed = [str(p) for p in CONFIG_FILES if set_chat_id(p, user_id)]
        print(f"状态已改写；CHAT_ID → {user_id}（{', '.join(changed)}）")

        welcomed = set(json.loads((backup / "welcomed.json").read_text())) if (backup / "welcomed.json").exists() else set()
        from src.bot import _send_topic_welcome
        for v in mapping.values():
            if v["thread"] in welcomed:
                continue
            try:
                await _send_topic_welcome(bot, user_id, v["thread"], v["name"], v["path"])
            except Exception as exc:
                print(f"  ! 欢迎消息失败 {v['name']}：{exc}")
            welcomed.add(v["thread"])
            (backup / "welcomed.json").write_text(json.dumps(sorted(welcomed)))
            await asyncio.sleep(1.2)

        try:
            await bot.send_message(old_chat, f"📦 code-anywhere 已切换到和 @{me.username} 的私聊 Thread。"
                                             "这个群保留作历史存档，之后不再更新。")
        except Exception as exc:
            print(f"  ! 旧群存档说明发送失败：{exc}")
        (backup / "DONE").write_text(time.strftime("%F %T"))

    print(f"\n✦ 切换完成，现在可以启动服务。回滚：停服务，按 {backup.name}/SOURCES.json 把配置拷回，其余文件拷回仓库根目录。")


if __name__ == "__main__":
    asyncio.run(main("--yes" in sys.argv))
