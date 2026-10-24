"""共享浏览器池：所有会话通过 Playwright MCP 使用，需要人时 TG 一键接管（2026-10-22 用户需求）。

- 槽位：一块 Xvfb 虚拟屏幕 + 一个有头 Chromium（CDP 只监听本机）。用 systemd-run 跑成独立的
  用户单元，桥接重启不会带走浏览器；用到才启动，空闲回收。
- 租用：每个 CLI 进程（pid:启动时间）的 Playwright MCP 经桥接中转连 CDP，第一次连上时租一个槽位，
  断开（Playwright MCP 空闲超时会主动断开）后归还。一个槽位同一时间只给一个会话，互不抢标签页。
- 共用身份：所有项目共用一个登录身份。槽位归还、人工接管结束、槽位停止时导出 cookie 和 localStorage 到共享
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
import sys
import time
from pathlib import Path

import aiohttp
from aiohttp import web

log = logging.getLogger("code-anywhere.browser")

MAX_SLOTS = 4                                                   # 数量硬上限（用户要求不能开太多），配置再大也不超过
SLOTS = max(1, min(int(os.getenv("BROWSER_POOL_SLOTS", "2")), MAX_SLOTS))
MAX_TABS = int(os.getenv("BROWSER_POOL_MAX_TABS", "6"))         # 每个槽位最多这么多标签页
BASE = Path(os.getenv("BROWSER_POOL_DIR", str(Path.home() / ".local/share/code-anywhere/browser")))
COOKIES = BASE / "cookies.json"
STORAGE = BASE / "local-storage.json"          # 各网站 localStorage（登录令牌常在这里）
STORAGE_MAX_PER_ORIGIN = 2 << 20
SCREEN = os.getenv("BROWSER_POOL_SCREEN", "1280x800")
IDLE_STOP = int(os.getenv("BROWSER_POOL_IDLE_STOP", "1800"))   # 无人租用这么久就停掉槽位
RELEASE_GRACE = 20                                              # 断开后稍等，给重连留余地
HELP_TIMEOUT = int(os.getenv("BROWSER_HELP_TIMEOUT", "900"))
MIN_AVAILABLE_GIB = float(os.getenv("BROWSER_POOL_MIN_AVAILABLE_GIB", "2"))
# 远程查看（人工接管）：手机能直接访问本机就用直连模式；不能则经中转机（手机能访问、本机能 ssh 的机器）。
# 不配置则人工接管不可用。设置方法见 docs/REMOTE-VIEW-SETUP.md（给 Agent 照着做的手册）
RELAY_HOST = os.getenv("BROWSER_HELP_SSH_HOST", "")             # 中转机 ssh 主机名；留空 = 直连模式
RELAY_LISTEN = os.getenv("BROWSER_HELP_LISTEN", "")             # noVNC 对外监听（中转机或本机），如 0.0.0.0:6801
RELAY_PUBLIC_URL = os.getenv("BROWSER_HELP_PUBLIC_URL", "")     # 手机打开的地址，如 https://example.com:36801
RELAY_PORT = int(os.getenv("BROWSER_HELP_RELAY_PORT", "16801"))  # 中转机本机上的隧道口
RELAY_WEBSOCKIFY = os.getenv("BROWSER_HELP_WEBSOCKIFY", "")     # 起 noVNC 的命令前缀（不含监听与目标），如 websockify --web /usr/share/novnc
# 对外端口原本被别的服务占着时，结束后用这条命令恢复它。照抄原进程命令行不可靠（依赖它启动时的环境，
# 2026-10-23 实测恢复失败），所以要求配置一条确认能用的命令；不配置就只停不恢复，并在 TG 里说明
RELAY_RESTORE = os.getenv("BROWSER_HELP_RESTORE", "")

IS_MAC = sys.platform == "darwin"
# macOS 没有 Xvfb：槽位直接在桌面上开有界面的 Chrome，远程查看转发系统自带的“屏幕共享”（整个桌面，
# 连接时输入 Mac 登录密码）。用的时候才开隧道、用完就关（2026-10-24 用户确认可接受）
MAC_SCREEN_SHARING_PORT = 5900

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
    caches = [Path.home() / ".cache/ms-playwright", Path.home() / "Library/Caches/ms-playwright"]
    patterns = ["chromium-*/chrome-linux*/chrome",
                "chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium",
                "chromium-*/chrome-mac*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"]
    found = sorted(p for cache in caches for pattern in patterns for p in cache.glob(pattern))
    if found:
        return str(found[-1])
    for name in ("chromium", "chromium-browser", "google-chrome"):
        for folder in os.getenv("PATH", "").split(":"):
            if (Path(folder) / name).exists():
                return str(Path(folder) / name)
    mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if mac_chrome.exists():
        return str(mac_chrome)
    raise RuntimeError("找不到 Chromium：请运行 ./setup --only browser")


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
    """浏览器池只看内存（2026-10-23 用户决定）：池子槽位数固定，不受全局浏览器进程数阈值限制；
    其他会话自行启动浏览器仍按 scripts/browser-resource-check 的全部规则。"""
    try:
        meminfo = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        available = int(meminfo["MemAvailable"].split()[0]) / 1024 / 1024
    except (OSError, KeyError, ValueError):
        return True, ""
    if available < MIN_AVAILABLE_GIB:
        return False, f"系统可用内存 {available:.1f} GiB，低于 {MIN_AVAILABLE_GIB} GiB"
    return True, ""


async def start_slot(slot: int) -> None:
    if await _version(slot):
        return
    ok, detail = await _resource_check()
    if not ok:
        raise RuntimeError(f"内存不足，暂不启动共享浏览器：{detail}")
    profile = BASE / f"slot-{slot}"
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    width, height = SCREEN.split("x")
    chrome = [chrome_binary(), f"--remote-debugging-port={cdp_port(slot)}", "--remote-debugging-address=127.0.0.1",
              f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
              "--disable-dev-shm-usage", "--password-store=basic", f"--window-size={width},{height}"]
    if IS_MAC:
        # 独立会话启动，桥接重启不带走它（launchd 配了 AbandonProcessGroup）；pid 记下来，停止时用
        chrome += [f"--window-position={40 * slot},{40 * slot}", "about:blank"]
        log_file = open(BASE / f"slot-{slot}.log", "ab")
        proc = await asyncio.create_subprocess_exec(*chrome, stdout=log_file, stderr=log_file,
                                                    start_new_session=True)
        (BASE / f"slot-{slot}.pid").write_text(str(proc.pid))
    else:
        chrome += ["--window-position=0,0", "--start-maximized", "about:blank"]
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
    await import_identity(slot)
    log.info("[browser] 槽位 %s 已启动 %s cdp=%s", slot, "桌面" if IS_MAC else f"display=:{display(slot)}", cdp_port(slot))


async def stop_slot(slot: int) -> None:
    if await _version(slot):
        await export_identity(slot)
    if IS_MAC:
        pid_file = BASE / f"slot-{slot}.pid"
        try:
            os.kill(int(pid_file.read_text()), 15)
        except (OSError, ValueError):
            pass
        pid_file.unlink(missing_ok=True)
    else:
        await _run("systemctl", "--user", "stop", unit(slot))
    log.info("[browser] 槽位 %s 已停止", slot)


class _Session:
    """浏览器级 CDP 连接：按 id 等回复，事件交给 on_event。"""

    def __init__(self, ws):
        self.ws, self.next_id, self.on_event = ws, 0, None

    async def send(self, method: str, params: dict | None = None, session: str | None = None, timeout: float = 15):
        self.next_id += 1
        message = {"id": self.next_id, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        await self.ws.send_json(message)
        async def wait():
            async for msg in self.ws:
                data = json.loads(msg.data)
                if data.get("id") == message["id"]:
                    return data
                if "method" in data and self.on_event:
                    await self.on_event(data)
        data = await asyncio.wait_for(wait(), timeout)
        if "error" in data:
            raise RuntimeError(f"{method}: {data['error'].get('message')}")
        return data.get("result", {})


async def _browser(slot: int):
    info = await _version(slot)
    if not info:
        return None
    http = aiohttp.ClientSession(trust_env=False)
    ws = await http.ws_connect(info["webSocketDebuggerUrl"], max_msg_size=0)
    return http, ws


async def _cdp(slot: int, method: str, params: dict | None = None):
    conn = await _browser(slot)
    if not conn:
        return None
    http, ws = conn
    try:
        return await _Session(ws).send(method, params)
    finally:
        await ws.close()
        await http.close()


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _save(path: Path, data) -> None:
    BASE.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.chmod(0o600)
    tmp.replace(path)


def _origin(url: str) -> str | None:
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme in {"http", "https"} and parts.netloc else None


async def _page_storage(slot: int) -> dict[str, dict]:
    """当前打开的各网站的 localStorage（很多网站把登录令牌放这里，例如 SPACE 的 session）。"""
    result = {}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10), trust_env=False) as http:
            pages = await (await http.get(f"http://127.0.0.1:{cdp_port(slot)}/json/list")).json(content_type=None)
            for page in pages:
                origin = _origin(page.get("url", ""))
                if page.get("type") != "page" or not origin or origin in result:
                    continue
                async with http.ws_connect(page["webSocketDebuggerUrl"], max_msg_size=0) as ws:
                    value = await _Session(ws).send("Runtime.evaluate", {
                        "expression": "JSON.stringify(Object.assign({}, localStorage))", "returnByValue": True})
                    data = json.loads(value.get("result", {}).get("value") or "{}")
                    if len(json.dumps(data)) <= STORAGE_MAX_PER_ORIGIN:
                        result[origin] = data
    except Exception:
        log.warning("[browser] 读取槽位 %s 的 localStorage 失败", slot, exc_info=True)
    return result


async def export_identity(slot: int) -> None:
    """把这个槽位的 cookie 和 localStorage 合并进共享身份，并推给另一个在运行的槽位。"""
    result = await _cdp(slot, "Storage.getCookies")
    if result is None:
        return
    merged = {(c["name"], c["domain"], c["path"]): c for c in _load(COOKIES, [])}
    merged.update({(c["name"], c["domain"], c["path"]): c for c in result.get("cookies", [])})
    _save(COOKIES, list(merged.values()))
    storage = _load(STORAGE, {})
    storage.update(await _page_storage(slot))   # 打开着的网站以当前页面为准
    _save(STORAGE, storage)
    for other in range(1, SLOTS + 1):
        if other != slot and await _version(other):
            await import_identity(other)


async def import_identity(slot: int) -> None:
    cookies = [{k: v for k, v in c.items() if k not in {"size", "session", "priority", "sourcePort",
                                                         "sourceScheme", "partitionKey"}} for c in _load(COOKIES, [])]
    if cookies:
        await _cdp(slot, "Storage.setCookies", {"cookies": cookies})
    storage = _load(STORAGE, {})
    if storage:
        try:
            await _write_storage(slot, storage)
        except Exception:
            log.warning("[browser] 写入槽位 %s 的 localStorage 失败", slot, exc_info=True)


async def _write_storage(slot: int, storage: dict[str, dict]) -> None:
    """localStorage 只能在该网站的页面里写：开一个临时标签页，把对该网站首页的请求在浏览器内部
    拦下来直接回一个空白页（不访问网络），写完关掉。"""
    conn = await _browser(slot)
    if not conn:
        return
    http, ws = conn
    cdp = _Session(ws)
    target = None
    try:
        target = (await cdp.send("Target.createTarget", {"url": "about:blank"}))["targetId"]
        session = (await cdp.send("Target.attachToTarget", {"targetId": target, "flatten": True}))["sessionId"]

        async def on_event(event):
            if event.get("method") == "Fetch.requestPaused" and event.get("sessionId") == session:
                await ws.send_json({"id": 0, "sessionId": session, "method": "Fetch.fulfillRequest", "params": {
                    "requestId": event["params"]["requestId"], "responseCode": 200,
                    "responseHeaders": [{"name": "Content-Type", "value": "text/html"}], "body": ""}})
        cdp.on_event = on_event
        await cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "resourceType": "Document"}]}, session)
        for origin, items in storage.items():
            await cdp.send("Page.navigate", {"url": origin + "/"}, session)
            script = "(items => { for (const [k, v] of Object.entries(items)) localStorage.setItem(k, v); })"
            await cdp.send("Runtime.evaluate", {"expression": f"{script}({json.dumps(items)})"}, session)
    finally:
        if target:
            try:
                await cdp.send("Target.closeTarget", {"targetId": target})
            except Exception:
                pass
        await ws.close()
        await http.close()


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
        await export_identity(slot)
    except Exception:
        log.warning("[browser] 槽位 %s 导出登录身份失败", slot, exc_info=True)
    log.info("[browser] 槽位 %s 已归还（%s）", slot, client)


def _schedule_release(slot: int, client: str) -> None:
    lease = _leases.get(slot)
    if lease and lease["client"] == client and not lease["conns"]:
        lease["release_task"] = asyncio.create_task(_release_later(slot, client))


async def monitor() -> None:
    """空闲回收：没人租用超过 IDLE_STOP 的槽位停掉；接管超时收尾。"""
    if help_available():  # 桥接重启会丢掉接管状态：中转机上残留的临时 noVNC 先停掉，原服务恢复
        try:
            await _relay("stop")
        except Exception:
            log.warning("[browser] 启动时清理中转机失败", exc_info=True)
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

async def _pages(slot: int) -> int:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2), trust_env=False) as http:
            async with http.get(f"http://127.0.0.1:{cdp_port(slot)}/json/list") as resp:
                return sum(t.get("type") == "page" for t in await resp.json(content_type=None))
    except Exception:
        return 0


async def _tab_limit_refusal(slot: int, raw: str) -> str | None:
    """新开标签页前检查数量，超过上限直接回错误，不转给浏览器。"""
    if '"Target.createTarget"' not in raw:
        return None
    try:
        message = json.loads(raw)
    except ValueError:
        return None
    if message.get("method") != "Target.createTarget" or await _pages(slot) < MAX_TABS:
        return None
    reply = {"id": message.get("id"), "error": {"code": -32000, "message":
             f"共享浏览器每个槽位最多 {MAX_TABS} 个标签页，请先关闭不用的标签页（browser_tabs 的 close）再打开新页面"}}
    if message.get("sessionId"):
        reply["sessionId"] = message["sessionId"]
    return json.dumps(reply, ensure_ascii=False)


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
                        refusal = await _tab_limit_refusal(slot, msg.data)
                        if refusal:
                            await downstream.send_str(refusal)
                            continue
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

def _which(name: str, *extra: Path) -> str | None:
    import shutil
    found = shutil.which(name)
    if found:
        return found
    return next((str(p) for p in extra if p.exists()), None)


def _novnc_web() -> str | None:
    for folder in (os.getenv("NOVNC_WEB", ""), str(Path.home() / ".local/share/code-anywhere/noVNC"), "/usr/share/novnc"):
        if folder and (Path(folder) / "vnc.html").exists():
            return folder
    return None


def view_mode() -> str | None:
    """远程查看怎么连到手机：
    - cloudflare（默认，零配置）：本机临时起 noVNC + Cloudflare 免费临时隧道，拿到一个 https://….trycloudflare.com 地址
    - relay：配了 BROWSER_HELP_SSH_HOST，经中转机转出去（自定义隧道）
    - direct：配了 BROWSER_HELP_PUBLIC_URL，手机能直接访问本机，noVNC 在本机对外监听（自定义）
    """
    if RELAY_HOST and RELAY_LISTEN and RELAY_PUBLIC_URL and RELAY_WEBSOCKIFY:
        return "relay"
    if RELAY_PUBLIC_URL and RELAY_LISTEN and RELAY_WEBSOCKIFY:
        return "direct"
    if _cloudflared() and _websockify() and _novnc_web():
        return "cloudflare"
    return None


def _cloudflared() -> str | None:
    return _which("cloudflared", Path.home() / ".local/bin/cloudflared")


def _websockify() -> str | None:
    from src.config import PROJECT_ROOT
    return _which("websockify", PROJECT_ROOT / ".venv/bin/websockify")


def help_available() -> bool:
    return view_mode() is not None


def view_port(slot: int) -> int:
    return 6080 + slot


def _relay_script(action: str) -> str:
    """中转机上的启停脚本。原来占着对外端口的服务先记下命令再停，结束后按原命令恢复。"""
    if action == "start":
        host, port = RELAY_LISTEN.rsplit(":", 1)
        return f"""set -e
mkdir -p ~/.code-anywhere && cd ~/.code-anywhere
ok=; for i in $(seq 20); do nc -z 127.0.0.1 {RELAY_PORT} 2>/dev/null && ok=1 && break; sleep 0.5; done
[ -n "$ok" ] || {{ echo "ssh 反向隧道没有建立（127.0.0.1:{RELAY_PORT} 不通）：检查中转机 authorized_keys 是否允许端口转发" >&2; exit 3; }}
if [ -f novnc.pid ] && kill -0 "$(cat novnc.pid)" 2>/dev/null; then kill "$(cat novnc.pid)"; sleep 0.5; fi
old=$(lsof -nP -t -iTCP@{host}:{port} -sTCP:LISTEN 2>/dev/null | head -1 || true)
if [ -n "$old" ]; then
  [ -f saved.flag ] || ps -o command= -p "$old" > saved.flag
  kill "$old"; for i in $(seq 30); do kill -0 "$old" 2>/dev/null || break; sleep 0.1; done
fi
nohup {RELAY_WEBSOCKIFY} {host}:{port} 127.0.0.1:{RELAY_PORT} > novnc.log 2>&1 &
echo $! > novnc.pid
sleep 1; kill -0 "$(cat novnc.pid)" && echo started
"""
    restore = (f'nohup {RELAY_RESTORE} > ~/.code-anywhere/restored.log 2>&1 &\n  sleep 1; echo restored'
               if RELAY_RESTORE else 'echo "stopped-original"')
    return f"""cd ~/.code-anywhere 2>/dev/null || exit 0
if [ -f novnc.pid ]; then kill "$(cat novnc.pid)" 2>/dev/null; rm -f novnc.pid; fi
sleep 0.5
if [ -f saved.flag ]; then
  rm -f saved.flag
  {restore}
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


async def _cloudflare_url(proc, timeout: float = 40) -> str:
    """cloudflared 把临时地址打印在日志里（stderr）。"""
    import re
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = await asyncio.wait_for(proc.stderr.readline(), max(0.1, deadline - time.time()))
        if not line:
            break
        found = re.search(rb"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if found:
            async def drain():   # 继续读掉日志，避免管道写满卡住 cloudflared
                while await proc.stderr.readline():
                    pass
            asyncio.create_task(drain())
            return found.group(0).decode()
    raise RuntimeError("Cloudflare 临时隧道没有给出地址（检查这台机器能否访问 Internet）")


async def _open_view(item: dict, slot: int) -> str:
    """起 VNC 并把它送到手机能打开的地址，返回 noVNC 网页地址（不含参数）。进程记在 item["procs"]。"""
    mode = view_mode()
    if IS_MAC:   # 系统屏幕共享已经在 5900 上，不需要另起 VNC；一次性密码换成 Mac 登录密码
        target = MAC_SCREEN_SHARING_PORT
        vnc = None
    else:
        target = vnc_port(slot)
        vnc = await asyncio.create_subprocess_exec(
            "x11vnc", "-display", f":{display(slot)}", "-localhost", "-rfbport", str(target),
            "-passwd", item["password"], "-forever", "-shared", "-noxdamage", "-quiet",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        item["procs"].append(vnc)
    if mode == "relay":   # 中转机：ssh 反向隧道 + 中转机上临时起 noVNC
        link = await asyncio.create_subprocess_exec(
            "ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
            "-R", f"127.0.0.1:{RELAY_PORT}:127.0.0.1:{target}", RELAY_HOST,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    elif mode == "direct":
        link = await asyncio.create_subprocess_exec(
            *shlex.split(RELAY_WEBSOCKIFY), RELAY_LISTEN, f"127.0.0.1:{target}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    else:                 # cloudflare：noVNC 只听本机，由临时隧道转出去
        link = await asyncio.create_subprocess_exec(
            _websockify(), "--web", _novnc_web(), f"127.0.0.1:{view_port(slot)}", f"127.0.0.1:{target}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    item["procs"].append(link)
    await asyncio.sleep(2)
    if link.returncode is not None or (vnc and vnc.returncode is not None):
        raise RuntimeError("本机 VNC 或 noVNC / 隧道没有起来")
    if mode == "relay":
        await _relay("start")
        item["relay"] = True
        return RELAY_PUBLIC_URL
    if mode == "direct":
        return RELAY_PUBLIC_URL
    tunnel = await asyncio.create_subprocess_exec(
        _cloudflared(), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{view_port(slot)}",
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    item["procs"].append(tunnel)
    return await _cloudflare_url(tunnel)


async def _start_item(kind: str, slot: int, topic: int, reason: str, **extra) -> tuple[str, dict]:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from src.api_server import _bot
    from src import config
    help_id = secrets.token_hex(4)
    item = {"kind": kind, "slot": slot, "reason": reason[:300], "started": time.time(), "status": "waiting",
            "procs": [], "topic": topic, "password": secrets.token_urlsafe(6)[:8], **extra}
    _help[help_id] = item
    try:
        base = await _open_view(item, slot)
        url = f"{base.rstrip('/')}/vnc.html?autoconnect=1&resize=scale" + ("" if IS_MAC else f"&password={item['password']}")
        mac_note = "\n连接时输入这台 Mac 的登录密码（看到的是整个桌面，浏览器在桌面上）。" if IS_MAC else ""
        minutes = HELP_TIMEOUT // 60
        if kind == "help":
            gate(slot).clear()
            buttons = [InlineKeyboardButton("🖥 打开浏览器", url=url),
                       InlineKeyboardButton("✅ 我弄好了", callback_data=f"bh:done:{help_id}", style="success")]
            text = (f"🖥 需要你在共享浏览器里操作：{item['reason']}\n\n点“打开浏览器”进入，弄好后点“我弄好了”。"
                    f"链接 {minutes} 分钟内有效，期间会话暂停操作这个浏览器。{mac_note}")
        else:
            buttons = [InlineKeyboardButton("🖥 打开浏览器", url=url),
                       InlineKeyboardButton("关闭", callback_data=f"bh:close:{help_id}", style="danger")]
            text = (f"🖥 共享浏览器（槽位 {slot}）：{item['reason']}\n\n点“打开浏览器”查看和操作，用完点“关闭”。"
                    f"链接 {minutes} 分钟后自动失效。{mac_note}")
        sent = await _bot.send_message(chat_id=config.CHAT_ID, message_thread_id=topic, parse_mode=None,
                                       reply_markup=InlineKeyboardMarkup([buttons]), text=text)
        item["message_id"] = sent.message_id
    except Exception:
        log.warning("[browser] 打开远程查看失败", exc_info=True)
        await finish_help(help_id, "failed", notify=False)
        raise
    log.info("[browser] %s %s 槽位 %s：%s", kind, help_id, slot, item["reason"])
    return help_id, item


async def request_help(source_session_id: str, reason: str) -> dict:
    if not help_available():
        return {"status": "unavailable", "detail": "这台机器缺少远程查看组件（cloudflared / noVNC），告诉用户运行 ./setup --only browser"}
    client = "-".join(source_session_id.split(":")[-2:])
    slot = lease_of(client)
    if slot is None:
        return {"status": "not_leased", "detail": "你还没有在用共享浏览器：先用浏览器工具打开需要用户操作的页面，再请求帮忙"}
    existing = next((k for k, h in _help.items() if h["slot"] == slot and h["status"] == "waiting" and h["kind"] == "help"), None)
    if existing:
        return {"status": "waiting", "help_id": existing, "detail": "已经在等用户操作这个浏览器"}
    from src import projects
    from src.api_server import _bot
    lease = _leases[slot]
    topic = projects.get_topic(lease.get("cwd") or "")
    if not _bot or not topic:
        return {"status": "unavailable", "detail": "这个项目没有绑定 TG Thread，无法通知用户"}
    try:
        help_id, _ = await _start_item("help", slot, topic, reason, client=client, session_id=lease.get("session_id"))
    except Exception as exc:
        return {"status": "failed", "detail": str(exc)}
    return {"status": "waiting", "help_id": help_id,
            "detail": "已在 TG 请用户操作；用户点“我弄好了”或超时后，你会收到一条消息，也可用 browser_help_status 查询"}


async def open_for_user(topic: int, cwd: str) -> str:
    """TG /vnc：给用户打开共享浏览器的画面。这个项目的会话正在用的槽位优先；没有就给用户自己租一个。"""
    if not help_available():
        return "这台机器缺少远程查看组件（cloudflared / noVNC），请运行 ./setup --only browser"
    slot = next((s for s, lease in _leases.items() if lease.get("cwd") == cwd), None)
    client = None
    if slot is None:
        client = f"tg-{topic}"
        try:
            slot = await acquire(client)
        except RuntimeError as exc:
            return str(exc)
        _leases[slot].update(cwd=cwd, project=Path(cwd).name + "（你）")
    existing = next((h for h in _help.values() if h["slot"] == slot and h["status"] == "waiting"), None)
    if existing:
        return "这个浏览器的远程查看已经开着，用上面那条消息里的按钮"
    what = "这个项目的会话正在用的浏览器" if client is None else "空闲的共享浏览器，可以提前登录网站（所有项目共用登录状态）"
    try:
        await _start_item("view", slot, topic, what, user_client=client)
    except Exception as exc:
        if client:
            async with _lock:
                _leases.pop(slot, None)
        return f"⚠️ 打开失败：{exc}"
    return ""


async def finish_help(help_id: str, status: str, notify: bool = True) -> None:
    item = _help.get(help_id)
    if not item or item["status"] != "waiting":
        return
    item["status"] = status
    item["finished"] = time.time()
    if item.get("kind", "help") == "help":
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
        await export_identity(item["slot"])
    except Exception:
        log.warning("[browser] 导出登录身份失败", exc_info=True)
    from src.api_server import _bot
    from src import config
    if _bot and item.get("message_id"):
        label = {"done": "✅ 已完成", "closed": "已关闭", "timeout": "⌛ 已超时，远程查看已关闭",
                 "failed": "⚠️ 未能打开远程查看", "cancelled": "已取消，远程查看已关闭"}[status]
        try:
            await _bot.edit_message_text(chat_id=config.CHAT_ID, message_id=item["message_id"], parse_mode=None,
                                         text=f"🖥 共享浏览器：{item['reason']}\n\n{label}")
        except Exception:
            pass
    if item.get("user_client"):   # /vnc 给用户临时租的槽位，关了就还
        async with _lock:
            if _leases.get(item["slot"], {}).get("client") == item["user_client"]:
                _leases.pop(item["slot"], None)
        _last_used[item["slot"]] = time.time()
    lease = _leases.get(item["slot"])
    if lease and not lease["conns"]:
        _schedule_release(item["slot"], lease["client"])
    if notify and item.get("kind", "help") == "help" and item.get("session_id"):
        from src import agent_sessions
        text = {"done": f"用户已在共享浏览器里完成操作（{item['reason']}），请继续。",
                "timeout": f"用户没有在 {HELP_TIMEOUT // 60} 分钟内完成共享浏览器里的操作（{item['reason']}），远程查看已关闭；需要时可重新请求。"}.get(status)
        if text:
            try:
                await agent_sessions.send_message(item["session_id"], text, f"browser-help-{help_id}",
                                                  sender="code-anywhere", urgent=True)
            except Exception:
                log.warning("[browser] 通知会话失败", exc_info=True)
    log.info("[browser] %s 结束：%s", help_id, status)


def help_status(help_id: str) -> dict:
    item = _help.get(help_id)
    if not item:
        return {"status": "unknown", "detail": "没有这个接管编号"}
    return {"status": item["status"], "reason": item["reason"], "started": item["started"],
            "finished": item.get("finished")}


def status() -> dict:
    return {"slots": SLOTS, "help_available": help_available(),
            "mode": view_mode(),
            "leases": {slot: {k: v for k, v in lease.items() if k != "release_task"} for slot, lease in _leases.items()},
            "help": {k: help_status(k) for k in _help}}


async def handle_help(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        return web.json_response(await request_help(data["source_session_id"], str(data.get("reason") or "")))
    except (KeyError, ValueError, TypeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def handle_help_cancel(request: web.Request) -> web.Response:
    await finish_help(request.match_info["id"], "cancelled")
    return web.json_response(help_status(request.match_info["id"]))


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
    app.router.add_post("/browser/help/{id}/cancel", handle_help_cancel)
    app.router.add_get("/browser/status", handle_status)
