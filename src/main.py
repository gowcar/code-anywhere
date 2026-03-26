"""Code Anywhere — 入口"""

import asyncio

from src.config import BOT_TOKEN, BOT_PORT, ALLOW_ALL, ALLOWED_USERS
from src.log import setup_logging
from src.bot import create_app
from src.api_server import start_api_server, set_bot
from src import projects as proj

BANNER = r"""
   ___          _        _                _
  / __\___   __| | ___  / \   _ __  _   _| |    | |__   ___ _ __ ___
 / /  / _ \ / _` |/ _ \/  /\ | '_ \| | | \ \ /\ / / '_ \ / _ \ '__/ _ \
/ /__| (_) | (_| |  __/\_/ \\ | | | | |_| |\ V  V /| | | |  __/ | |  __/
\____/\___/ \__,_|\___\/   \/ |_| |_|\__, | \_/\_/ |_| |_|\___|_|  \___|
                                      |___/
"""


def main():
    if not BOT_TOKEN:
        print("错误: 请在 .env 中设置 BOT_TOKEN")
        return

    log = setup_logging()

    auth = "所有人" if ALLOW_ALL else str(ALLOWED_USERS)
    discovered = proj.discover()
    bindings = proj.all_bindings()

    print(BANNER)
    print(f"  授权:    {auth}")
    print(f"  Hook:    http://127.0.0.1:{BOT_PORT}")
    print(f"  项目:    {len(discovered)} 个 ({len(bindings)} 已绑定)")
    for p in discovered:
        status = "✓" if p["bound"] else " "
        print(f"           [{status}] {p['name']} ({p['sessions']} sessions)")
    print()

    app = create_app()

    # 启动 HTTP API server（Hook 回调）
    async def post_init_extra(application):
        set_bot(application.bot, application)
        await start_api_server()

    original_post_init = app.post_init
    async def combined_post_init(application):
        await original_post_init(application)
        await post_init_extra(application)
    app.post_init = combined_post_init

    log.info("Bot 启动")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
