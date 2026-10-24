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
import shlex
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
CONFIG_FILE = Path(os.getenv("CODE_ANYWHERE_CONFIG", str(Path.home() / ".config" / "code-anywhere" / "config.env")))
LEGACY_ENV = REPO / ".env"   # 旧位置：只读，合并进 CONFIG_FILE
MCP_CMD = str(REPO / "scripts" / "session-mcp")
BROWSER_MCP_CMD = str(REPO / "scripts" / "browser-mcp")
PLAYWRIGHT_MCP = HOME / ".local" / "share" / "code-anywhere" / "playwright-mcp"
PLAYWRIGHT_MCP_VERSION = "0.0.83"
BEGIN, END = "<!-- code-anywhere:begin -->", "<!-- code-anywhere:end -->"
IS_MAC = sys.platform == "darwin"
CHECK_ONLY = False
INTERACTIVE = False   # ./setup --ask 才逐项询问
PROBLEMS: list[str] = []


# ---------------------------------------------------------------- 输出与交互

def ok(msg): print(f"  ✓ {msg}")
def info(msg): print(f"    {msg}")


def warn(msg):
    print(f"  ✗ {msg}")
    PROBLEMS.append(msg)


def ask(question: str, default: bool = True) -> bool:
    """默认不提问（用户没法判断要不要装某个依赖）：安全的直接做，会改动用户已有文件的直接跳过。
    只有 ./setup --ask 时才逐项询问。必须由用户提供的东西（bot token、登录）用 prompt，不走这里。"""
    if CHECK_ONLY:
        return False
    if not INTERACTIVE:
        label = question.rstrip("？?")
        print(f"  → {label}" if default else f"  → 跳过（不改动你已有的东西）：{label}")
        return default
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


def read_config() -> dict[str, str]:
    """配置文件为准；旧的仓库 .env 只补充配置文件里没有（或为空）的项。"""
    merged = {k: v for k, v in read_env(LEGACY_ENV).items() if v}
    merged.update({k: v for k, v in read_env(CONFIG_FILE).items() if v})
    return merged


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
    """在 getUpdates 结果里找最近一条人类消息：私聊（默认的私聊 Thread 模式）或话题群组。"""
    for update in reversed(updates):
        message = update.get("message") or {}
        chat, user = message.get("chat") or {}, message.get("from") or {}
        if user.get("is_bot") or chat.get("type") not in {"private", "group", "supergroup"} or "id" not in chat:
            continue
        private = chat["type"] == "private"
        return {"chat_id": chat["id"], "title": "私聊" if private else chat.get("title", ""),
                "is_forum": private or bool(chat.get("is_forum")), "private": private,
                "user_id": user.get("id"), "user": user.get("username") or user.get("first_name", "")}
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


AVATAR = REPO / "assets" / "avatar.jpg"


def tg_upload(token: str, method: str, files: dict[str, Path], **params) -> dict:
    """带文件的 Bot API 调用（multipart/form-data）。"""
    boundary = "----code-anywhere-" + os.urandom(8).hex()
    body = io.BytesIO()
    for key, value in params.items():
        value = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
    for key, path in files.items():
        body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"; filename=\"{path.name}\"\r\n"
                   f"Content-Type: image/jpeg\r\n\r\n".encode())
        body.write(path.read_bytes())
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=body.getvalue(),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)["result"]
    except urllib.error.HTTPError as exc:
        body_json = json.loads(exc.read() or b"{}")
        raise TgError(exc.code, body_json.get("description", str(exc))) from None


def ensure_avatar(token: str, me: dict) -> None:
    """新 bot 没有头像时换上 Code Anywhere 的头像（assets/avatar.jpg）；已有头像不动。"""
    try:
        if tg(token, "getUserProfilePhotos", user_id=me["id"], limit=1).get("total_count"):
            ok("bot 已有头像")
            return
    except TgError:
        return
    warn("bot 还没有头像")
    if AVATAR.exists() and ask("换上 Code Anywhere 的头像？"):
        try:
            tg_upload(token, "setMyProfilePhoto", {"avatar": AVATAR},
                      photo={"type": "static", "photo": "attach://avatar"})
            ok("头像已设置（客户端可能要过一会儿才刷新）")
        except TgError as exc:
            info(f"自动设置失败（{exc}）：可以在 @BotFather 发 /setuserpic，上传 {AVATAR}")


THREADED_MODE_HINT = "在 @BotFather 发 /mybots → 选你的 bot → Bot Settings → Threaded Mode → 打开"


def check_group(token: str, chat_id: int, bot_id: int) -> list[str]:
    """返回入口配置上缺的东西；空列表 = 就绪。chat_id > 0 是私聊 Thread 模式（默认），否则是话题群组。"""
    missing = []
    if chat_id > 0:
        if not tg(token, "getMe").get("has_topics_enabled"):
            missing.append(f"bot 还没开启 Threaded Mode（{THREADED_MODE_HINT}）")
        return missing
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

PACKAGE_MANAGERS = [  # (命令, 安装前缀)；按顺序找第一个可用的
    ("apt-get", "sudo apt-get update && sudo apt-get install -y"),
    ("dnf", "sudo dnf install -y"), ("yum", "sudo yum install -y"),
    ("pacman", "sudo pacman -S --noconfirm"), ("zypper", "sudo zypper install -y"), ("apk", "sudo apk add"),
]
BREW_INSTALL = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'


def package_install_command(packages: list[str], is_mac: bool | None = None, available=shutil.which) -> str | None:
    """当前系统安装这些包的命令；找不到包管理器返回 None。"""
    if (IS_MAC if is_mac is None else is_mac):
        return f"brew install {' '.join(packages)}" if available("brew") else None
    for manager, prefix in PACKAGE_MANAGERS:
        if available(manager):
            return f"{prefix} {' '.join(packages)}"
    return None


def step_deps():
    """基础依赖：git / curl / tmux / uv / node / claude / codex / glab（缺什么装什么）"""
    system = [n for n in ("git", "curl", "tmux") if not shutil.which(n)]
    if not system:
        ok("git / curl / tmux")
    else:
        warn(f"缺少 {', '.join(system)}")
        if IS_MAC and not shutil.which("brew") and ask("macOS 需要 Homebrew 来装它们，现在安装 Homebrew？"):
            run(BREW_INSTALL, shell=True, capture=False)
            os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ["PATH"]
        cmd = package_install_command(system)
        if not cmd:
            info("没有找到可用的包管理器，请手动安装后重跑 ./setup")
        elif ask(f"现在执行 `{cmd}`？（可能要求输入 sudo 密码）"):
            run(cmd, shell=True, capture=False)
    installers = {
        "uv": "curl -LsSf https://astral.sh/uv/install.sh | sh",
        "claude": "curl -fsSL https://claude.ai/install.sh | bash",
    }
    for name, cmd in installers.items():
        if which(name):
            ok(name)
        else:
            warn(f"缺少 {name}")
            if ask(f"现在安装 {name}（{cmd}）？"):
                run(cmd, shell=True, capture=False)
    if which("node") and which("npm"):
        ok("node / npm")
    else:
        warn("缺少 Node.js（Codex 需要）")
        if ask("从 nodejs.org 下载 Node 22 到 ~/.local/share/node（不需要 sudo）？"):
            install_node()
    if which("codex"):
        ok("codex")
    elif which("npm"):
        warn("缺少 codex")
        if ask("现在安装 codex（npm install -g --prefix ~/.local @openai/codex）？"):
            run([which("npm"), "install", "-g", "--prefix", str(HOME / ".local"), "@openai/codex"], capture=False)
    else:
        warn("缺少 codex：需要先装 Node.js（只用 Claude 可忽略）")
    if which("glab"):
        ok("glab")
    else:
        info("没有 glab（只在 issue 流程用 GitLab 时需要，配置时会自动装）")
        if ask("现在下载 glab 到 ~/.local/bin？"):
            install_glab()


def node_dist_name(index: list[dict], system: str, machine: str, major: str = "v22") -> tuple[str, str]:
    """从 nodejs.org 的 index.json 选出该大版本最新 LTS 的 (版本, 压缩包名)。"""
    os_name = {"darwin": "darwin", "linux": "linux"}[system]
    arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}[machine.lower()]
    release = next(r for r in index if r["version"].startswith(major + "."))
    return release["version"], f"node-{release['version']}-{os_name}-{arch}.tar.xz"


def install_node():
    with urllib.request.urlopen("https://nodejs.org/dist/index.json", timeout=30) as resp:
        index = json.load(resp)
    version, archive_name = node_dist_name(index, "darwin" if IS_MAC else "linux", platform.machine())
    print(f"    下载 {archive_name} …")
    with urllib.request.urlopen(f"https://nodejs.org/dist/{version}/{archive_name}", timeout=600) as resp:
        data = resp.read()
    root = HOME / ".local" / "share" / "node"
    root.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as archive:
        try:
            archive.extractall(root, filter="data")
        except TypeError:  # 3.12 之前的部分小版本没有 filter 参数
            archive.extractall(root)
    node_bin = root / archive_name.removesuffix(".tar.xz") / "bin"
    BIN.mkdir(parents=True, exist_ok=True)
    for name in ("node", "npm", "npx"):
        link = BIN / name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(node_bin / name)
    ok(f"Node {version} → {node_bin}（已链接到 ~/.local/bin）")


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
    if probe.exists() and run([str(probe), "-c", "import telegram, aiohttp, mistune, dotenv, PIL"]).returncode == 0:
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


OWN_TOOLS = ("ca-config", "issue-platform", "issue-upload", "issue-images", "local-issue", "gitlab-upload", "gitlab-issue-images")
CONVENIENCE_TOOLS = ("t", "tmux-tui")   # 电脑上挑选 tmux 会话的小工具；对方已有同名命令就保留对方的


def step_tools():
    """命令行工具：issue 相关工具 → ~/.local/bin；会话选择器 t（对方没有同名命令时才装）"""
    # t 会调用 tmux-tui，两个配套：只要有一个是对方自己的同名命令，两个都不装
    theirs = [n for n in CONVENIENCE_TOOLS
              if (BIN / n).exists() and (BIN / n).read_bytes() != (REPO / "tools" / n).read_bytes()]
    if theirs:
        ok(f"已有你自己的 {'、'.join(theirs)}，会话选择器 t 不安装，保留你的版本")
    for name in OWN_TOOLS + (() if theirs else CONVENIENCE_TOOLS):
        source, target = REPO / "tools" / name, BIN / name
        if target.exists() and target.read_bytes() == source.read_bytes():
            ok(f"{name} 已是最新")
            continue
        if target.exists():
            warn(f"{name} 不是最新")
            if not ask(f"更新 {name}（旧文件备份为 .bak-时间戳）？"):
                continue
            target.rename(target.with_name(f"{target.name}.bak-{int(time.time())}"))
        else:
            warn(f"未安装 {name}")
            if not ask(f"安装 {name} → {target}？"):
                continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        ok(f"{name} → {target}")
    pointer = HOME / ".config" / "code-anywhere" / "repo"   # local-issue 等工具靠它找到仓库
    if not CHECK_ONLY and (not pointer.exists() or pointer.read_text().strip() != str(REPO)):
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(str(REPO) + "\n")
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
        if logged_in or read_config().get("ANTHROPIC_API_KEY"):
            ok("Claude 已登录")
        else:
            warn("Claude 未登录")
            info(f"任选其一：在终端运行 `claude auth login`；或在下面填 API Key（存进 {CONFIG_FILE}）")
            info("用自建网关 / 第三方服务的，留空跳过，之后在配置文件里填 ANTHROPIC_BASE_URL 和令牌")
            if not CHECK_ONLY:
                key = prompt("ANTHROPIC_API_KEY（留空跳过）", secret=True)
                if key:
                    write_env({"ANTHROPIC_API_KEY": key})
                    ok("已写入配置文件")
    if which("codex"):
        config = HOME / ".codex" / "config.toml"
        logged = "Logged in" in (run([which("codex"), "login", "status"]).stdout or "")
        if logged or (config.exists() and "model_provider" in config.read_text()) or read_config().get("OPENAI_API_KEY"):
            ok("Codex 已登录或已配置网关")
        else:
            warn("Codex 未登录（只用 Claude 可忽略）")
            info(f"任选其一：在终端运行 `codex login`；或在下面填 OpenAI API Key（存进 {CONFIG_FILE}）")
            if not CHECK_ONLY:
                key = prompt("OPENAI_API_KEY（留空跳过）", secret=True)
                if key:
                    write_env({"OPENAI_API_KEY": key})
                    ok("已写入配置文件")


def ensure_config() -> None:
    if not CONFIG_FILE.exists():
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(TEMPLATES / "config.env", CONFIG_FILE)
    CONFIG_FILE.chmod(0o600)


def write_env(updates: dict[str, str]):
    """写进 ~/.config/code-anywhere/config.env（原地改值，注释和顺序保持不变）。"""
    ensure_config()
    CONFIG_FILE.write_text(update_env(CONFIG_FILE.read_text(), updates))
    CONFIG_FILE.chmod(0o600)


def step_config():
    """配置文件 ~/.config/code-anywhere/config.env（所有密钥和设置都在这里，权限 600）"""
    existed = CONFIG_FILE.exists()
    legacy = {k: v for k, v in read_env(LEGACY_ENV).items() if v and v != "your_bot_token_here"}
    current = read_env(CONFIG_FILE)
    to_move = {k: v for k, v in legacy.items() if not current.get(k)}
    if existed and not to_move:
        ok(f"配置文件：{CONFIG_FILE}")
        if CONFIG_FILE.stat().st_mode & 0o077 and not CHECK_ONLY:
            CONFIG_FILE.chmod(0o600)
        return
    if CHECK_ONLY:
        warn(f"配置文件{'需要合并旧的 .env' if existed else '还没有'}：{CONFIG_FILE}")
        return
    ensure_config()
    if to_move:
        write_env(to_move)
        ok(f"已把仓库里旧的 .env 合并进配置文件（{len(to_move)} 项；旧文件保留未删）")
    ok(f"配置文件：{CONFIG_FILE}（没填的项以后直接编辑它）")


def step_telegram():
    """Telegram bot 与入口（默认私聊 Thread 模式：每个项目是和 bot 私聊里的一个 Thread）"""
    env = read_config()
    token, chat_id = env.get("BOT_TOKEN", ""), env.get("CHAT_ID", "")
    if token and token != "your_bot_token_here":
        try:
            me = tg(token, "getMe")
            if chat_id:
                missing = check_group(token, int(chat_id), me["id"])
                if not missing:
                    ensure_avatar(token, me)
                    ok(f"@{me['username']} 已就绪，" + ("私聊 Thread 模式" if int(chat_id) > 0 else f"群组 {chat_id}"))
                    return
                warn("；".join(missing))
            else:
                warn("还没有绑定入口（CHAT_ID 为空）")
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
    ensure_avatar(token, me)
    info("接下来（私聊 Thread 模式，推荐）：")
    info(f"  1) {THREADED_MODE_HINT}")
    info(f"  2) 打开和 @{me['username']} 的私聊，随便发一句话（比如 hello）")
    info("  （也可以用话题群组：建群、开启话题、把 bot 设为管理员并勾选「管理话题」，再在群里发一句话）")
    try:
        tg(token, "deleteWebhook")
    except TgError:
        pass
    found, offset, deadline = None, None, time.time() + 900
    print("    等待你的消息（最多 15 分钟，Ctrl+C 中止）…")
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
        warn("超时：没收到你的消息")
        return
    tg(token, "getUpdates", offset=offset)  # 确认已读，服务启动后不再重复处理
    ok(f"入口：{found['title']}（{found['chat_id']}），你的 TG 账号：{found['user']}（{found['user_id']}）")
    while missing := check_group(token, found["chat_id"], me["id"]):
        warn("；".join(missing))
        input("    改好后按回车重新检查…")
    allowed = {u for u in env.get("ALLOWED_USERS", "").split(",") if u.strip() not in {"", "0"}}
    allowed.add(str(found["user_id"]))
    write_env({"BOT_TOKEN": token, "CHAT_ID": str(found["chat_id"]),
               "ALLOWED_USERS": ",".join(sorted(allowed)), "BOT_PORT": env.get("BOT_PORT", "5000")})
    ok(f"配置文件已写入（只允许 {', '.join(sorted(allowed))} 使用）")


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
    """local-sessions 与共享浏览器 browser 两个 MCP（Claude 与 Codex 两边）"""
    for name, cmd in (("local-sessions", MCP_CMD), ("browser", BROWSER_MCP_CMD)):
        if which("claude"):
            if run([which("claude"), "mcp", "get", name]).returncode == 0:
                ok(f"Claude 已注册 {name}")
            else:
                warn(f"Claude 未注册 {name}")
                if ask(f"注册 {name} 到 Claude 用户级配置？"):
                    run([which("claude"), "mcp", "add", "--scope", "user", name, cmd], capture=False)
        config = HOME / ".codex" / "config.toml"
        if which("codex"):
            if config.exists() and f"[mcp_servers.{name}]" in config.read_text():
                ok(f"Codex 已注册 {name}")
            else:
                warn(f"Codex 未注册 {name}")
                if ask(f"注册 {name} 到 Codex？"):
                    run([which("codex"), "mcp", "add", name, "--", cmd], capture=False)


NOVNC_DIR = HOME / ".local" / "share" / "code-anywhere" / "noVNC"
NOVNC_TARBALL = "https://github.com/novnc/noVNC/archive/refs/tags/v1.6.0.tar.gz"


def step_remote_view():
    """远程查看共享浏览器（/vnc、请用户接管）：默认用 Cloudflare 免费临时隧道，不需要任何配置。"""
    cloudflared = shutil.which("cloudflared") or (str(BIN / "cloudflared") if (BIN / "cloudflared").exists() else None)
    if cloudflared:
        ok("cloudflared（Cloudflare 临时隧道）")
    else:
        warn("缺少 cloudflared")
        arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(os.uname().machine, "amd64")
        if IS_MAC and shutil.which("brew"):
            if ask("用 Homebrew 安装 cloudflared？"):
                run([shutil.which("brew"), "install", "cloudflared"], capture=False)
        elif IS_MAC:
            url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-{arch}.tgz"
            if ask(f"下载 cloudflared 到 {BIN}？"):
                BIN.mkdir(parents=True, exist_ok=True)
                run(f"curl -fsSL {url} | tar xz -C {shlex.quote(str(BIN))}", shell=True, capture=False)
        else:
            url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"
            if ask(f"下载 cloudflared 到 {BIN}（不需要 sudo）？"):
                BIN.mkdir(parents=True, exist_ok=True)
                run(["curl", "-fsSL", "-o", str(BIN / "cloudflared"), url], capture=False)
                (BIN / "cloudflared").chmod(0o755)
    venv_ws = REPO / ".venv" / "bin" / "websockify"
    if shutil.which("websockify") or venv_ws.exists():
        ok("websockify")
    else:
        warn("缺少 websockify")
        # 只装 websockify 本身（加 numpy 提速）：它的 jwcrypto/redis/requests 只给令牌插件用，
        # jwcrypto 会拖进要编译的 cryptography（Intel Mac 上没有现成安装包）
        if ask("装到项目虚拟环境（websockify，不带用不到的插件依赖）？"):
            py = str(REPO / ".venv" / "bin" / "python")
            run([which("uv") or "uv", "pip", "install", "--python", py, "--no-deps", "websockify"], capture=False)
            run([which("uv") or "uv", "pip", "install", "--python", py, "numpy"], capture=False)
    if IS_MAC:  # Retina 屏远程查看时临时降分辨率用
        if shutil.which("displayplacer") or (BIN / "displayplacer").exists():
            ok("displayplacer（远程查看时临时降分辨率）")
        else:
            warn("缺少 displayplacer（远程查看时画面会很大）")
            arch = "apple" if os.uname().machine == "arm64" else "intel"
            if ask(f"下载 displayplacer 到 {BIN}？"):
                BIN.mkdir(parents=True, exist_ok=True)
                run(["curl", "-fsSL", "-o", str(BIN / "displayplacer"),
                     f"https://github.com/jakehilborn/displayplacer/releases/download/v1.4.0/displayplacer-{arch}-v140"],
                    capture=False)
                (BIN / "displayplacer").chmod(0o755)
    if (NOVNC_DIR / "vnc.html").exists() or Path("/usr/share/novnc/vnc.html").exists():
        ok("noVNC 网页")
    else:
        warn("缺少 noVNC 网页")
        if ask(f"下载 noVNC 到 {NOVNC_DIR}？"):
            NOVNC_DIR.mkdir(parents=True, exist_ok=True)
            run(f"curl -fsSL {NOVNC_TARBALL} | tar xz --strip-components=1 -C {shlex.quote(str(NOVNC_DIR))}",
                shell=True, capture=False)


def step_browser():
    """共享浏览器：Playwright MCP、Chromium、虚拟屏幕（Xvfb）、远程查看（x11vnc）、技能说明"""
    cli = PLAYWRIGHT_MCP / "node_modules" / "@playwright" / "mcp" / "cli.js"
    if cli.exists():
        ok(f"Playwright MCP（{PLAYWRIGHT_MCP}）")
    elif not which("npm"):
        warn("缺少 Node.js，无法安装 Playwright MCP")
    else:
        warn("缺少 Playwright MCP")
        if ask(f"安装 @playwright/mcp@{PLAYWRIGHT_MCP_VERSION} 到 {PLAYWRIGHT_MCP}？"):
            PLAYWRIGHT_MCP.mkdir(parents=True, exist_ok=True)
            run([which("npm"), "install", "--prefix", str(PLAYWRIGHT_MCP), "--no-audit", "--no-fund",
                 f"@playwright/mcp@{PLAYWRIGHT_MCP_VERSION}"], capture=False)
    if list((HOME / ".cache" / "ms-playwright").glob("chromium-*/chrome-linux*/chrome")) or \
            list((HOME / "Library" / "Caches" / "ms-playwright").glob("chromium-*/chrome-mac*")):
        ok("Chromium")
    elif (PLAYWRIGHT_MCP / "node_modules" / "playwright" / "cli.js").exists():
        warn("缺少 Chromium")
        if ask("下载 Playwright 的 Chromium？"):
            run([which("node"), str(PLAYWRIGHT_MCP / "node_modules" / "playwright" / "cli.js"), "install", "chromium"],
                capture=False)
    if IS_MAC:
        # macOS：浏览器开在真实桌面上，远程查看转发系统“屏幕共享”，需要它开着
        if run(["nc", "-z", "-w", "2", "127.0.0.1", "5900"]).returncode == 0:
            ok("屏幕共享已开启（远程查看用）")
        else:
            warn("屏幕共享没开：/vnc 和请你接管需要它")
            info("系统设置 → 通用 → 共享 → 打开“屏幕共享”（只允许你自己的账号）")
    else:
        missing = [n for n, binary in (("xvfb", "Xvfb"), ("x11vnc", "x11vnc")) if not shutil.which(binary)]
        if not missing:
            ok("Xvfb / x11vnc")
        else:
            warn(f"缺少 {', '.join(missing)}")
            cmd = package_install_command(missing)
            if cmd and ask(f"现在执行 `{cmd}`？（可能要求输入 sudo 密码）"):
                run(cmd, shell=True, capture=False)
    step_remote_view()
    skill = (TEMPLATES / "skills" / "shared-browser" / "SKILL.md").read_text()
    for root in (HOME / ".claude" / "skills", HOME / ".codex" / "skills"):
        if not root.parent.exists():
            continue
        target = root / "shared-browser" / "SKILL.md"
        if target.exists() and target.read_text() == skill:
            ok(f"技能 shared-browser（{target.parent}）")
        else:
            warn(f"技能 shared-browser 未安装或不是最新（{target.parent}）")
            if ask(f"安装/更新技能 shared-browser 到 {target.parent}？"):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(skill)
                ok(f"已写入 {target}")


def step_rules():
    """全局规则：跨 session 沟通格式、issue 处理（标记块管理）"""
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


def step_issues():
    """issue 流程（可选）：只检查。配置交给 code-anywhere 会话——在它的 Thread 里说“配置 issue 流程”"""
    platform_file = HOME / ".config" / "code-anywhere" / "issue-platform"
    if platform_file.exists():
        platform = platform_file.read_text().strip()
        cli = "gh" if platform == "github" else "glab"
        if which(cli) and run([which(cli), "auth", "status"]).returncode == 0:
            ok(f"issue 流程已配置：{platform}，{cli} 已登录")
        else:
            warn(f"issue 流程配置为 {platform}，但 {cli} 没装或没登录")
            info("在 code-anywhere 的 Thread 里说“配置 issue 流程”，它会帮你装好并登录")
    elif (HOME / ".config" / "gitlab-token").exists():
        ok("issue 流程：GitLab（旧配置）")
    else:
        info("issue 流程还没配置（可选）：装好后在 code-anywhere 的 Thread 里说“配置 issue 流程”")


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
    """最终自检：服务健康 + 往 Telegram 发一条上线消息"""
    env = read_config()
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
    if env.get("BOT_TOKEN") and env.get("CHAT_ID") and ask("往 Telegram 发一条上线消息验证链路？"):
        tg(env["BOT_TOKEN"], "sendMessage", chat_id=env["CHAT_ID"],
           text=f"✅ code-anywhere 已在 {socket.gethostname()} 就绪。\n\n"
                f"私聊里会自动出现 code-anywhere（总体协调）和 issue-desk（反馈问题）两个 Thread。想加什么能力（比如配置 issue 流程），直接在 code-anywhere 里说。\n"
                f"新项目：/new 项目名（默认 Claude，加 codex 用 Codex）。")
        ok("已发送，去 Telegram 看看")


STEPS = {"deps": step_deps, "python": step_python, "config": step_config, "tools": step_tools, "auth": step_cli_auth,
         "telegram": step_telegram, "hooks": step_hooks, "mcp": step_mcp, "browser": step_browser, "rules": step_rules,
         "issues": step_issues, "service": step_service, "verify": step_verify}


def uninstall() -> None:
    """卸载 code-anywhere 装到这台机器上的东西（装了一半也能清理）。只删自己装的；用户数据目录保留并告知。"""
    print(f"卸载 code-anywhere：{REPO}\n")
    uid = str(os.getuid())
    if IS_MAC:
        plist = HOME / "Library" / "LaunchAgents" / "com.code-anywhere.plist"
        if plist.exists() and str(REPO) in plist.read_text():   # 只停这份代码装的服务
            run(["launchctl", "bootout", f"gui/{uid}", str(plist)])
            plist.unlink()
            ok("已停止并删除常驻服务（launchd）")
        data = HOME / ".local" / "share" / "code-anywhere" / "browser"
        for pid_file in data.glob("slot-*.pid") if data.exists() else []:
            with contextlib.suppress(OSError, ValueError):
                os.kill(int(pid_file.read_text()), 15)
            pid_file.unlink(missing_ok=True)
    else:
        # 只停这份代码装的服务：看服务文件确实在本用户目录下且指向本仓库。不能只按服务名判断——
        # 用临时 HOME 测试卸载时曾把机器上真正在跑的桥接停掉（2026-10-24）
        unit = HOME / ".config" / "systemd" / "user" / "code-anywhere.service"
        if unit.exists() and str(REPO) in unit.read_text():
            run(["systemctl", "--user", "disable", "--now", "code-anywhere"])
            unit.unlink(missing_ok=True)
            run(["systemctl", "--user", "daemon-reload"])
            for slot in range(1, 5):
                run(["systemctl", "--user", "stop", f"ca-browser-{slot}"])
            ok("已停止并删除常驻服务（systemd）")
    settings = HOME / ".claude" / "settings.json"
    if settings.exists() and str(REPO / "hooks") in settings.read_text():
        sys.path.insert(0, str(REPO / "hooks"))
        import install as hook_install
        with contextlib.redirect_stdout(io.StringIO()):
            hook_install.uninstall()
        ok("已从 ~/.claude/settings.json 移除 code-anywhere 的 hooks（其他设置不动）")
    for name in ("local-sessions", "browser"):
        if which("claude") and run([which("claude"), "mcp", "get", name]).returncode == 0:
            run([which("claude"), "mcp", "remove", "--scope", "user", name])
            ok(f"已从 Claude 注销 MCP：{name}")
        config = HOME / ".codex" / "config.toml"
        if which("codex") and config.exists() and f"[mcp_servers.{name}]" in config.read_text():
            run([which("codex"), "mcp", "remove", name])
            ok(f"已从 Codex 注销 MCP：{name}")
    for target in (HOME / ".claude" / "CLAUDE.md", HOME / ".codex" / "AGENTS.md"):
        if target.exists() and BEGIN in target.read_text():
            text = target.read_text()
            start, end = text.index(BEGIN), text.index(END) + len(END)
            target.write_text((text[:start].rstrip("\n") + "\n" + text[end:].lstrip("\n")).strip("\n") + "\n")
            ok(f"已从 {target} 删除 code-anywhere 的规则块（其他内容不动）")
    for name in OWN_TOOLS + CONVENIENCE_TOOLS:   # 只删和仓库版本一样的（即我们装的）
        target = BIN / name
        if target.exists() and target.read_bytes() == (REPO / "tools" / name).read_bytes():
            target.unlink()
            ok(f"已删除 {target}")
    for root in (HOME / ".claude" / "skills", HOME / ".codex" / "skills"):
        if (root / "shared-browser").exists():
            shutil.rmtree(root / "shared-browser")
            ok(f"已删除技能 {root / 'shared-browser'}")
    (HOME / ".config" / "code-anywhere" / "repo").unlink(missing_ok=True)
    data = HOME / ".local" / "share" / "code-anywhere"
    print()
    if data.exists():
        info(f"保留了数据目录 {data}（本地 issue、共享浏览器的登录状态、Playwright MCP）；不要了就删除它")
    info(f"最后删除代码目录即可：rm -rf {REPO}")
    print("\n卸载完成 ✓")


class _Tee(io.TextIOBase):
    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for stream in self.streams:
            stream.write(text)
            stream.flush()
        return len(text)

    def flush(self):
        for stream in self.streams:
            stream.flush()


def ensure_common_paths() -> None:
    """ssh 远程执行、launchd 等非登录环境的 PATH 里常常没有 Homebrew 和 ~/.local/bin，
    会把已经装好的 tmux、node 误判为缺失，生成的服务 PATH 也会缺目录（2026-10-24 Alan 的 Mac）。"""
    extra = [HOME / ".local" / "bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin"), HOME / ".local" / "share" / "node" / "bin"]
    current = os.environ.get("PATH", "").split(":")
    missing = [str(p) for p in extra if p.is_dir() and str(p) not in current]
    if missing:
        os.environ["PATH"] = ":".join(missing + current)


def main():
    global CHECK_ONLY
    ensure_common_paths()
    parser = argparse.ArgumentParser(description="code-anywhere 安装 / 自检")
    parser.add_argument("--check", action="store_true", help="只检查，不做任何修改")
    parser.add_argument("--ask", action="store_true", help="逐项询问（默认不问：安全的直接做，会改动你已有文件的跳过）")
    parser.add_argument("--yes", "-y", action="store_true", help=argparse.SUPPRESS)   # 兼容旧用法，现在本来就不问
    parser.add_argument("--only", help="只跑这些步骤（逗号分隔）：" + ",".join(STEPS))
    parser.add_argument("--uninstall", action="store_true", help="卸载 code-anywhere 装到这台机器上的东西（装了一半也能清理）")
    args = parser.parse_args()
    if args.uninstall:
        uninstall()
        return
    CHECK_ONLY = args.check
    global INTERACTIVE
    INTERACTIVE = args.ask
    os.environ["PATH"] = f"{BIN}:{os.environ.get('PATH', '')}"  # 刚装的命令后续步骤立刻可用
    names = args.only.split(",") if args.only else list(STEPS)
    if not CHECK_ONLY:   # 完整输出另存一份，出问题时不用截图，直接看 logs/setup.log
        (REPO / "logs").mkdir(exist_ok=True)
        sys.stdout = _Tee(sys.stdout, open(REPO / "logs" / "setup.log", "a", encoding="utf-8"))
        print(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} ./setup {' '.join(sys.argv[1:])}")
    print(f"code-anywhere {'自检' if CHECK_ONLY else '安装'}：{REPO}\n")
    for i, name in enumerate(names, 1):
        step = STEPS[name]
        print(f"[{i}/{len(names)}] {step.__doc__.splitlines()[0]}")
        try:
            step()
        except KeyboardInterrupt:   # Ctrl+C 就是要停下：整体退出，不再继续后面的步骤
            print("\n\n已中止。重新运行会从头检查，已经装好的会直接跳过。")
            sys.exit(130)
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
