#!/usr/bin/env python3
"""把 code-anywhere 的入口从“群组话题”搬到“新 bot 的私聊 Thread”。

用法：.venv/bin/python scripts/migrate-to-private-threads.py <新 bot token 文件> [--yes]
- 先停服务、备份全部状态文件到 .migration-backup-<时间>/（出问题可原样恢复）；
- 旧 bot 在旧群发一条迁移说明（群保留作存档，不删）；
- .env 换成新 bot、CHAT_ID 改为用户私聊；为每个已绑定项目建同名私聊 Thread 并重新绑定；
- 跳过已标为“历史 · …”的旧话题（不知道原名、不在 .topic-names.json 里的）；
- 每个 Thread 发欢迎消息（含最近对话），最后启动服务。会话原生历史、recon 外脑不受影响。
"""
import asyncio
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
STATE_FILES = [".env", ".bindings.json", ".topic-names.json", ".liveness-cards.json", ".state.json",
               ".watch-offsets.json", ".backends.json", ".permissions.json", ".model-prefs.json"]


def read_env() -> dict:
    values = {}
    for line in (REPO / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def write_env(updates: dict) -> None:
    lines, done = (REPO / ".env").read_text().splitlines(), set()
    for i, line in enumerate(lines):
        key = line.lstrip("# ").split("=", 1)[0].strip()
        if "=" in line and key in updates and key not in done:
            lines[i] = f"{key}={updates[key]}"
            done.add(key)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in done]
    (REPO / ".env").write_text("\n".join(lines) + "\n")


async def main(token_file: str, assume_yes: bool) -> None:
    from telegram import Bot
    from src import state
    new_token = re.search(r"\d{6,12}:[A-Za-z0-9_-]{30,}", Path(token_file).expanduser().read_text()).group(0)
    env = read_env()
    user_id = int(env["ALLOWED_USERS"].split(",")[0])
    old_token, old_chat = env["BOT_TOKEN"], int(env["CHAT_ID"])
    bindings = {int(k): v for k, v in json.loads((REPO / ".bindings.json").read_text()).items()}
    names = json.loads((REPO / ".topic-names.json").read_text()) if (REPO / ".topic-names.json").exists() else {}
    plan = [(tid, path, names[str(tid)]["base"]) for tid, path in bindings.items() if str(tid) in names]
    skipped = [(tid, path) for tid, path in bindings.items() if str(tid) not in names]

    new_bot, old_bot = Bot(new_token), Bot(old_token)
    async with new_bot, old_bot:
        me = await new_bot.get_me()
        assert me.has_topics_enabled, "新 bot 没有开启 Thread（在 @BotFather 打开 Threaded Mode）"
        await new_bot.get_chat(user_id)  # 用户必须先给新 bot 发过消息
        print(f"新 bot @{me.username}；迁移 {len(plan)} 个项目，跳过 {len(skipped)} 个历史话题：")
        for tid, path, name in plan:
            print(f"  {name:24} {path}")
        for tid, path in skipped:
            print(f"  （跳过）topic {tid} {path}")
        if not assume_yes and input("继续？[y/N] ").strip().lower() != "y":
            return

        backup = REPO / f".migration-backup-{time.strftime('%Y%m%d-%H%M%S')}"
        backup.mkdir()
        subprocess.run(["systemctl", "--user", "stop", "code-anywhere"], check=False)
        for name in STATE_FILES:
            if (REPO / name).exists():
                shutil.copy2(REPO / name, backup / name)
        print(f"已停服务并备份到 {backup.name}/")

        # 清掉新 bot 上积压的更新（如用户的 Hello、测试改名的系统消息），免得服务启动后误处理
        pending = await new_bot.get_updates(timeout=1)
        if pending:
            await new_bot.get_updates(offset=pending[-1].update_id + 1, timeout=1)

        await old_bot.send_message(old_chat, f"📦 code-anywhere 已迁移到私聊 @{me.username}（Thread 模式）。"
                                             "这个群保留作历史存档，之后不再更新。")

        new_bindings, new_names = {}, {}
        for _, path, name in plan:
            topic = await new_bot.create_forum_topic(user_id, name=name)
            new_bindings[str(topic.message_thread_id)] = path
            new_names[str(topic.message_thread_id)] = {"base": name, "applied": name}
            print(f"  ✓ {name} → Thread {topic.message_thread_id}")
            await asyncio.sleep(1.2)  # 私聊约 1 条/秒
        (REPO / ".bindings.json").write_text(json.dumps(new_bindings, ensure_ascii=False, indent=2) + "\n")
        (REPO / ".topic-names.json").write_text(json.dumps(new_names, ensure_ascii=False, indent=2) + "\n")
        (REPO / ".liveness-cards.json").unlink(missing_ok=True)  # 旧卡片在旧群里，与新入口无关
        state.load()
        state.topic_dirs.clear()
        for tid, path in new_bindings.items():
            state.set_cwd(int(tid), path)
        write_env({"BOT_TOKEN": new_token, "CHAT_ID": str(user_id)})
        print(".env 已切换到新 bot，CHAT_ID 为你的私聊")

        from src.bot import _send_topic_welcome
        for tid, path in new_bindings.items():
            await _send_topic_welcome(new_bot, user_id, int(tid), new_names[tid]["base"], path)
            await asyncio.sleep(1.2)

    subprocess.run(["systemctl", "--user", "start", "code-anywhere"], check=False)
    print(f"服务已启动。回滚：停服务后把 {backup.name}/ 里的文件拷回仓库根目录再启动。")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], "--yes" in sys.argv))
