"""Hook 共享工具"""

import json
import os
import sys
import urllib.request


def get_bot_url():
    port = os.environ.get("CA_BOT_PORT") or _config_port()
    return f"http://127.0.0.1:{port}"


def read_stdin():
    return json.loads(sys.stdin.read())


def post_to_bot(endpoint, data, timeout=130):
    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{get_bot_url()}{endpoint}",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except Exception:
        # Bot 不可达 → 静默退出，不干预 Claude
        return None


def _config_port() -> str:
    """hook 由 CLI 拉起，看不到桥接的环境变量：从 ~/.config/code-anywhere/config.env 读端口。"""
    path = os.environ.get("CODE_ANYWHERE_CONFIG") or os.path.expanduser("~/.config/code-anywhere/config.env")
    try:
        for line in open(path, encoding="utf-8"):
            if line.strip().startswith("BOT_PORT="):
                return line.split("=", 1)[1].strip().strip('"') or "5000"
    except OSError:
        pass
    return "5000"
