"""共享浏览器 MCP：启动官方 Playwright MCP，经 code-anywhere 桥接中转连到浏览器池。

调用方会话按真实祖先 CLI 进程（pid-启动时间）识别，桥接据此给每个会话租独立的槽位；
第一次调用浏览器工具时才租，空闲 10 分钟 Playwright MCP 主动断开，槽位归还，下次调用自动重连。
"""
import os
import sys
from pathlib import Path

from src.session_mcp import caller_processes

PLAYWRIGHT_MCP = Path(os.getenv("PLAYWRIGHT_MCP_DIR", str(Path.home() / ".local/share/code-anywhere/playwright-mcp")))
PORT = int(os.getenv("BOT_PORT", "5000"))


def main() -> None:
    cli = PLAYWRIGHT_MCP / "node_modules/@playwright/mcp/cli.js"
    if not cli.exists():
        sys.exit(f"Playwright MCP 未安装：npm install --prefix {PLAYWRIGHT_MCP} @playwright/mcp（./setup 会自动安装）")
    callers = caller_processes()
    client = "-".join(callers[0]) if callers else f"anon-{os.getppid()}"
    output = Path.home() / ".local/share/code-anywhere/browser/output" / client
    output.mkdir(parents=True, exist_ok=True)
    for folder in (output.parent.parent, output.parent):  # 里面有共享登录身份，只给本账号
        folder.chmod(0o700)
    node = os.getenv("NODE", "node")
    os.execvp(node, [node, str(cli), "--cdp-endpoint", f"http://127.0.0.1:{PORT}/browser/cdp/{client}",
                     "--caps", "vision,pdf,devtools", "--idle-timeout", "600000",
                     "--output-dir", str(output), *sys.argv[1:]])


if __name__ == "__main__":
    main()
