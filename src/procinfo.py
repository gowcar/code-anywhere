"""进程信息（启动标记 / 父进程 / 名字 / cwd / 参数）：Linux 读 /proc，macOS 用 ps 和 lsof。

local-sessions 用「pid + 启动标记」认定一个会话，防止 PID 被回收后串到别的进程。
macOS 没有 /proc，以前这里一律读不到，会话列表永远为空、发送方也认不出来。
"""
import os
import shutil
import subprocess
import time
from pathlib import Path

_LINUX = Path("/proc/self/stat").exists()
# launchd 和 CLI 给的 PATH 常不含 /usr/sbin（macOS 的 lsof 在那里）
LSOF = shutil.which("lsof") or "/usr/sbin/lsof"
_PS = shutil.which("ps") or "/bin/ps"


def _ps(pid, field: str) -> str | None:
    try:
        out = subprocess.run([_PS, "-o", f"{field}=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=5, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = out.stdout.strip()
    return value if out.returncode == 0 and value else None


def _linux_stat(pid):
    raw = Path(f"/proc/{pid}/stat").read_text()
    close = raw.rfind(")")
    return raw[raw.find("(") + 1:close], raw[close + 2:].split()


def start_token(pid) -> str | None:
    """进程启动标记：Linux 为启动 tick，macOS 为启动时刻（秒）。只用于比较，不含冒号。"""
    if _LINUX:
        try:
            return _linux_stat(pid)[1][19]
        except (OSError, IndexError):
            return None
    lstart = _ps(pid, "lstart")
    if not lstart:
        return None
    try:
        return str(int(time.mktime(time.strptime(" ".join(lstart.split()), "%a %b %d %H:%M:%S %Y"))))
    except ValueError:
        return None


def parent(pid) -> int | None:
    if _LINUX:
        try:
            return int(_linux_stat(pid)[1][1])
        except (OSError, IndexError, ValueError):
            return None
    ppid = _ps(pid, "ppid")
    return int(ppid) if ppid and ppid.isdigit() else None


def name(pid) -> str | None:
    if _LINUX:
        try:
            return _linux_stat(pid)[0]
        except OSError:
            return None
    comm = _ps(pid, "comm")
    return Path(comm).name if comm else None


def cwd(pid) -> str | None:
    if _LINUX:
        try:
            return str(Path(f"/proc/{pid}/cwd").resolve(strict=True))
        except OSError:
            return None
    try:
        out = subprocess.run([LSOF, "-a", "-p", str(pid), "-d", "cwd", "-Fn"], capture_output=True,
                             text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return next((line[1:] for line in out.stdout.splitlines() if line.startswith("n/")), None)


def args(pid) -> list[str]:
    """命令行参数。macOS 的 ps 按空白拆分，只适合找 --resume <uuid> 这类不含空格的参数。"""
    if _LINUX:
        try:
            return Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")
        except OSError:
            return []
    return (_ps(pid, "args") or "").split()
