"""配置管理"""

import os
from pathlib import Path

from dotenv import load_dotenv

# 所有配置与密钥在 ~/.config/code-anywhere/config.env（./setup 生成，权限 600）；
# 仓库里的 .env 是旧位置，只补充配置文件里没有的项（兼容早期安装）
CONFIG_FILE = Path(os.getenv("CODE_ANYWHERE_CONFIG", str(Path.home() / ".config" / "code-anywhere" / "config.env")))
load_dotenv(CONFIG_FILE)
load_dotenv(Path(__file__).parent.parent / ".env")

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
