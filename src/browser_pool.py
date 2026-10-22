"""共享浏览器池：所有会话通过 Playwright MCP 使用，需要人时 TG 一键接管（2026-10-22 用户需求）。

- 槽位：一块 Xvfb 虚拟屏幕 + 一个有头 Chromium（CDP 只监听本机）。用 systemd-run 跑成独立的
  用户单元，桥接重启不会带走浏览器；用到才启动，空闲回收。
- 租用：每个 CLI 进程（pid:启动时间）的 Playwright MCP 经桥接中转连 CDP，第一次连上时租一个槽位，
  断开（Playwright MCP 空闲超时会主动断开）后归还。一个槽位同一时间只给一个会话，互不抢标签页。
- 共用身份：所有项目共用一个登录身份。槽位归还、人工接管结束、槽位停止时导出 cookie 到共享
  存储，并推给另一个在运行的槽位；槽位启动时导入。
- 人工接管：会话请求帮忙 → 本机起 x11vnc（一次性密码）→ ssh 反向隧道到中转机 → 中转机上临时起
  noVNC（websockify）→ 会话所在 Thread 收到 [打开浏览器][我弄好了]。结束或超时后全部停掉，
  中转机上被临时让出端口的原有服务按原命令恢复。接管期间暂停模型对这个浏览器的操作。
"""
import asyncio
import json
import logging
import os
import secrets
import shlex
import time
from pathlib import Path

import aiohttp
from aiohttp import web

log = logging.getLogger("code-anywhere.browser")

SLOTS = int(os.getenv("BROWSER_POOL_SLOTS", "2"))
BASE = Path(os.getenv("BROWSER_POOL_DIR", str(Path.home() / ".local/share/code-anywhere/browser")))
COOKIES = BASE / "cookies.json"
SCREEN = os.getenv("BROWSER_POOL_SCREEN", "1280x800")
IDLE_STOP = int(os.getenv("BROWSER_POOL_IDLE_STOP", "1800"))   # 无人租用这么久就停掉槽位
RELEASE_GRACE = 20                                              # 断开后稍等，给重连留余地
HELP_TIMEOUT = int(os.getenv("BROWSER_HELP_TIMEOUT", "900"))
# 远程查看经中转机（手机能访问的机器）转出去；不配置则人工接管不可用
RELAY_HOST = os.getenv("BROWSER_HELP_SSH_HOST", "")             # ssh 主机名（~/.ssh/config 里的 Host）
RELAY_LISTEN = os.getenv("BROWSER_HELP_LISTEN", "")             # 中转机对外监听，如 10.0.1.188:6801
RELAY_PUBLIC_URL = os.getenv("BROWSER_HELP_PUBLIC_URL", "")     # 手机打开的地址，如 https://nas.im:36801
RELAY_PORT = int(os.getenv("BROWSER_HELP_RELAY_PORT", "16801"))  # 中转机本机上的隧道口
RELAY_WEBSOCKIFY = os.getenv("BROWSER_HELP_WEBSOCKIFY", "")     # 中转机上起 noVNC 的命令前缀（不含监听与目标）

_leases: dict[int, dict] = {}        # 槽位 → {client, session_id, cwd, since, conns, release_task}
_last_used: dict[int, float] = {}
_help: dict[str, dict] = {}          # 接管编号 → 状态
_gates: dict[int, asyncio.Event] = {}  # 槽位 → 未接管时 set；接管期间 clear，暂停模型操作
_lock = asyncio.Lock()


def display(slot: int) -> int:
    return 120 + slot


def cdp_port(slot: int) -> int:
    return 9320 + slot


def vnc_port(slot: int) -> int:
    return 5920 + slot


def unit(slot: int) -> str:
    return f"ca-browser-{slot}"


def chrome_binary() -> str:
    found = sorted((Path.home() / ".cache/ms-playwright").glob("chromium-*/chrome-linux*/chrome"))
    if found:
        return str(found[-1])
    for name in ("chromium", "chromium-browser", "google-chrome"):
        for folder in os.getenv("PATH", "").split(":"):
            if (Path(folder) / name).exists():
                return str(Path(folder) / name)
    raise RuntimeError("找不到 Chromium：请运行 npx playwright install chromium")


def gate(slot: int) -> asyncio.Event:
    if slot not in _gates:
        _gates[slot] = asyncio.Event()
        _gates[slot].set()
    return _gates[slot]


async def _run(*args: str, timeout: float = 20) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "timeout"
    return proc.returncode, out.decode(errors="replace")


async def _version(slot: int) -> dict | None:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2), trust_env=False) as http:
            async with http.get(f"http://127.0.0.1:{cdp_port(slot)}/json/version") as resp:
                return await resp.json(content_type=None)
    except Exception:
        return None


async def _resource_check() -> tuple[bool, str]:
    from src.config import PROJECT_ROOT
    script = PROJECT_ROOT / "scripts/browser-resource-check"
    if not script.exists():
        return True, ""
    code, out = await _run(str(script))
    return code == 0, out.strip()


async def start_slot(slot: int) -> None:
    if await _version(slot):
        return
    ok, detail = await _resource_check()
    if not ok:
        raise RuntimeError(f"浏览器资源检查未通过，暂不启动新浏览器：{detail}")
    profile = BASE / f"slot-{slot}"
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    width, height = SCREEN.split("x")
    chrome = [chrome_binary(), f"--remote-debugging-port={cdp_port(slot)}", "--remote-debugging-address=127.0.0.1",
              f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
              "--disable-dev-shm-usage", "--password-store=basic", "--window-position=0,0",
              f"--window-size={width},{height}", "--start-maximized", "about:blank"]
    script = (f"Xvfb :{display(slot)} -screen 0 {SCREEN}x24 -nolisten tcp -noreset & "
              f"for i in $(seq 50); do [ -e /tmp/.X11-unix/X{display(slot)} ] && break; sleep 0.1; done; "
              f"DISPLAY=:{display(slot)} exec {shlex.join(chrome)}")
    await _run("systemctl", "--user", "reset-failed", unit(slot))
    code, out = await _run("systemd-run", "--user", f"--unit={unit(slot)}", "--collect",
                           "--property=KillMode=control-group", "/bin/bash", "-c", script)
    if code != 0:
        raise RuntimeError(f"启动浏览器槽位 {slot} 失败：{out.strip()}")
    for _ in range(100):
        if await _version(slot):
            break
        await asyncio.sleep(0.2)
    else:
        raise RuntimeError(f"浏览器槽位 {slot} 启动后 20 秒内没有响应")
    await import_cookies(slot)
    log.info("[browser] 槽位 %s 已启动 display=:%s cdp=%s", slot, display(slot), cdp_port(slot))


async def stop_slot(slot: int) -> None:
    if await _version(slot):
        await export_cookies(slot)
    await _run("systemctl", "--user", "stop", unit(slot))
    log.info("[browser] 槽位 %s 已停止", slot)


async def _cdp(slot: int, method: str, params: dict | None = None):
    info = await _version(slot)
    if not info:
        return None
    async with aiohttp.ClientSession(trust_env=False) as http:
        async with http.ws_connect(info["webSocketDebuggerUrl"], max_msg_size=0) as ws:
            await ws.send_json({"id": 1, "method": method, "params": params or {}})
            async for msg in ws:
                data = json.loads(msg.data)
                if data.get("id") == 1:
                    return data.get("result")
    return None


def _load_cookies() -> list[dict]:
    try:
        return json.loads(COOKIES.read_text())
    except (OSError, ValueError):
        return []


async def export_cookies(slot: int) -> None:
    """把这个槽位的 cookie 合并进共享身份，并推给另一个在运行的槽位。"""
    result = await _cdp(slot, "Storage.getCookies")
    if not result:
        return
    merged = {(c["name"], c["domain"], c["path"]): c for c in _load_cookies()}
    merged.update({(c["name"], c["domain"], c["path"]): c for c in result.get("cookies", [])})
    cookies = list(merged.values())
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = COOKIES.with_suffix(".tmp")
    tmp.write_text(json.dumps(cookies))
    tmp.chmod(0o600)
    tmp.replace(COOKIES)
    for other in range(1, SLOTS + 1):
        if other != slot and await _version(other):
            await import_cookies(other)


async def import_cookies(slot: int) -> None:
    cookies = [{k: v for k, v in c.items() if k not in {"size", "session", "priority", "sourcePort",
                                                         "sourceScheme", "partitionKey"}} for c in _load_cookies()]
    if cookies:
        await _cdp(slot, "Storage.setCookies", {"cookies": cookies})


# ---------- 租用 ----------

def lease_of(client: str) -> int | None:
    return next((slot for slot, lease in _leases.items() if lease["client"] == client), None)


async def _owner(client: str) -> dict:
    """client = CLI 进程的 pid-启动时间，对应会话列表里 backend:pane:pid:start。"""
    from src import agent_sessions
    pid_start = client.split("-", 1)
    for session in await agent_sessions.discover():
        if session["session_id"].split(":")[-2:] == pid_start:
            return session
    return {}


async def acquire(client: str) -> int:
    async with _lock:
        slot = lease_of(client)
        if slot is None:
            free = [s for s in range(1, SLOTS + 1) if s not in _leases]
            if not free:
                busy = "、".join(f"{lease.get('project') or lease['client']}" for lease in _leases.values())
                raise RuntimeError(f"共享浏览器的 {SLOTS} 个槽位都在使用中（{busy}），请稍后重试")
            # 优先用已经开着的槽位，省一次启动
            running = [s for s in free if await _version(s)]
            slot = (running or free)[0]
            owner = await _owner(client)
            _leases[slot] = {"client": client, "session_id": owner.get("session_id"), "cwd": owner.get("cwd"),
                             "project": owner.get("project"), "since": time.time(), "conns": 0,
                             "release_task": None}
            log.info("[browser] 槽位 %s 租给 %s（%s）", slot, client, owner.get("project"))
        lease = _leases[slot]
        if lease.get("release_task"):
            lease["release_task"].cancel()
            lease["release_task"] = None
    try:
        await start_slot(slot)
    except Exception:
        async with _lock:
            if _leases.get(slot, {}).get("client") == client and not _leases[slot]["conns"]:
                _leases.pop(slot, None)
        raise
    _last_used[slot] = time.time()
    return slot


async def _release_later(slot: int, client: str) -> None:
    await asyncio.sleep(RELEASE_GRACE)
    async with _lock:
        lease = _leases.get(slot)
        if not lease or lease["client"] != client or lease["conns"]:
            return
        if any(h["slot"] == slot and h["status"] == "waiting" for h in _help.values()):
            return  # 人还在操作，等接管结束再还
        _leases.pop(slot, None)
    _last_used[slot] = time.time()
    try:
        await export_cookies(slot)
    except Exception:
        log.warning("[browser] 槽位 %s 导出 cookie 失败", slot, exc_info=True)
    log.info("[browser] 槽位 %s 已归还（%s）", slot, client)


def _schedule_release(slot: int, client: str) -> None:
    lease = _leases.get(slot)
    if lease and lease["client"] == client and not lease["conns"]:
        lease["release_task"] = asyncio.create_task(_release_later(slot, client))


async def monitor() -> None:
    """空闲回收：没人租用超过 IDLE_STOP 的槽位停掉；接管超时收尾。"""
    while True:
        await asyncio.sleep(60)
        now = time.time()
        for help_id, item in list(_help.items()):
            if item["status"] == "waiting" and now - item["started"] > HELP_TIMEOUT:
                asyncio.create_task(finish_help(help_id, "timeout"))
        for slot in range(1, SLOTS + 1):
            if slot in _leases or now - _last_used.get(slot, 0) < IDLE_STOP:
                continue
            if await _version(slot):
                try:
                    await stop_slot(slot)
                except Exception:
                    log.warning("[browser] 停止槽位 %s 失败", slot, exc_info=True)


# ---------- CDP 中转（Playwright MCP 连这里） ----------

async def handle_version(request: web.Request) -> web.Response:
    client = request.match_info["client"]
    try:
        slot = await acquire(client)
    except RuntimeError as exc:
        return web.json_response({"error": str(exc)}, status=503)
    info = await _version(slot) or {}
    upstream = info.get("webSocketDebuggerUrl", "")
    path = upstream.split(f":{cdp_port(slot)}", 1)[-1]
    info["webSocketDebuggerUrl"] = f"ws://{request.host}/browser/cdp/{client}/ws?path={path}"
    return web.json_response(info)


async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    client = request.match_info["client"]
    try:
        slot = await acquire(client)
    except RuntimeError as exc:
        return web.json_response({"error": str(exc)}, status=503)
    path = request.query.get("path") or ((await _version(slot)) or {}).get(
        "webSocketDebuggerUrl", "").split(f":{cdp_port(slot)}", 1)[-1]
    downstream = web.WebSocketResponse(max_msg_size=0, heartbeat=None)
    await downstream.prepare(request)
    _leases[slot]["conns"] += 1
    try:
        async with aiohttp.ClientSession(trust_env=False) as http:
            async with http.ws_connect(f"ws://127.0.0.1:{cdp_port(slot)}{path}", max_msg_size=0) as upstream:
                async def to_browser():
                    async for msg in downstream:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            break
                        await gate(slot).wait()   # 人工接管期间暂停模型操作
                        _last_used[slot] = time.time()
                        await upstream.send_str(msg.data)

                async def to_client():
                    async for msg in upstream:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            break
                        await downstream.send_str(msg.data)

                tasks = [asyncio.create_task(to_browser()), asyncio.create_task(to_client())]
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in tasks:
                    task.cancel()
    finally:
        lease = _leases.get(slot)
        if lease and lease["client"] == client:
            lease["conns"] -= 1
            _schedule_release(slot, client)
        await downstream.close()
    return downstream


# ---------- 人工接管 ----------

def help_available() -> bool:
    return bool(RELAY_HOST and RELAY_LISTEN and RELAY_PUBLIC_URL and RELAY_WEBSOCKIFY)


def _relay_script(action: str) -> str:
    """中转机上的启停脚本。原来占着对外端口的服务先记下命令再停，结束后按原命令恢复。"""
    host, port = RELAY_LISTEN.rsplit(":", 1)
    if action == "start":
        return f"""set -e
mkdir -p ~/.code-anywhere && cd ~/.code-anywhere
if [ -f novnc.pid ] && kill -0 "$(cat novnc.pid)" 2>/dev/null; then kill "$(cat novnc.pid)"; sleep 0.5; fi
old=$(lsof -nP -t -iTCP@{host}:{port} -sTCP:LISTEN 2>/dev/null | head -1 || true)
if [ -n "$old" ]; then
  [ -f saved.cmd ] || ps -o command= -p "$old" > saved.cmd
  [ -f saved.cwd ] || lsof -a -p "$old" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' > saved.cwd
  kill "$old"; for i in $(seq 30); do kill -0 "$old" 2>/dev/null || break; sleep 0.1; done
fi
nohup {RELAY_WEBSOCKIFY} {host}:{port} 127.0.0.1:{RELAY_PORT} > novnc.log 2>&1 &
echo $! > novnc.pid
sleep 1; kill -0 "$(cat novnc.pid)" && echo started
"""
    return """cd ~/.code-anywhere 2>/dev/null || exit 0
if [ -f novnc.pid ]; then kill "$(cat novnc.pid)" 2>/dev/null; rm -f novnc.pid; fi
sleep 0.5
if [ -f saved.cmd ]; then
  dir=$(cat saved.cwd 2>/dev/null); [ -n "$dir" ] && cd "$dir"
  nohup $(cat ~/.code-anywhere/saved.cmd) > ~/.code-anywhere/restored.log 2>&1 &
  rm -f ~/.code-anywhere/saved.cmd ~/.code-anywhere/saved.cwd
  echo restored
fi
"""


async def _relay(action: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", RELAY_HOST, "bash -s",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await asyncio.wait_for(proc.communicate(_relay_script(action).encode()), 40)
    if proc.returncode != 0:
        raise RuntimeError(f"中转机 {action} 失败：{out.decode(errors='replace').strip()[-300:]}")
    return out.decode(errors="replace").strip()


async def request_help(source_session_id: str, reason: str) -> dict:
    if not help_available():
        return {"status": "unavailable", "detail": "未配置远程查看（BROWSER_HELP_* 环境变量），无法请用户接管"}
    client = "-".join(source_session_id.split(":")[-2:])
    slot = lease_of(client)
    if slot is None:
        return {"status": "not_leased", "detail": "你还没有在用共享浏览器：先用浏览器工具打开需要用户操作的页面，再请求帮忙"}
    existing = next((k for k, h in _help.items() if h["slot"] == slot and h["status"] == "waiting"), None)
    if existing:
        return {"status": "waiting", "help_id": existing, "detail": "已经在等用户操作这个浏览器"}
    from src import projects
    from src.api_server import _bot
    from src import config
    lease = _leases[slot]
    topic = projects.get_topic(lease.get("cwd") or "")
    if not _bot or not topic:
        return {"status": "unavailable", "detail": "这个项目没有绑定 TG Thread，无法通知用户"}
    help_id = secrets.token_hex(4)
    password = secrets.token_urlsafe(6)[:8]
    item = {"slot": slot, "client": client, "session_id": lease.get("session_id"), "reason": reason[:300],
            "started": time.time(), "status": "waiting", "procs": [], "topic": topic}
    _help[help_id] = item
    try:
        vnc = await asyncio.create_subprocess_exec(
            "x11vnc", "-display", f":{display(slot)}", "-localhost", "-rfbport", str(vnc_port(slot)),
            "-passwd", password, "-forever", "-shared", "-noxdamage", "-quiet",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        item["procs"].append(vnc)
        tunnel = await asyncio.create_subprocess_exec(
            "ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
            "-R", f"127.0.0.1:{RELAY_PORT}:127.0.0.1:{vnc_port(slot)}", RELAY_HOST,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        item["procs"].append(tunnel)
        await asyncio.sleep(2)
        if tunnel.returncode is not None or vnc.returncode is not None:
            raise RuntimeError("本机 VNC 或 ssh 隧道没有起来")
        await _relay("start")
        item["relay"] = True
        gate(slot).clear()
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        url = f"{RELAY_PUBLIC_URL.rstrip('/')}/vnc.html?autoconnect=1&resize=scale&password={password}"
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🖥 打开浏览器", url=url),
                                          InlineKeyboardButton("✅ 我弄好了", callback_data=f"bh:done:{help_id}",
                                                               style="success")]])
        minutes = HELP_TIMEOUT // 60
        sent = await _bot.send_message(
            chat_id=config.CHAT_ID, message_thread_id=topic, reply_markup=keyboard, parse_mode=None,
            text=f"🖥 需要你在共享浏览器里操作：{item['reason']}\n\n点“打开浏览器”进入，弄好后点“我弄好了”。"
                 f"链接 {minutes} 分钟内有效，期间会话暂停操作这个浏览器。")
        item["message_id"] = sent.message_id
    except Exception as exc:
        log.warning("[browser] 发起接管失败", exc_info=True)
        await finish_help(help_id, "failed", notify=False)
        return {"status": "failed", "detail": str(exc)}
    log.info("[browser] 接管 %s 槽位 %s：%s", help_id, slot, item["reason"])
    return {"status": "waiting", "help_id": help_id,
            "detail": "已在 TG 请用户操作；用户点“我弄好了”或超时后，你会收到一条消息，也可用 browser_help_status 查询"}


async def finish_help(help_id: str, status: str, notify: bool = True) -> None:
    item = _help.get(help_id)
    if not item or item["status"] != "waiting":
        return
    item["status"] = status
    item["finished"] = time.time()
    gate(item["slot"]).set()
    for proc in item.pop("procs", []):
        if proc.returncode is None:
            proc.terminate()
    if item.pop("relay", None):
        try:
            await _relay("stop")
        except Exception:
            log.warning("[browser] 中转机收尾失败", exc_info=True)
    try:
        await export_cookies(item["slot"])
    except Exception:
        log.warning("[browser] 导出 cookie 失败", exc_info=True)
    from src.api_server import _bot
    from src import config
    if _bot and item.get("message_id"):
        label = {"done": "✅ 已完成", "timeout": "⌛ 已超时，远程查看已关闭", "failed": "⚠️ 未能打开远程查看"}[status]
        try:
            await _bot.edit_message_text(chat_id=config.CHAT_ID, message_id=item["message_id"], parse_mode=None,
                                         text=f"🖥 共享浏览器：{item['reason']}\n\n{label}")
        except Exception:
            pass
    lease = _leases.get(item["slot"])
    if lease and not lease["conns"]:
        _schedule_release(item["slot"], lease["client"])
    if notify and item.get("session_id"):
        from src import agent_sessions
        text = {"done": f"用户已在共享浏览器里完成操作（{item['reason']}），请继续。",
                "timeout": f"用户没有在 {HELP_TIMEOUT // 60} 分钟内完成共享浏览器里的操作（{item['reason']}），远程查看已关闭；需要时可重新请求。"}.get(status)
        if text:
            try:
                await agent_sessions.send_message(item["session_id"], text, f"browser-help-{help_id}",
                                                  sender="code-anywhere", urgent=True)
            except Exception:
                log.warning("[browser] 通知会话失败", exc_info=True)
    log.info("[browser] 接管 %s 结束：%s", help_id, status)


def help_status(help_id: str) -> dict:
    item = _help.get(help_id)
    if not item:
        return {"status": "unknown", "detail": "没有这个接管编号"}
    return {"status": item["status"], "reason": item["reason"], "started": item["started"],
            "finished": item.get("finished")}


def status() -> dict:
    return {"slots": SLOTS, "help_available": help_available(),
            "leases": {slot: {k: v for k, v in lease.items() if k != "release_task"} for slot, lease in _leases.items()},
            "help": {k: help_status(k) for k in _help}}


async def handle_help(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        return web.json_response(await request_help(data["source_session_id"], str(data.get("reason") or "")))
    except (KeyError, ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def handle_help_status(request: web.Request) -> web.Response:
    return web.json_response(help_status(request.match_info["id"]))


async def handle_status(request: web.Request) -> web.Response:
    result = status()
    result["running"] = [s for s in range(1, SLOTS + 1) if await _version(s)]
    return web.json_response(result)


def add_routes(app: web.Application) -> None:
    app.router.add_get("/browser/cdp/{client}/json/version", handle_version)
    app.router.add_get("/browser/cdp/{client}/json/version/", handle_version)
    app.router.add_get("/browser/cdp/{client}/ws", handle_ws)
    app.router.add_post("/browser/help", handle_help)
    app.router.add_get("/browser/help/{id}", handle_help_status)
    app.router.add_get("/browser/status", handle_status)
