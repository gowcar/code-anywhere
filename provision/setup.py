#!/usr/bin/env python3
"""code-anywhere 交互式安装：逐步“检查 → 缺什么补什么”，可反复执行。只用标准库。

用法：./setup            交互式安装 / 修复
      ./setup --check    只检查，不改任何东西
      ./setup --only telegram,service   只跑指定步骤
"""
import argparse
import contextlib
import getpass
import io
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "provision" / "templates"
HOME = Path.home()
BIN = HOME / ".local" / "bin"
ENV_FILE = REPO / ".env"
MCP_CMD = str(REPO / "scripts" / "session-mcp")
BEGIN, END = "<!-- code-anywhere:begin -->", "<!-- code-anywhere:end -->"
IS_MAC = sys.platform == "darwin"
CHECK_ONLY = False
PROBLEMS: list[str] = []


# ---------------------------------------------------------------- 输出与交互

def ok(msg): print(f"  ✓ {msg}")
def info(msg): print(f"    {msg}")


def warn(msg):
    print(f"  ✗ {msg}")
    PROBLEMS.append(msg)


def ask(question: str, default: bool = True) -> bool:
    if CHECK_ONLY:
        return False
    hint = "[Y/n]" if default else "[y/N]"
    answer = input(f"  ? {question} {hint} ").strip().lower()
    return default if not answer else answer in {"y", "yes", "是"}


def prompt(question: str, default: str = "", secret: bool = False) -> str:
    label = f"  ? {question}" + (f" [{default}]" if default and not secret else "") + "："
    value = getpass.getpass(label) if secret else input(label)
    return value.strip() or default


def run(cmd, check=False, capture=True, input_text=None, shell=False):
    return subprocess.run(cmd, check=check, text=True, input=input_text, shell=shell,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.STDOUT if capture else None)


def which(name: str) -> str | None:
    return shutil.which(name) or (str(BIN / name) if (BIN / name).exists() else None)


# ---------------------------------------------------------------- 纯函数（有单测）

def merge_block(text: str, body: str) -> str:
    """把 code-anywhere 管理的规则放进标记块；不动用户自己的其他内容。"""
    block = f"{BEGIN}\n{body.strip()}\n{END}"
    if BEGIN in text and END in text:
        head, rest = text.split(BEGIN, 1)
        return head + block + rest.split(END, 1)[1]
    if body.strip() and body.strip() in text:  # 早期手写进去、还没有标记的同一份规则
        return text.replace(body.strip(), block, 1)
    return (text.rstrip() + "\n\n" if text.strip() else "") + block + "\n"


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines() if path.exists() else []:
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def update_env(text: str, updates: dict[str, str]) -> str:
    """原地改值（含被注释掉的同名项），其余行、注释和顺序保持不变。"""
    lines, done = text.splitlines(), set()
    for i, line in enumerate(lines):
        key = line.lstrip("# ").split("=", 1)[0].strip()
        if "=" in line and key in updates and key not in done:
            lines[i] = f"{key}={updates[key]}"
            done.add(key)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in done]
    return "\n".join(lines) + "\n"


def render(name: str, **values: str) -> str:
    text = (TEMPLATES / name).read_text()
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def find_group_message(updates: list[dict]) -> dict | None:
    """在 getUpdates 结果里找最近一条来自群组的人类消息。"""
    for update in reversed(updates):
        message = update.get("message") or {}
        chat, user = message.get("chat") or {}, message.get("from") or {}
        if chat.get("type") in {"group", "supergroup"} and not user.get("is_bot"):
            return {"chat_id": chat["id"], "title": chat.get("title", ""),
                    "is_forum": bool(chat.get("is_forum")), "user_id": user.get("id"),
                    "user": user.get("username") or user.get("first_name", "")}
    return None


def service_path() -> str:
    """常驻服务的 PATH：命令的真实所在目录；不要 fnm 每个 shell 的临时目录，也不要项目 .venv。"""
    dirs = [str(BIN)]
    for name in ("claude", "codex", "node", "glab", "tmux", "uv", "git"):
        found = shutil.which(name)
        if found:
            real = Path(found).resolve().parent
            if "fnm_multishells" not in str(real) and REPO not in real.parents:
                dirs.append(str(real))
    dirs += (["/opt/homebrew/bin"] if IS_MAC else []) + ["/usr/local/bin", "/usr/bin", "/bin"]
    return ":".join(dict.fromkeys(dirs))


# ---------------------------------------------------------------- Telegram

class TgError(Exception):
    def __init__(self, code: int, description: str):
        super().__init__(description)
        self.code = code


def tg(token: str, method: str, http_timeout: int = 20, **params) -> dict:
    data = urllib.parse.urlencode({k: json.dumps(v) if isinstance(v, (dict, list)) else v
                                   for k, v in params.items()}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    try:
        with urllib.request.urlopen(req, timeout=http_timeout) as resp:
            return json.load(resp)["result"]
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read() or b"{}")
        raise TgError(exc.code, body.get("description", str(exc))) from None


def check_group(token: str, chat_id: int, bot_id: int) -> list[str]:
    """返回群组配置上缺的东西；空列表 = 就绪。"""
    missing = []
    chat = tg(token, "getChat", chat_id=chat_id)
    if not chat.get("is_forum"):
        missing.append("群组没有开启「话题 / Topics」")
    member = tg(token, "getChatMember", chat_id=chat_id, user_id=bot_id)
    if member.get("status") != "administrator":
        missing.append("bot 不是群管理员")
    elif not member.get("can_manage_topics"):
        missing.append("bot 管理员权限里没有勾选「管理话题」")
    return missing


# ---------------------------------------------------------------- 步骤

def step_deps():
    """基础依赖：git / curl / tmux / uv / node / claude / codex / glab"""
    system = [n for n in ("git", "curl", "tmux") if not shutil.which(n)]
    if system:
        cmd = (f"brew install {' '.join(system)}" if IS_MAC
               else f"sudo apt-get install -y {' '.join(system)}")
        warn(f"缺少 {', '.join(system)}，安装命令：{cmd}")
        if ask(f"现在执行 `{cmd}`？"):
            run(cmd, shell=True, capture=False)
    else:
        ok("git / curl / tmux")
    installers = {
        "uv": "curl -LsSf https://astral.sh/uv/install.sh | sh",
        "claude": "curl -fsSL https://claude.ai/install.sh | bash",
        "codex": "npm install -g @openai/codex",
    }
    for name, cmd in installers.items():
        if which(name):
            ok(name)
            continue
        if name == "codex" and not shutil.which("npm"):
            warn("codex 需要 node/npm（建议用 fnm 或 nvm 装 Node 22+），装好后重跑 ./setup")
            continue
        warn(f"缺少 {name}，安装命令：{cmd}")
        if ask(f"现在安装 {name}？"):
            run(cmd, shell=True, capture=False)
    if which("glab"):
        ok("glab")
    else:
        warn("缺少 glab（GitLab issue 流程需要）")
        if ask("现在下载 glab 到 ~/.local/bin？"):
            install_glab()


def install_glab():
    arch = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}[platform.machine().lower()]
    suffix = f"{'darwin' if IS_MAC else 'linux'}_{arch}.tar.gz"
    url = "https://gitlab.com/api/v4/projects/gitlab-org%2Fcli/releases/permalink/latest"
    with urllib.request.urlopen(url, timeout=30) as resp:
        release = json.load(resp)
    asset = next(l["url"] for l in release["assets"]["links"] if l["name"].endswith(suffix))
    with urllib.request.urlopen(asset, timeout=180) as resp:
        archive = tarfile.open(fileobj=io.BytesIO(resp.read()))
    member = next(m for m in archive.getmembers() if m.name.endswith("bin/glab"))
    BIN.mkdir(parents=True, exist_ok=True)
    (BIN / "glab").write_bytes(archive.extractfile(member).read())
    (BIN / "glab").chmod(0o755)
    ok(f"glab {release['tag_name']} → {BIN / 'glab'}")


def step_python():
    """Python 虚拟环境与依赖"""
    probe = REPO / ".venv" / "bin" / "python"
    if probe.exists() and run([str(probe), "-c", "import telegram, mcp, aiohttp, mistune, dotenv"]).returncode == 0:
        ok(".venv 依赖齐全")
        return
    warn(".venv 不存在或依赖不全")
    uv = which("uv")
    if not uv:
        info("需要先装 uv（见第一步）")
        return
    if ask("现在创建 .venv 并安装依赖？"):
        if not probe.exists():
            run([uv, "venv", str(REPO / ".venv")], capture=False)
        run([uv, "pip", "install", "--python", str(probe), "-r", str(REPO / "requirements-mcp.txt")],
            capture=False)


def step_tools():
    """个人工具：t / tmux-tui / cc → ~/.local/bin，tmux.conf → ~/.tmux.conf"""
    targets = {name: BIN / name for name in ("t", "tmux-tui", "cc")}
    targets["tmux.conf"] = HOME / ".tmux.conf"
    for name, target in targets.items():
        source = REPO / "tools" / name
        if target.exists() and target.read_bytes() == source.read_bytes():
            ok(f"{name} 已是最新")
            continue
        if target.exists():
            warn(f"{target} 与仓库版本不同")
            if not ask(f"用仓库版本覆盖 {target}（旧文件备份为 .bak-时间戳）？", default=False):
                continue
            target.rename(target.with_name(f"{target.name}.bak-{int(time.time())}"))
        else:
            warn(f"未安装 {name}")
            if not ask(f"安装 {name} → {target}？"):
                continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        ok(f"{name} → {target}")
    if str(BIN) not in os.environ.get("PATH", "").split(":"):
        warn(f"{BIN} 不在 PATH 里，请在 shell 配置中加入它（fish: fish_add_path {BIN}）")


def step_cli_auth():
    """Claude / Codex 能否调用模型"""
    if which("claude"):
        status = run([which("claude"), "auth", "status"]).stdout or ""
        try:
            logged_in = json.loads(status[status.index("{"):]).get("loggedIn")
        except ValueError:
            logged_in = False
        if logged_in or read_env(ENV_FILE).get("ANTHROPIC_API_KEY"):
            ok("Claude 已登录")
        else:
            warn("Claude 未登录")
            info("任选其一：在终端运行 `claude auth login`；或在下面填 API Key（存进 .env，权限 600）")
            if not CHECK_ONLY:
                key = prompt("ANTHROPIC_API_KEY（留空跳过）", secret=True)
                if key:
                    write_env({"ANTHROPIC_API_KEY": key})
                    ok("已写入 .env")
    if which("codex"):
        config = HOME / ".codex" / "config.toml"
        logged = "Logged in" in (run([which("codex"), "login", "status"]).stdout or "")
        if logged or (config.exists() and "model_provider" in config.read_text()):
            ok("Codex 已登录或已配置网关")
        else:
            warn("Codex 未登录：在终端运行 `codex login`（只用 Claude 可忽略）")


def write_env(updates: dict[str, str]):
    if not ENV_FILE.exists():
        shutil.copy2(REPO / ".env.example", ENV_FILE)
    ENV_FILE.write_text(update_env(ENV_FILE.read_text(), updates))
    ENV_FILE.chmod(0o600)


def step_telegram():
    """Telegram bot 与话题群组"""
    env = read_env(ENV_FILE)
    token, chat_id = env.get("BOT_TOKEN", ""), env.get("CHAT_ID", "")
    if token and token != "your_bot_token_here":
        try:
            me = tg(token, "getMe")
            if chat_id:
                missing = check_group(token, int(chat_id), me["id"])
                if not missing:
                    ok(f"@{me['username']} 已就绪，群组 {chat_id}")
                    return
                warn("；".join(missing))
            else:
                warn("还没有绑定群组（CHAT_ID 为空）")
        except TgError as exc:
            warn(f"BOT_TOKEN 无效或群组不可用：{exc}")
            token = ""
    else:
        warn("还没有配置 Telegram bot")
    if CHECK_ONLY:
        return
    if not token:
        info("在 Telegram 找 @BotFather → /newbot 创建一个 bot，复制它给你的 token。")
        info("⚠️ 一个 bot 同一时间只能被一台机器接收；新机器请新建 bot，或先停掉旧机器的服务。")
        while True:
            token = prompt("粘贴 BOT_TOKEN", secret=True)
            try:
                me = tg(token, "getMe")
                break
            except TgError as exc:
                print(f"    token 不可用：{exc}，请重试")
    else:
        me = tg(token, "getMe")
    ok(f"bot 有效：@{me['username']}")
    info("接下来：")
    info(f"  1) 新建一个 Telegram 群组，把 @{me['username']} 拉进去")
    info("  2) 群设置里开启「话题 / Topics」")
    info("  3) 把 bot 设为管理员，并勾选「管理话题」")
    info("  4) 在群里随便发一句话（比如 hello）")
    try:
        tg(token, "deleteWebhook")
    except TgError:
        pass
    found, offset, deadline = None, None, time.time() + 900
    print("    等待群消息（最多 15 分钟，Ctrl+C 中止）…")
    while time.time() < deadline:
        try:
            updates = tg(token, "getUpdates", http_timeout=40, offset=offset or 0, timeout=30)
        except TgError as exc:
            if exc.code == 409:
                warn("这个 bot 正被别的程序接收消息（多半是另一台机器上的 code-anywhere），请先停掉它或换一个 bot")
                return
            raise
        if updates:
            offset = updates[-1]["update_id"] + 1
            found = find_group_message(updates) or found
        if found and not found["is_forum"]:
            info(f"收到「{found['title']}」的消息，但这个群还没开启话题。开启后请再发一句。")
            found = None
        if found:
            break
    if not found:
        warn("超时：没收到群消息")
        return
    tg(token, "getUpdates", offset=offset)  # 确认已读，服务启动后不再重复处理
    ok(f"群组：{found['title']}（{found['chat_id']}），你的 TG 账号：{found['user']}（{found['user_id']}）")
    while missing := check_group(token, found["chat_id"], me["id"]):
        warn("；".join(missing))
        input("    改好后按回车重新检查…")
    allowed = {u for u in env.get("ALLOWED_USERS", "").split(",") if u.strip() not in {"", "0"}}
    allowed.add(str(found["user_id"]))
    write_env({"BOT_TOKEN": token, "CHAT_ID": str(found["chat_id"]),
               "ALLOWED_USERS": ",".join(sorted(allowed)), "BOT_PORT": env.get("BOT_PORT", "5000")})
    ok(f".env 已写入（只允许 {', '.join(sorted(allowed))} 使用）")


def step_hooks():
    """Claude hooks（会话启动、停止、通知、权限审批 → TG）"""
    settings = HOME / ".claude" / "settings.json"
    text = settings.read_text() if settings.exists() else ""
    if str(REPO / "hooks") in text and all(h in text for h in ("session_start.py", "stop.py", "permission.py")):
        ok("hooks 已安装")
        return
    warn("Claude hooks 未安装")
    if ask("安装到 ~/.claude/settings.json？"):
        settings.parent.mkdir(parents=True, exist_ok=True)
        sys.path.insert(0, str(REPO / "hooks"))
        import install as hook_install
        hook_install.install()


def step_mcp():
    """local-sessions MCP（Claude 与 Codex 两边）"""
    if which("claude"):
        if run([which("claude"), "mcp", "get", "local-sessions"]).returncode == 0:
            ok("Claude 已注册 local-sessions")
        else:
            warn("Claude 未注册 local-sessions")
            if ask("注册到 Claude 用户级配置？"):
                run([which("claude"), "mcp", "add", "--scope", "user", "local-sessions", MCP_CMD], capture=False)
    config = HOME / ".codex" / "config.toml"
    if which("codex"):
        if config.exists() and "[mcp_servers.local-sessions]" in config.read_text():
            ok("Codex 已注册 local-sessions")
        else:
            warn("Codex 未注册 local-sessions")
            if ask("注册到 Codex？"):
                run([which("codex"), "mcp", "add", "local-sessions", "--", MCP_CMD], capture=False)


def step_rules():
    """全局规则：跨 session 沟通格式、GitLab issue 处理（标记块管理）"""
    body = (TEMPLATES / "global-rules.md").read_text()
    targets = [HOME / ".claude" / "CLAUDE.md"]
    if which("codex") or (HOME / ".codex").exists():
        targets.append(HOME / ".codex" / "AGENTS.md")
    for path in targets:
        current = path.read_text() if path.exists() else ""
        merged = merge_block(current, body)
        if merged == current:
            ok(f"{path} 已是最新")
            continue
        warn(f"{path} 需要更新" + ("（当前是断开的软链接）" if path.is_symlink() and not path.exists() else ""))
        if ask(f"写入 {path}（只改 code-anywhere 标记块）？"):
            if path.is_symlink() and not path.exists():
                path.unlink()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(merged)
            ok(f"{path} 已更新")


def step_gitlab():
    """GitLab issue 流程（可选）：glab 登录 + issue-desk 会话目录"""
    glab = which("glab")
    if not glab:
        info("跳过：没有 glab")
        return
    status = run([glab, "auth", "status"]).stdout or ""
    hosts = [l.split("Logged in to ", 1)[1].split()[0] for l in status.splitlines() if "Logged in to " in l]
    logged = bool(hosts)
    if logged:
        ok("glab 已登录：" + ", ".join(hosts))
    else:
        warn("glab 未登录")
        if ask("配置 GitLab issue 流程？", default=False):
            host = prompt("GitLab 域名", "git.garena.com")
            info(f"在 https://{host}/-/user_settings/personal_access_tokens 创建令牌，只勾 api")
            token = prompt("粘贴令牌", secret=True)
            token_file = HOME / ".config" / "gitlab-token"
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(token + "\n")
            token_file.chmod(0o600)
            result = run([glab, "auth", "login", "--hostname", host, "--stdin"], input_text=token)
            if result.returncode:
                warn("glab 登录失败：" + (result.stdout or "").strip()[-200:])
                return
            run([glab, "config", "set", "git_protocol", "ssh", "--host", host])
            ok(f"glab 已登录 {host}，令牌存于 {token_file}")
            logged = True
    if not logged:
        return
    desk = HOME / "workspace" / "issue-desk"
    if (desk / "CLAUDE.md").exists():
        ok(f"issue-desk 已存在：{desk}")
        return
    warn("还没有 issue-desk 会话目录")
    if ask(f"创建 {desk}（别名路由表在其 CLAUDE.md 里，可随时改）？"):
        (desk / ".claude").mkdir(parents=True, exist_ok=True)
        (desk / "CLAUDE.md").write_text(render("issue-desk.CLAUDE.md", HOME=str(HOME)))
        (desk / ".claude" / "settings.local.json").write_text(
            json.dumps({"model": "claude-sonnet-5", "effortLevel": "medium",  # 只判断仓库 + 整理 issue，Sonnet 足够
                        "permissions": {"defaultMode": "bypassPermissions"}}, indent=2) + "\n")
        ok(f"已创建 {desk}")
        info("在该目录运行一次 `claude` 并说句话，桥接 5 分钟内会为它建 TG 话题")


def step_service():
    """常驻服务（Linux systemd 用户服务 / macOS launchd）"""
    (REPO / "logs").mkdir(exist_ok=True)
    values = {"REPO": str(REPO), "PATH": service_path()}
    if IS_MAC:
        target = HOME / "Library" / "LaunchAgents" / "com.code-anywhere.plist"
        content = render("code-anywhere.plist", **values)
    else:
        target = HOME / ".config" / "systemd" / "user" / "code-anywhere.service"
        content = render("code-anywhere.service", **values)
    current = target.read_text() if target.exists() else ""
    if current == content:
        ok(f"服务配置已是最新：{target}")
    else:
        warn(f"服务配置{'需要更新' if current else '不存在'}：{target}")
        if not ask("写入服务配置并（重新）启动服务？"):
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        if IS_MAC:
            uid = os.getuid()
            run(["launchctl", "bootout", f"gui/{uid}", str(target)])
            run(["launchctl", "bootstrap", f"gui/{uid}", str(target)], capture=False)
        else:
            run(["systemctl", "--user", "daemon-reload"])
            run(["systemctl", "--user", "enable", "code-anywhere"])
            run(["systemctl", "--user", "restart", "code-anywhere"], capture=False)
        ok("服务已启动")
    if not IS_MAC:
        user = os.environ.get("USER") or getpass.getuser()
        if "Linger=yes" in (run(["loginctl", "show-user", user, "-p", "Linger"]).stdout or ""):
            ok("已开启 linger（不登录也常驻、开机自启）")
        else:
            warn("未开启 linger：退出登录后服务会停")
            if ask("执行 `loginctl enable-linger`？"):
                if run(["loginctl", "enable-linger", user]).returncode:
                    run(["sudo", "loginctl", "enable-linger", user], capture=False)
        if run(["systemctl", "--user", "is-active", "code-anywhere"]).stdout.strip() == "active":
            ok("服务运行中（勿再用 ./code-anywhere.sh start，会起第二个实例抢消息）")
        else:
            warn("服务没有在运行：systemctl --user status code-anywhere 查看原因")


def step_verify():
    """最终自检：服务健康 + 往群里发一条上线消息"""
    env = read_env(ENV_FILE)
    port = env.get("BOT_PORT", "5000")
    for _ in range(30):
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                    f"http://127.0.0.1:{port}/health", timeout=3) as resp:
                health = json.load(resp)
            break
        except (OSError, ValueError):
            time.sleep(1)
    else:
        warn(f"服务健康检查失败（127.0.0.1:{port}/health），看 logs/code-anywhere.log")
        return
    ok(f"服务健康：{health.get('status')}，已接管 {health.get('terminal_sessions', 0)} 个会话")
    if env.get("BOT_TOKEN") and env.get("CHAT_ID") and ask("往群里发一条上线消息验证链路？"):
        tg(env["BOT_TOKEN"], "sendMessage", chat_id=env["CHAT_ID"],
           text=f"✅ code-anywhere 已在 {socket.gethostname()} 就绪。在 ~/workspace 下任一目录启动 claude/codex 说句话，5 分钟内会出现对应话题。")
        ok("已发送，去群里看看")


STEPS = {"deps": step_deps, "python": step_python, "tools": step_tools, "auth": step_cli_auth,
         "telegram": step_telegram, "hooks": step_hooks, "mcp": step_mcp, "rules": step_rules,
         "gitlab": step_gitlab, "service": step_service, "verify": step_verify}


def main():
    global CHECK_ONLY
    parser = argparse.ArgumentParser(description="code-anywhere 安装 / 自检")
    parser.add_argument("--check", action="store_true", help="只检查，不做任何修改")
    parser.add_argument("--only", help="只跑这些步骤（逗号分隔）：" + ",".join(STEPS))
    args = parser.parse_args()
    CHECK_ONLY = args.check
    names = args.only.split(",") if args.only else list(STEPS)
    print(f"code-anywhere {'自检' if CHECK_ONLY else '安装'}：{REPO}\n")
    for i, name in enumerate(names, 1):
        step = STEPS[name]
        print(f"[{i}/{len(names)}] {step.__doc__.splitlines()[0]}")
        try:
            step()
        except KeyboardInterrupt:
            print("\n  已中止本步骤")
        except Exception as exc:  # 单步失败不影响后续步骤
            warn(f"{name} 出错：{exc}")
        print()
    if PROBLEMS and not CHECK_ONLY:
        # 本轮修过的项会留在 PROBLEMS 里；静默复查一遍，汇总以复查结果为准
        CHECK_ONLY = True
        PROBLEMS.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            for name in names:
                try:
                    STEPS[name]()
                except Exception as exc:
                    warn(f"{name} 出错：{exc}")
    if PROBLEMS:
        print(f"仍有 {len(PROBLEMS)} 项待处理：")
        for problem in PROBLEMS:
            print(f"  - {problem}")
        print("\n处理后重新运行 ./setup（可反复执行）")
        sys.exit(1)
    print("全部就绪 ✓")


if __name__ == "__main__":
    main()
