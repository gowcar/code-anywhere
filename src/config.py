"""配置管理"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN")

_raw_users = os.getenv("ALLOWED_USERS", "0").strip()
ALLOWED_USERS = set(map(int, _raw_users.split(","))) if _raw_users else {0}
ALLOW_ALL = 0 in ALLOWED_USERS

# Telegram 群组（自动检测或从 .env 读取）
CHAT_ID: int | None = int(os.getenv("CHAT_ID", "0")) or None

# Hook HTTP
BOT_PORT = int(os.getenv("BOT_PORT", "5000"))


# 路径
PROJECT_ROOT = Path(__file__).parent.parent
STATE_FILE = PROJECT_ROOT / ".state.json"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
