"""日志配置

策略：
- 主日志：logs/code-anywhere.log（滚动，保留 7 天）
- 控制台：精简输出
- httpx (TG polling)：只显示 WARNING 以上
"""

import logging
from logging.handlers import TimedRotatingFileHandler

from src.config import LOG_DIR


def setup_logging():
    log_file = LOG_DIR / "code-anywhere.log"

    # 文件 handler：按天滚动，保留 7 天
    file_handler = TimedRotatingFileHandler(
        log_file, when="midnight", backupCount=7, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))

    # 控制台 handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    ))

    # root logger
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # 静默 httpx polling 日志
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    return logging.getLogger("code-anywhere")
