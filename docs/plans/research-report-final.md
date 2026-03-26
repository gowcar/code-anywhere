# Code Anywhere — 完整技术研究报告

> 基于已确认的特性范围，为实现阶段提供技术方案指导。
>
> 日期：2026-03-24

---

## 目录

1. [整体架构设计](#1-整体架构设计)
2. [技术栈选型](#2-技术栈选型)
3. [核心数据流](#3-核心数据流)
4. [P0 特性技术方案](#4-p0-特性技术方案)
5. [P1 特性技术方案](#5-p1-特性技术方案)
6. [图片输入验证结论](#6-图片输入验证结论)
7. [关键技术难点与解决方案](#7-关键技术难点与解决方案)
8. [参考资料](#8-参考资料)

---

## 1. 整体架构设计

### 1.1 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                    Telegram (用户手机/桌面)                    │
│                                                              │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│   │ Topic A  │  │ Topic B  │  │ Topic C  │  │  ...     │   │
│   │ 项目-前端 │  │ 项目-后端 │  │ 恢复会话  │  │          │   │
│   └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────────┘   │
│        │              │              │                        │
└────────┼──────────────┼──────────────┼───────────────────────┘
         │              │              │
         │   Telegram Bot API (Polling)│
         ▼              ▼              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Code Anywhere Bot                          │
│                                                              │
│   ┌──────────────────────────────────────────────────────┐  │
│   │                 核心模块                               │  │
│   │  ├─ TG 消息处理器 (命令 + 文本 + 图片 + 回调)         │  │
│   │  ├─ 会话管理器 (创建/列出/切换/恢复/删除)              │  │
│   │  ├─ Claude 进程管理器 (启动/流式读取/中断)             │  │
│   │  ├─ 权限仲裁器 (Hook 接收 + TG 按钮 + 超时)          │  │
│   │  ├─ 输出渲染器 (Markdown → TG HTML + 智能分段)        │  │
│   │  └─ HTTP API 服务器 (Hook 回调接收)                   │  │
│   └──────────────────────────────────────────────────────┘  │
│              │                            ▲                  │
│              │ 启动 claude -p             │ HTTP POST        │
│              ▼                            │                  │
│   ┌──────────────────┐     ┌──────────────────────────┐     │
│   │  Claude Code CLI  │     │   Claude Code Hooks      │     │
│   │  (子进程)          │     │   ├─ PreToolUse          │     │
│   │                    │     │   ├─ SessionStart        │     │
│   │  stream-json 输出  │     │   ├─ Notification        │     │
│   │  stdin 管道输入    │     │   └─ Stop                │     │
│   └──────────────────┘     └──────────────────────────┘     │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 设计原则

1. **CLI `-p` 为主干** — 通过 `claude -p` 启动非交互会话，`--output-format stream-json` 获取结构化输出
2. **Hook 做事件感知** — 4 个 Hook 脚本覆盖会话生命周期 (Start/Stop) 和工具执行 (PreToolUse/Notification)
3. **Forum Topic = 会话** — Telegram 群组的每个话题对应一个 Claude Code 会话，物理隔离
4. **异步全栈** — Python asyncio 驱动，非阻塞 I/O
5. **零外部依赖部署** — 无需数据库/Redis/云服务，仅 Python + Claude Code CLI

### 1.3 模块划分（建议）

```
code-anywhere/
├── src/
│   ├── bot.py              # Telegram Bot 入口 + 命令路由
│   ├── session.py          # 会话管理器（状态、持久化、恢复）
│   ├── claude_runner.py    # Claude 进程管理（启动、流式读取、中断）
│   ├── permission.py       # 权限仲裁器（接收、展示、决策）
│   ├── renderer.py         # Markdown → TG HTML 渲染 + 智能分段
│   ├── api_server.py       # HTTP API（Hook 回调接收）
│   └── config.py           # 配置管理
├── hooks/
│   ├── common.py           # 共享工具函数
│   ├── permission.py       # PreToolUse Hook
│   ├── session_start.py    # SessionStart Hook
│   ├── notification.py     # Notification Hook
│   └── stop.py             # Stop Hook
├── .env.example            # 配置模板
└── ...
```

---

## 2. 技术栈选型

| 层 | 选型 | 理由 |
|---|------|------|
| 语言 | **Python 3.11+** | asyncio 成熟、telegram-bot 库生态好、Claude Pilot 验证过 |
| Telegram | **python-telegram-bot ^22.x** | 官方推荐、异步原生、功能完整 |
| HTTP Server | **aiohttp** | 轻量异步、与 asyncio 无缝集成 |
| Markdown | **mistune ^3.x** (GFM) | 性能好、插件化、可自定义 renderer |
| Claude 集成 | **CLI `-p`** + **Hooks** | 简单可靠、无 SDK 版本依赖 |
| 配置 | **python-dotenv** | .env 文件管理 |
| 进程管理 | **asyncio.subprocess** | 原生异步子进程 |

---

## 3. 核心数据流

### 3.1 TG 会话：用户发消息 → Claude 回复

```
用户在 TG 话题发消息
    │
    ▼
Bot 接收 (handle_message)
    │
    ├─ 检查白名单
    ├─ 获取/创建会话 (session_manager)
    ├─ 获取 topic_lock (防并发)
    │
    ▼
启动 claude -p 子进程
    │
    ├─ 命令: claude -p "{text}" --output-format stream-json --verbose
    ├─ 若有 session_id: 追加 --resume {session_id}
    ├─ 设置 env: TELEGRAM_CHAT_ID, TELEGRAM_THREAD_ID
    ├─ cwd: 项目目录
    │
    ▼
逐行读 stdout (stream-json)
    │
    ├─ {"type":"system"} → 提取 session_id
    ├─ {"type":"assistant", "message":{"content":[...]}}
    │   ├─ type:"text" → Markdown 渲染 → TG 发送
    │   └─ type:"tool_use" → 工具简述 → TG 发送
    ├─ {"type":"result"} → 检查 is_error
    │
    ▼
进程结束 → 清理状态 → 释放 lock
```

### 3.2 权限审批流

```
Claude 执行工具前
    │
    ▼
PreToolUse Hook 触发 (hooks/permission.py)
    │
    ├─ 从 stdin 读取 JSON (session_id, tool_name, tool_input, cwd)
    ├─ 构建工具描述文本
    ├─ HTTP POST → Bot /permission 端点
    │
    ▼
Bot 接收权限请求 (api_server)
    │
    ├─ 创建 pending_permissions[request_id] = {event: asyncio.Event}
    ├─ 发送 TG 消息 + InlineKeyboard [✅ 允许] [❌ 拒绝]
    │
    ▼
用户点击按钮 (handle_callback)
    │
    ├─ 解析 callback_data: "perm_allow:{request_id}" / "perm_deny:{request_id}"
    ├─ 设置 pending_permissions[request_id]["decision"]
    ├─ event.set() 唤醒等待
    │
    ▼
Hook 脚本收到 HTTP 响应
    │
    ├─ decision == "allow" → stdout 输出允许 JSON
    ├─ decision == "deny"  → stdout 输出拒绝 JSON
    │
    ▼
Claude 继续/拒绝执行工具
```

### 3.3 终端会话监控流 (P1)

```
用户在电脑终端运行 claude (交互模式)
    │
    ▼
SessionStart Hook 触发
    │
    ├─ 从 stdin 读取: session_id, transcript_path, cwd, source, model
    ├─ 从环境变量读取: $TMUX_PANE (可选)
    ├─ HTTP POST → Bot /session_start
    │
    ▼
Bot 创建 Forum Topic + 启动 JSONL Watcher
    │
    ├─ 每 0.5s 轮询 transcript JSONL 文件
    ├─ 增量读取 → JSON 解析 → UUID 去重
    ├─ assistant 消息 → Markdown → TG 话题
    ├─ tool_use → 工具简述 → TG 话题
    │
    ▼
Stop Hook 触发 → Bot 停止 Watcher → TG 通知
```

---

## 4. P0 特性技术方案

### 4.1 TG Bot 基础通信

**目标**：建立 Bot 与用户的基本通信能力。

**技术方案**：
- 使用 `python-telegram-bot` 的 `Application` 类，Polling 模式
- 注册 `CommandHandler` (命令) + `MessageHandler` (文本消息) + `CallbackQueryHandler` (按钮回调)
- 群组模式：Bot 作为 Forum Topic 群组的管理员

**关键代码模式**：
```python
app = Application.builder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", cmd_start))
app.add_handler(CommandHandler("projects", cmd_projects))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.add_handler(CallbackQueryHandler(handle_callback))
app.run_polling(allowed_updates=Update.ALL_TYPES)
```

### 4.2 CLI `-p` 模式集成

**目标**：通过子进程调用 Claude Code CLI，获取结构化输出。

**技术方案**：
```python
cmd = [
    "claude", "-p", user_text,
    "--output-format", "stream-json",
    "--verbose"
]
# 会话恢复
if session_id:
    cmd.extend(["--resume", session_id])
# 工具自动批准（可选，配合 bypass 模式）
if bypass_mode:
    cmd.append("--dangerously-skip-permissions")

proc = await asyncio.create_subprocess_exec(
    *cmd,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    cwd=project_dir,
    env={**os.environ, "TELEGRAM_CHAT_ID": str(chat_id), "TELEGRAM_THREAD_ID": str(topic_id)}
)
```

**stream-json 输出格式**：
```json
{"type":"system","session_id":"uuid","model":"claude-sonnet-4-20250514"}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"..."}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_xxx","name":"Bash","input":{"command":"npm test"}}]}}
{"type":"result","is_error":false,"result":"...","session_id":"uuid"}
```

### 4.3 流式输出转发

**目标**：实时将 Claude 输出转发到 Telegram 话题。

**技术方案**：
- 逐行读取 stdout，`json.loads` 解析
- 文本消息：缓冲累积，达到一定长度或遇到工具调用时 flush 发送
- 工具调用：立即发送简述（工具名 + 关键参数摘要）
- 使用 `parse_mode="HTML"` 发送格式化消息

**缓冲策略**：
```python
buffer = ""
async for line in proc.stdout:
    event = json.loads(line)
    if event["type"] == "assistant":
        for block in event["message"]["content"]:
            if block["type"] == "text":
                buffer += block["text"]
                # 遇到段落结尾或缓冲超过 2000 字符时发送
                if "\n\n" in buffer or len(buffer) > 2000:
                    await send_rendered(chat_id, topic_id, buffer)
                    buffer = ""
            elif block["type"] == "tool_use":
                # 先 flush 文本缓冲
                if buffer:
                    await send_rendered(chat_id, topic_id, buffer)
                    buffer = ""
                # 发送工具简述
                await send_tool_summary(chat_id, topic_id, block)
```

**关于 `--verbose`**：保留 `--verbose` 标志（Claude Pilot 实践验证可行）。`--verbose` 可获取更详细的中间事件（含 partial messages），有助于实时展示进度。实现时需过滤 partial messages 以控制消息量——只在完整消息到达时发送，partial messages 可选择性忽略或仅用于更新"正在输入..."状态。

### 4.4 权限远程审批

**目标**：Claude 执行工具前，通过 Telegram 按钮让用户远程审批。

**技术方案**：

**Hook 脚本 (hooks/permission.py)**：
```python
#!/usr/bin/env python3
import json, sys, os, urllib.request

data = json.loads(sys.stdin.read())
tool_name = data.get("tool_name", "Unknown")
tool_input = data.get("tool_input", {})

# 构建工具描述
description = format_tool_description(tool_name, tool_input)

# 发送到 Bot HTTP API
payload = json.dumps({
    "session_id": data.get("session_id"),
    "tool_name": tool_name,
    "description": description,
    "chat_id": os.environ.get("TELEGRAM_CHAT_ID"),
    "thread_id": os.environ.get("TELEGRAM_THREAD_ID"),
}).encode()

req = urllib.request.Request(
    f"http://localhost:{BOT_PORT}/permission",
    data=payload,
    headers={"Content-Type": "application/json"}
)
resp = urllib.request.urlopen(req, timeout=130)
result = json.loads(resp.read())

# 返回决策给 Claude
if result["decision"] == "allow":
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow"
    }}))
else:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": result.get("reason", "User denied")
    }}))
```

**Bot 端 (权限端点)**：
```python
async def handle_permission(request):
    data = await request.json()
    request_id = str(uuid.uuid4())
    event = asyncio.Event()
    pending_permissions[request_id] = {"event": event, "decision": None}

    # 发送 TG 按钮
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ 允许", callback_data=f"perm_allow:{request_id}"),
        InlineKeyboardButton("❌ 拒绝", callback_data=f"perm_deny:{request_id}"),
    ]])
    await bot.send_message(
        chat_id=data["chat_id"],
        message_thread_id=data["thread_id"],
        text=f"🔐 <b>权限请求</b>\n\n{data['description']}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )

    # 等待用户决策（超时 120s → deny）
    try:
        await asyncio.wait_for(event.wait(), timeout=120)
        decision = pending_permissions[request_id]["decision"]
    except asyncio.TimeoutError:
        decision = "deny"
    finally:
        del pending_permissions[request_id]

    return web.json_response({"decision": decision})
```

**超时机制**：
- Hook 脚本 timeout 设为 130s（settings.json 中配置）
- Bot 等待用户决策 120s
- 超时自动拒绝（fail-safe）

### 4.5 会话管理

**目标**：创建、列出、切换会话，管理会话状态。

**数据结构**：
```python
@dataclass
class Session:
    session_id: str | None       # Claude Code session UUID
    topic_id: int                # Telegram topic ID
    chat_id: int                 # Telegram chat ID
    project_dir: str             # 项目目录
    source: str                  # "telegram" | "terminal"
    proc: asyncio.subprocess.Process | None
    created_at: datetime
    name: str | None             # 用户自定义名称
    bypass: bool = False         # 权限 bypass 开关

# 全局状态
sessions: dict[int, Session] = {}        # topic_id → Session
session_index: dict[str, int] = {}       # session_id → topic_id
```

**命令设计**：
| 命令 | 功能 |
|------|------|
| `/start` | 初始化 Bot |
| `/projects` | 列出项目目录 → Inline Keyboard 选择 |
| `/sessions` | 列出活跃会话 |
| `/info` | 当前会话信息 |

### 4.6 Forum Topic = 会话

**目标**：每个 Claude Code 会话对应一个独立的 Telegram Forum Topic。

**创建流程**：
```python
async def create_session_topic(chat_id: int, project_name: str) -> int:
    """创建 Forum Topic 并返回 topic_id"""
    topic = await bot.create_forum_topic(
        chat_id=chat_id,
        name=f"🤖 {project_name}",
    )
    return topic.message_thread_id
```

**命名规则**：
- 新建：`🤖 {project_name}`
- 恢复：`🔄 {session_name or project_name}`
- 用户可通过 `/rename <名称>` 修改

### 4.7 白名单认证

**目标**：仅允许授权用户使用 Bot。

**技术方案**：
```python
ALLOWED_USERS = set(map(int, os.getenv("ALLOWED_USERS", "").split(",")))

def auth_required(func):
    """装饰器：白名单认证"""
    async def wrapper(update: Update, context):
        if update.effective_user.id not in ALLOWED_USERS:
            return  # 静默忽略
        return await func(update, context)
    return wrapper
```

所有 handler 均使用 `@auth_required` 装饰。

### 4.8 会话恢复 (Resume)

**目标**：恢复历史 Claude Code 会话，继续对话。

**技术方案**：

**JSONL 文件存储路径**：Claude Code 将会话记录存储在 `~/.claude/projects/<project-hash>/<session-id>.jsonl`，其中 `<project-hash>` 是项目目录路径的哈希值。默认扫描目录通过 `CLAUDE_PROJECTS_DIR` 环境变量配置，默认为 `~/.claude/projects`。

**扫描历史会话**：
```python
async def scan_sessions(projects_dir: str) -> list[dict]:
    """扫描 Claude Code JSONL 文件，提取会话元信息"""
    sessions = []
    for jsonl_file in Path(projects_dir).rglob("*.jsonl"):
        info = {"session_id": jsonl_file.stem, "path": str(jsonl_file)}
        # 读头部 8KB 取 cwd
        with open(jsonl_file, "r") as f:
            head = f.read(8192)
            for line in head.split("\n"):
                if line.strip():
                    event = json.loads(line)
                    if "cwd" in event:
                        info["cwd"] = event["cwd"]
                    break
        # 读尾部 4KB 取 custom-title
        with open(jsonl_file, "rb") as f:
            f.seek(max(0, jsonl_file.stat().st_size - 4096))
            tail = f.read().decode(errors="ignore")
            # 查找 custom-title 字段
            for line in reversed(tail.split("\n")):
                if "custom-title" in line:
                    try:
                        event = json.loads(line)
                        info["name"] = event.get("custom-title", "")
                    except: pass
                    break
        info["mtime"] = jsonl_file.stat().st_mtime
        sessions.append(info)
    return sorted(sessions, key=lambda s: s["mtime"], reverse=True)
```

**恢复命令**：
```
/resume                    → 列出最近 10 个会话 (Inline Keyboard)
/resume <名称或ID前缀>     → 模糊匹配恢复
```

**恢复后发消息**：通过 `claude -p "{text}" --resume {session_id}` 继续对话。

---

## 5. P1 特性技术方案

### 5.1 Hook 事件集成

**目标**：通过 4 个 Hook 脚本感知 Claude Code 会话生命周期。

**settings.json 配置**：
```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "python3 /path/to/hooks/permission.py",
        "timeout": 130
      }]
    }],
    "SessionStart": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "python3 /path/to/hooks/session_start.py"
      }]
    }],
    "Notification": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "python3 /path/to/hooks/notification.py"
      }]
    }],
    "Stop": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "python3 /path/to/hooks/stop.py"
      }]
    }]
  }
}
```

**Hook 输入格式 (stdin JSON)**：

| Hook | 关键字段 |
|------|---------|
| PreToolUse | `session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`, `tool_name`, `tool_input` |
| SessionStart | `session_id`, `transcript_path`, `cwd`, `source` (`"startup"` \| `"resume"` \| `"clear"` \| `"compact"`), `model`, `permission_mode` |
| Notification | `session_id`, `message`, `title` (可选), `notification_type` (matcher: `permission_prompt` \| `idle_prompt` \| `auth_success` \| `elicitation_dialog`) |
| Stop | `session_id`, `transcript_path`, `cwd`, `stop_hook_active`, `last_assistant_message`, `stop_reason` |

> **注意**：`tmux_pane` 不是 Hook stdin 的字段。若需检测 tmux 环境，应从环境变量 `$TMUX_PANE` 读取（参考 Claude Pilot session_start.py 的做法）。

**Hook 输出格式**：

| Hook | 输出能力 |
|------|---------|
| PreToolUse | `permissionDecision` (`"allow"` \| `"deny"`), `permissionDecisionReason`, `updatedInput` (修改工具参数), `additionalContext` (注入上下文) |
| Stop | `decision` (`"block"`) + `reason` — 可阻止 Claude 停止（防止过早退出） |
| SessionStart | 可选输出 `additionalContext`（向 Claude 注入额外上下文，如项目规范），无则 exit 0 |
| Notification | 无特殊输出要求，exit 0 即可 |

> **Stop Hook 的阻止能力**：这是一个有价值的功能。例如可以在 Claude 即将退出时检查任务是否真正完成，若未完成可返回 `{"decision": "block", "reason": "任务未完成"}` 阻止退出。

**共享工具模块 (hooks/common.py)**：
```python
import os, json

def get_bot_port():
    return int(os.environ.get("BOT_PORT", "5000"))

def get_bot_url():
    return f"http://localhost:{get_bot_port()}"

def read_stdin():
    import sys
    return json.loads(sys.stdin.read())

def post_to_bot(endpoint, data):
    import urllib.request
    payload = json.dumps(data).encode()
    req = urllib.request.Request(
        f"{get_bot_url()}{endpoint}",
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    return json.loads(urllib.request.urlopen(req, timeout=130).read())
```

### 5.2 Markdown 优雅渲染

**目标**：将 Claude 输出的 Markdown 转为 Telegram 支持的 HTML 子集。

**Telegram 支持的 HTML 标签**：
`<b>`, `<i>`, `<code>`, `<pre>`, `<a href>`, `<blockquote>`, `<s>`, `<u>`, `<tg-spoiler>`

**降级策略**：

| Markdown 格式 | Telegram 渲染 | 降级方案 |
|---------------|-------------|---------|
| `**bold**` | `<b>bold</b>` | 直接支持 |
| `*italic*` | `<i>italic</i>` | 直接支持 |
| `` `code` `` | `<code>code</code>` | 直接支持 |
| ` ```code block``` ` | `<pre>code block</pre>` | 直接支持（含语言标签） |
| `[link](url)` | `<a href="url">link</a>` | 直接支持 |
| `> quote` | `<blockquote>quote</blockquote>` | 直接支持 |
| `~~strike~~` | `<s>strike</s>` | 直接支持 |
| `# heading` | `<b>heading</b>` | 降级为粗体 |
| `\| table \|` | `<pre>table</pre>` | 降级为预格式化 |
| `![img](url)` | `[图片: alt](url)` | 降级为文本链接 |
| `- list item` | `• list item` | 符号前缀 |

**实现**：自定义 `mistune.HTMLRenderer` 子类 `TelegramHTMLRenderer`。

### 5.3 消息智能分段

**目标**：长消息按段落边界拆分，每段 ≤ 4096 字符（TG 限制）。

**算法**：
```python
def split_message(markdown_text: str) -> list[tuple[str, str]]:
    """返回 [(html, raw_markdown), ...]"""
    html_full = render_to_tg_html(markdown_text)
    if len(html_full) <= 4096:
        return [(html_full, markdown_text)]

    chunks = []
    blocks = markdown_text.split("\n\n")
    current_md = ""

    for block in blocks:
        test_md = current_md + ("\n\n" if current_md else "") + block
        test_html = render_to_tg_html(test_md)
        if len(test_html) <= 4096:
            current_md = test_md
        else:
            if current_md:
                chunks.append((render_to_tg_html(current_md), current_md))
            # 单块超长 → 硬切
            if len(render_to_tg_html(block)) > 4096:
                for i in range(0, len(block), 4000):
                    sub = block[i:i+4000]
                    chunks.append((sub, sub))  # 纯文本 fallback
            else:
                current_md = block
    if current_md:
        chunks.append((render_to_tg_html(current_md), current_md))
    return chunks
```

**发送时 fallback**：HTML 解析失败 → 纯文本重试。
```python
async def send_rendered(chat_id, topic_id, text):
    for html, raw in split_message(text):
        try:
            await bot.send_message(chat_id, html, message_thread_id=topic_id, parse_mode="HTML")
        except Exception:
            await bot.send_message(chat_id, raw, message_thread_id=topic_id)
```

### 5.4 工具调用可视化

**目标**：清晰展示 Claude 每一步在做什么。

**工具描述格式**：

| 工具 | 展示格式 |
|------|---------|
| Bash | `🔧 执行命令: {command}` (截断 200 字符) |
| Read | `📖 读取文件: {file_path}` |
| Edit | `✏️ 编辑文件: {file_path}` |
| Write | `📝 写入文件: {file_path}` |
| Glob | `🔍 搜索文件: {pattern}` |
| Grep | `🔎 搜索内容: {pattern}` |
| Task | `🧩 子任务: {description}` (截断 100 字符) |
| 其他 | `🔧 {tool_name}: {JSON摘要}` |

**计数器**：每个会话维护工具调用计数，展示为 `[#N]` 前缀。

### 5.5 Bypass 模式

**目标**：一键关闭权限审批，适合信任场景。

**实现**：
- `/bypass` 切换当前会话的 bypass 开关
- bypass 开启时：启动 claude 加 `--dangerously-skip-permissions` 参数
- 或者：Hook 脚本检查 bypass 状态，直接返回 allow

**方案选择**：推荐在 Hook 脚本层面实现，而非 `--dangerously-skip-permissions`（更安全，保留 Hook 通知能力）。

```python
# hooks/permission.py 中
data = read_stdin()
# 检查 Bot 是否设置了 bypass
resp = post_to_bot("/check_bypass", {
    "session_id": data.get("session_id"),
    "chat_id": os.environ.get("TELEGRAM_CHAT_ID"),
    "thread_id": os.environ.get("TELEGRAM_THREAD_ID"),
})
if resp.get("bypass"):
    # 直接允许，但仍然发一条通知
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": "Bypass mode enabled"
    }}))
    sys.exit(0)
# ... 正常权限审批流程
```

### 5.6 终端会话监控

**目标**：电脑跑长任务，手机实时看进度。

**JSONL Watcher**：
```python
async def watch_transcript(session: Session, transcript_path: str):
    """监听 JSONL 文件，转发新内容到 TG"""
    seen_uuids = set()
    last_pos = 0

    while True:
        try:
            await asyncio.sleep(0.5)
            file_size = os.path.getsize(transcript_path)
            if file_size <= last_pos:
                continue
            with open(transcript_path, "r") as f:
                f.seek(last_pos)
                new_data = f.read()
                last_pos = f.tell()
            for line in new_data.strip().split("\n"):
                if not line.strip():
                    continue
                event = json.loads(line)
                uuid = event.get("uuid")
                if uuid and uuid in seen_uuids:
                    continue
                if uuid:
                    seen_uuids.add(uuid)
                await process_transcript_event(session, event)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Watcher error: {e}")
```

### 5.7 图片/截图展示

**目标**：用户在 TG 发图 → 保存到本地 → Claude 通过 Read 工具读取分析。

**数据流**：
```
用户在 TG 发送图片
    │
    ▼
Bot 接收 (handle_photo)
    │
    ├─ 获取最高分辨率版本 (photo[-1])
    ├─ 下载到项目目录: {project_dir}/.claude-images/{timestamp}.jpg
    │
    ▼
构建 prompt 发给 claude -p
    │
    ├─ "用户发送了一张图片，请用 Read 工具查看: .claude-images/{filename}"
    ├─ --allowedTools "Read" (确保 Read 自动批准)
    │
    ▼
Claude 使用 Read 工具读取图片
    │
    ├─ Read 工具对图片返回 base64 编码的视觉内容
    ├─ Claude 分析图片内容并回复
    │
    ▼
回复通过正常流式输出转发到 TG
```

**注意事项**：
- Claude Code CLI 需通过原生 Anthropic 后端运行（`ANTHROPIC_API_KEY` 直连或 Claude Max），Read 工具读图片才能正常工作
- 图片保存在项目目录下的隐藏文件夹，`.gitignore` 排除
- 支持的格式：JPEG, PNG, GIF, WebP

### 5.8 交互式选择 → Inline Keyboard 按钮

**目标**：将需要用户选择的场景（项目选择、会话恢复等）转为 TG 按钮交互。

**应用场景**：

| 场景 | 按钮 | callback_data 格式 |
|------|------|-------------------|
| 项目选择 | `[项目名A] [项目名B] ...` | `proj:{dir_name}` |
| 会话恢复 | `[会话名/ID] ...` | `resume:{session_id}` |
| 权限审批 | `[✅ 允许] [❌ 拒绝]` | `perm_allow:{req_id}` / `perm_deny:{req_id}` |
| 删除确认 | `[确认删除] [取消]` | `del_yes:{session_id}` / `del_no:{session_id}` |

**统一回调处理**：
```python
async def handle_callback(update: Update, context):
    query = update.callback_query
    await query.answer()

    data = query.data
    if data.startswith("proj:"):
        await handle_project_select(query, data[5:])
    elif data.startswith("resume:"):
        await handle_resume_select(query, data[7:])
    elif data.startswith("perm_allow:") or data.startswith("perm_deny:"):
        await handle_permission_decision(query, data)
    elif data.startswith("del_yes:") or data.startswith("del_no:"):
        await handle_delete_confirm(query, data)
```

---

## 6. 图片输入验证结论

### 6.1 验证结果

| 方式 | 可行性 | 说明 |
|------|--------|------|
| `claude -p "prompt"` 直接传图 | ❌ 不可行 | CLI 不支持 `--image` 参数 |
| stdin pipe 二进制图片 | ❌ 不可行 | stdin 被当作纯文本处理 |
| `--input-format stream-json` + 图片块 | ⚠️ 理论可行 | 无官方文档，有已知 bug，风险高 |
| **Read 工具读本地图片** | ✅ 可行 | **推荐方案**。原生 API 下 Read 工具可正确读取图片 |
| Agent SDK streaming input | ✅ 可行 | 需要额外依赖，复杂度高，远期考虑 |

### 6.2 推荐方案

**TG 发图 → 保存本地 → Claude Read 工具读取**

这是当前最简单可靠的方案：
1. Bot 将 Telegram 图片下载到项目目录的 `.claude-images/` 子目录
2. 在 prompt 中告诉 Claude 图片的本地路径
3. Claude 自动使用 Read 工具读取图片文件（Read 工具原生支持图片文件的视觉读取）
4. Claude 分析图片内容后回复

**前提条件**：Claude Code CLI 需要通过原生 Anthropic 后端运行（即使用 `ANTHROPIC_API_KEY` 直连 Anthropic，或通过 Claude Max 订阅）。Read 工具读取图片是 Claude Code CLI 的内建能力，与用户应用层的 API 调用方式无关。当 Claude Code 通过 OpenRouter/AWS Bedrock 等第三方代理运行时，图片数据在 tool result 传输过程中可能损坏（已知问题 [#18588](https://github.com/anthropics/claude-code/issues/18588)）。

---

## 7. 关键技术难点与解决方案

### 7.1 并发控制

**问题**：用户在同一话题快速连发多条消息，可能同时启动多个 `claude -p` 进程。

**解决**：per-topic asyncio.Lock

```python
topic_locks: dict[int, asyncio.Lock] = {}

async def handle_message(update, context):
    topic_id = update.message.message_thread_id
    if topic_id not in topic_locks:
        topic_locks[topic_id] = asyncio.Lock()

    async with topic_locks[topic_id]:
        await process_message(update, context)
```

### 7.2 进程生命周期管理

**问题**：`claude -p` 进程可能因各种原因挂起或崩溃。

**解决**：
- 进程超时：设置合理的等待时间，超时后 SIGTERM → SIGKILL
- 退出码检查：非零退出码 → 错误通知
- 进程引用清理：进程结束后立即清除 `session.proc`

```python
try:
    async for line in proc.stdout:
        # 处理输出...
        pass
    await asyncio.wait_for(proc.wait(), timeout=10)
except asyncio.TimeoutError:
    proc.terminate()
    await asyncio.sleep(2)
    if proc.returncode is None:
        proc.kill()
finally:
    session.proc = None
```

### 7.3 Hook 通信可靠性

**问题**：Hook 脚本通过 HTTP 与 Bot 通信，可能遇到 Bot 未启动或网络问题。

**解决**：
- Hook 脚本捕获连接错误，采用**静默回退**策略（不输出任何内容，exit 0）
- 当 Hook 不输出决策时，Claude Code 回退到自身的权限机制（终端交互确认）
- HTTP 超时设为 130s（覆盖用户决策等待时间）
- Bot 的 HTTP 服务与 Telegram polling 同时启动

```python
# hooks/permission.py 中的错误处理
try:
    resp = post_to_bot("/permission", payload)
except Exception as e:
    # Bot 不可达 → 静默退出，不输出任何决策
    # Claude Code 将回退到自身的权限机制（终端交互确认）
    # 这比默认 allow（不安全）或默认 deny（阻塞工作）都更合理
    sys.exit(0)
```

> **设计决策**：参考 Claude Pilot 的实践——Bot 挂了时，Hook 不输出 = 不干预，Claude Code 回退到正常的终端权限流程。这避免了"默认放行所有操作"（不安全）和"默认拒绝一切"（阻塞工作）两个极端。

### 7.4 Telegram 速率限制

**问题**：Telegram Bot API 有速率限制（约 30 条/秒/群组），流式输出可能触发。

**解决**：
- 文本缓冲：不逐行发送，累积到段落或 2000 字符后发送
- 消息编辑：对连续文本，编辑已发送的消息而非新发（减少消息数）
- 退避重试：捕获 429 错误，等待后重试

```python
async def send_with_rate_limit(chat_id, topic_id, text, **kwargs):
    """带速率限制的发送"""
    try:
        return await bot.send_message(chat_id, text, message_thread_id=topic_id, **kwargs)
    except telegram.error.RetryAfter as e:
        await asyncio.sleep(e.retry_after)
        return await bot.send_message(chat_id, text, message_thread_id=topic_id, **kwargs)
```

**高级策略（可选）**：对流式文本使用"编辑最后一条消息"模式——先发一条，后续文本 edit_message 追加，减少消息数量。

### 7.5 会话状态持久化

**问题**：Bot 重启后内存会话状态丢失。

**解决方案（分阶段）**：

- **MVP**：内存存储 + `/resume` 手动恢复（足够个人使用）
- **后续**：可选 SQLite 本地持久化（sessions 表）

```python
# 简单的 JSON 文件持久化（MVP 后续可加）
SESSIONS_FILE = "data/sessions.json"

def save_sessions():
    data = {str(k): v.to_dict() for k, v in sessions.items()}
    Path(SESSIONS_FILE).write_text(json.dumps(data, indent=2))

def load_sessions():
    if Path(SESSIONS_FILE).exists():
        data = json.loads(Path(SESSIONS_FILE).read_text())
        for k, v in data.items():
            sessions[int(k)] = Session.from_dict(v)
```

### 7.6 权限审批竞态条件

**问题**：asyncio.Event + HTTP 长轮询的权限审批方案存在几个竞态风险。

**风险 1：用户快速双击按钮**

用户可能在短时间内点击两次"允许"或"拒绝"按钮，第二次 callback 时 `pending_permissions[request_id]` 可能已被 `del`。

**缓解**：callback handler 中先检查 key 是否存在，不存在则静默忽略。
```python
async def handle_permission_decision(query, data):
    action, request_id = data.split(":", 1)
    if request_id not in pending_permissions:
        await query.answer("该请求已处理")
        return
    # ... 正常处理
```

**风险 2：Bot 重启导致 pending_permissions 丢失**

Bot 重启后内存清空，但 Hook 脚本仍在等待 HTTP 响应。连接会因 Bot 进程终止而断开，Hook 脚本的 `urllib.request.urlopen` 将抛出异常，触发静默回退（§7.3）。

**缓解**：这个场景下静默回退策略天然处理了——Hook 连接中断 → 异常 → exit 0 → Claude 回退到终端权限。

**风险 3：多个权限请求同时到达，用户混淆按钮**

当 Claude 快速连续执行多个工具时，TG 中可能出现多条权限请求消息，用户可能点错。

**缓解**：
- 每条权限请求消息包含完整的工具描述（工具名 + 关键参数），方便用户区分
- 按钮消息格式统一，展示 `[#N]` 序号帮助定位
- 用户点击后编辑消息，标记已处理状态（如 `✅ 已允许` 或 `❌ 已拒绝`）

### 7.7 环境变量传递

**问题**：Hook 脚本需要知道当前消息属于哪个 TG 话题，才能将权限请求路由到正确位置。

**解决**：通过进程环境变量传递 chat_id 和 thread_id。

- TG 会话：`claude -p` 启动时设置 `TELEGRAM_CHAT_ID` 和 `TELEGRAM_THREAD_ID`
- 终端会话：SessionStart Hook 从 Bot API 获取对应的 topic_id，后续 Hook 通过 session_id → topic_id 映射查找

### 7.8 从 CLI `-p` 到 Agent SDK 的升级路径

当前架构选择 CLI `-p` 作为主要集成方式，但应在模块设计上为未来迁移到 Agent SDK 留出空间。

**架构兼容性设计**：`claude_runner.py` 模块应定义统一的抽象接口，底层实现可替换。

```python
# claude_runner.py 中的接口抽象
class ClaudeRunner(ABC):
    @abstractmethod
    async def run(self, prompt: str, session_id: str | None, cwd: str) -> AsyncIterator[Event]:
        """启动 Claude 会话，返回事件流"""
        ...

    @abstractmethod
    async def interrupt(self):
        """中断当前执行"""
        ...

class CLIRunner(ClaudeRunner):
    """基于 claude -p 的实现（当前方案）"""
    ...

class SDKRunner(ClaudeRunner):
    """基于 Agent SDK 的实现（远期方案）"""
    ...
```

**迁移时机**：当需要以下能力时考虑迁移到 Agent SDK：
- 更精细的工具控制（修改工具参数、注入上下文）
- 子代理管理（Subagents）
- 结构化输出（JSON Schema 约束）
- 会话分叉（fork）

### 7.9 日志与调试

**日志策略**：
- Bot 主进程：统一日志目录 `logs/`，按日期滚动，保留 7 天
- Hook 脚本：stderr 输出记录到 `logs/hooks.log`（Claude Code 会捕获 Hook 的 stderr）
- 关键操作日志：会话创建/恢复/删除、权限决策、进程启动/退出

**Hook 调试**：
- Hook 脚本可通过 `sys.stderr.write()` 输出调试信息（不影响 stdout 的 JSON 输出）
- Bot 的 `/health` 端点返回当前状态（活跃会话数、pending 权限数、进程数）
- 建议实现 `/debug` 命令，展示当前所有内部状态（仅管理员可用）

---

## 8. 参考资料

| 资源 | 说明 |
|------|------|
| [Claude Code Headless 文档](https://code.claude.com/docs/en/headless) | CLI `-p` 模式完整用法 |
| [Claude Code Hooks 文档](https://code.claude.com/docs/en/hooks) | Hook 系统配置和输入输出格式 |
| [Claude Code Hooks 指南](https://code.claude.com/docs/en/hooks-guide) | Hook 实践指南 |
| [Claude Code CLI 参考](https://code.claude.com/docs/en/cli-reference) | 完整 CLI 参数列表 |
| [python-telegram-bot 文档](https://docs.python-telegram-bot.org/) | Telegram Bot 库 API |
| [Telegram Bot API](https://core.telegram.org/bots/api) | Telegram 官方 Bot API |
| [Claude Pilot 源码](file:///Users/zhangxu/workspace/mk/claude-pilot) | 参考实现 |
| [Happy Coder 源码](file:///Users/zhangxu/workspace/github/happy) | 参考实现 |
