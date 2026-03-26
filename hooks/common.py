"""Hook 共享工具"""

import json
import os
import sys
import urllib.request


def get_bot_url():
    port = os.environ.get("CA_BOT_PORT", "5000")
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
