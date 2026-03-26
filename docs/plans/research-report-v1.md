# Code Anywhere — 深度研究报告 v1

> 研究目标：调研远程控制 Claude Code 的现有方案，为 Code Anywhere 项目提供特性参考和技术路径选择依据。
>
> 研究日期：2026-03-24

---

## 目录

1. [项目 A：Happy Coder — 详细分析](#1-项目-a-happy-coder)
2. [项目 B：Claude Pilot — 详细分析](#2-项目-b-claude-pilot)
3. [官方方案汇总](#3-官方方案汇总)
4. [社区方案汇总](#4-社区方案汇总)
5. [特性对比表格](#5-特性对比表格)
6. [技术路径对比](#6-技术路径对比)
7. [推荐关键特性清单](#7-推荐关键特性清单)

---

## 1. 项目 A：Happy Coder

### 1.1 项目概述

**定位**：Claude Code 和 Codex 的移动/Web 客户端，核心强调端到端加密和多设备无缝切换。

**规模**：商用级产品，monorepo 多包架构，代码量大、工程复杂度高。

**许可**：MIT 开源 | **作者**：Kirill Dubovitskiy 等

### 1.2 技术栈

| 层 | 技术 | 说明 |
|---|------|------|
| 移动端 | Expo (React Native) + React 19 | iOS/Android 双端 |
| Web 端 | React + Expo Router v6 | 与移动端共享代码 |
| 桌面端 | Tauri v2 | macOS 原生应用 |
| CLI | Node.js + TypeScript + Ink | 终端 UI (React for Terminal) |
| 服务器 | Fastify 5.x + Prisma ORM | PostgreSQL + Redis + S3 |
| 实时通信 | Socket.IO 4.x | 多 scope 连接 |
| 加密 | libsodium + TweetNaCl.js | 客户端 E2E 加密 |
| 状态管理 | Zustand | 轻量响应式 |
| 共享类型 | happy-wire (Zod schemas) | 中央化 wire 协议 |

### 1.3 架构设计

```
┌─────────────────────────────────────────────────────┐
│          用户设备层 (iOS / Android / Web / macOS)    │
│          全部客户端加密，服务器只存 opaque blob       │
└────────────────────────┬────────────────────────────┘
                         │ HTTPS + Socket.IO (E2E encrypted)
                         ▼
┌─────────────────────────────────────────────────────┐
│              Happy Server (Fastify)                  │
│  ├─ Auth (公钥签名, 无密码)                          │
│  ├─ Sessions + Messages (加密 blob 存储)             │
│  ├─ Machines (daemon 元数据)                         │
│  ├─ Artifacts (加密资产)                             │
│  ├─ Access Keys (per-session-per-machine)            │
│  ├─ KV Store (加密用户数据)                          │
│  ├─ Social (好友、Feed、分享)                        │
│  └─ Integrations (GitHub OAuth, Vendor Tokens)       │
└────────┬──────────────┬─────────────────┬───────────┘
         ▼              ▼                 ▼
     PostgreSQL       Redis            S3/MinIO
```

**CLI + Daemon 架构**：

```
CLI 入口 → 子命令路由 (doctor/auth/connect/claude/codex/gemini)
              │
              ├─ Local Mode: 直接运行 Claude 进程, 监听 JSONL
              └─ Remote Mode: Daemon 后台进程 + HTTP IPC
                  ├─ Control Server: /list, /spawn-session, /stop-session
                  └─ Session Pool: 多个后台 Agent 进程
```

**会话协议层 (Session Protocol)**：

统一的加密事件流，核心 envelope 结构：
```json
{
  "role": "user | agent",
  "turn": "cuid2",
  "subagent": "cuid2 (可选, 用于 Task tool 子代理)",
  "ev": {
    "t": "turn-start | text | tool-call-start | tool-call-end | stop | turn-end",
    "text": "...", "call": "...", "name": "...", "args": {}
  },
  "time": 1711234567890
}
```

### 1.4 核心特性

| # | 特性 | 描述 |
|---|------|------|
| 1 | **E2E 加密** | 服务器完全看不见用户代码，所有内容客户端加密存储 |
| 2 | **多平台客户端** | iOS + Android + Web + macOS (Tauri) |
| 3 | **设备间无缝切换** | Daemon + RPC + Socket.IO 实现即时控制权转移 |
| 4 | **多 Agent 支持** | Claude Code + Codex + Gemini，统一会话协议抽象 |
| 5 | **Sidechain 消息** | 优雅处理 Task tool 子代理的异步消息流 |
| 6 | **社交与分享** | 好友列表、活动 Feed、会话共享 |
| 7 | **权限模式系统** | 7 种模式 (default → yolo)，支持沙箱强制 |
| 8 | **实时同步** | seq-based 单调递增同步 + optimistic locking |
| 9 | **Daemon 后台运行** | CLI 退出后进程继续运行 |
| 10 | **公钥无密码认证** | 基于 ed25519 签名，无密码泄露风险 |

### 1.5 亮点功能

1. **E2E 加密架构** — 服务器存储的全是加密 blob，真正做到了 zero-knowledge。per-session data key + tweetnacl.box 密钥交换，既安全又支持多设备同步。

2. **Sidechain 消息处理** — Claude SDK 的 Task tool 会产生异步子代理消息，Happy 通过孤立缓冲 + 延迟映射优雅解决了消息乱序问题，支持重启后重放。

3. **Activity Cache + Batch Writes** — 高频心跳事件在内存缓冲，定期 flush 到 DB，将数据库写入压力降低到 1-2%。

4. **happy-wire 共享库** — 中央化的 Zod schemas 作为 npm 包发布，保证客户端/服务器/CLI 类型一致性。

5. **双路由 Launcher** — Local Launcher (交互模式, 共享 TTY) 和 Remote Launcher (后台模式, SDK 流式) 两条路径，覆盖不同使用场景。

### 1.6 局限性

| 局限 | 影响 |
|------|------|
| **部署复杂** | 需要 PostgreSQL + Redis + S3，非个人开发者友好 |
| **加密导致搜索不可能** | 服务端无法搜索/索引消息内容 |
| **Sidechain 复杂度高** | 多层缓冲和映射逻辑，维护成本大 |
| **学习曲线** | 密钥管理 (QR code)、Daemon、多权限模式 |
| **全局 seq counter** | 大规模用户可能成为瓶颈 |

---

## 2. 项目 B：Claude Pilot

### 2.1 项目概述

**定位**：通过 Telegram Bot 远程控制 Claude Code CLI 的轻量工具，支持手机实时监控、权限审批和指令发送。

**规模**：个人工具级，单文件为主（bot.py 1372 行），轻量但功能完善。

**许可**：MIT 开源 | **作者**：Mickey

### 2.2 技术栈

| 层 | 技术 | 说明 |
|---|------|------|
| 运行时 | Python 3.10+ | asyncio 全异步 |
| Telegram | python-telegram-bot ^22.6 | Polling 模式 |
| HTTP Server | aiohttp ^3.9 | Hook 回调接收 |
| Markdown | mistune ^3.2 (GFM) | Telegram HTML 渲染 |
| 终端交互 | tmux | send-keys 注入 |
| 存储 | 内存 (dict) + Claude JSONL | 无外部数据库 |

**依赖极简**：仅 3 个核心 pip 包 + 系统级 tmux。

### 2.3 架构设计

```
┌──────────────────────────────────────────┐
│     Telegram 群组 (Forum Topic 模式)      │
│  话题 A (终端会话)  话题 B (TG 会话)       │
└────────────────────┬─────────────────────┘
                     │ Telegram Bot API (Polling)
                     ▼
┌──────────────────────────────────────────┐
│            bot.py 核心逻辑                │
│  ├─ Telegram Polling (命令 + 消息)        │
│  ├─ HTTP API Server (port 5000)          │
│  ├─ 会话管理 (内存 dict)                  │
│  ├─ JSONL 文件监听 (轮询 0.5s)            │
│  ├─ 权限仲裁 (asyncio.Event)             │
│  └─ Markdown→TG HTML 渲染               │
└──────┬───────────────┬───────────────────┘
       │               │
       ▼               ▼
  claude -p          Claude Code Hooks
  (TG 会话)          (4 个 Hook 脚本)
                       ├─ session_start.py
                       ├─ permission.py
                       ├─ notification.py
                       └─ stop.py
                            │ HTTP POST
                            ▼
                     终端 Claude 进程
                     (tmux 中运行)
```

### 2.4 四种通信模式

| 模式 | 场景 | 工作流 |
|------|------|--------|
| **终端流式** | 电脑跑长任务 | Claude → Hook → Bot → JSONL 监听 → TG 实时转发 |
| **TG 会话** | 纯手机操控 | TG 消息 → Bot 启动 `claude -p` → 流式读 stdout → TG |
| **终端交互** | tmux 中注入 | TG 消息 → Bot → `tmux send-keys` → Claude 处理 |
| **会话恢复** | 继续历史 | `/resume` → 读 JSONL 头尾 → `claude -p --resume` |

### 2.5 核心特性

| # | 特性 | 描述 |
|---|------|------|
| 1 | **双模式会话** | 终端监控模式 + TG 纯操控模式 |
| 2 | **权限远程审批** | PreToolUse Hook → HTTP → TG 按钮 (允许/拒绝) |
| 3 | **JSONL 实时监听** | 0.5s 轮询 + UUID 去重，实时转发终端输出 |
| 4 | **tmux 消息注入** | Escape → i → text → Enter 模拟用户输入 |
| 5 | **会话持久化恢复** | 读 JSONL 文件恢复上下文，支持 ID/名称模糊匹配 |
| 6 | **Markdown 优雅降级** | 表格→pre, 标题→bold, 图片→链接 |
| 7 | **消息智能分段** | 按段落边界拆分 ≤4096 字符，不破坏格式 |
| 8 | **11 个 Bot 命令** | /projects, /resume, /rename, /interrupt, /quit, /delete, /info, /retry, /setdir, /bypass, /start |
| 9 | **白名单认证** | ALLOWED_USERS 限制访问 |
| 10 | **Bypass 模式** | /bypass 一键关闭权限审批 |

### 2.6 亮点功能

1. **Hook + HTTP + asyncio.Event 权限审批** — 完美的异步解耦设计。Hook 脚本通过 HTTP POST 发起权限请求，Bot 发 TG 按钮等用户点击，asyncio.Event 通知 Hook 继续。120s 超时自动拒绝（fail-safe）。

2. **双模式无缝集成** — 终端 Claude 和 TG 会话使用同一套 Bot 管理，话题自动创建/命名，用户体验一致。

3. **Telegram Markdown 渲染器** — 自定义 TelegramHTMLRenderer 类，对 GFM 所有格式做了有意义的降级（不是简单丢弃），fallback 到纯文本确保消息必达。

4. **tmux 交互注入** — 通过 send-keys literal 模式注入文本，per-pane Lock 防止并发冲突，pane 存在检查防止注入到已关闭会话。

5. **零外部依赖部署** — 无需数据库、无需 Redis、无需云服务。pip install 三个包 + 配置 .env 即可运行。

### 2.7 局限性

| 局限 | 影响 |
|------|------|
| **单用户** | 白名单模式，不适合多用户共享 |
| **Telegram only** | 不支持其他 IM 平台 |
| **内存会话** | Bot 重启丢失所有会话映射（JSONL 文件保留） |
| **单文件架构** | 1372 行 bot.py，模块化不足 |
| **JSONL 轮询** | 0.5s 延迟，非实时推送 |
| **TG 速率限制** | 长输出可能触发 30条/秒 限制 |
| **无加密** | 消息明文通过 Telegram 传输 |

---

## 3. 官方方案汇总

Anthropic 官方提供了 **5 种**与 Claude Code 集成的方式：

### 3.1 Agent SDK (Python / TypeScript) — 最强大

```
npm install @anthropic-ai/claude-agent-sdk
pip install claude-agent-sdk
```

- 编程方式调用 Claude Code 全部能力
- 异步流式消息、完整工具控制、会话管理（创建/恢复/分叉）
- 自定义 Hooks 生命周期回调
- 子代理 (Subagents) 支持
- MCP 服务器集成
- 结构化输出 (JSON Schema)

**适合**：构建产品级应用，需要完整控制的场景。

### 3.2 CLI Headless 模式 (`-p` / `--print`) — 最简单

```bash
claude -p "Fix the bug" --output-format stream-json --verbose
claude -p "Continue" --resume "$session_id"
```

- 三种输出格式：text / json / stream-json
- 会话保持：`--session-id` / `--continue` / `--resume`
- 工具自动批准：`--allowedTools "Bash(git diff *),Read,Edit"`
- 预算控制：`--max-budget-usd`
- 管道支持：`cat file.txt | claude -p "summarize"`

**适合**：脚本集成、CI/CD、简单的 Bot 后端。

### 3.3 Channels (官方 Telegram/Discord 集成) — 研究预览

```bash
/plugin install telegram@claude-plugins-official
/telegram:configure <bot_token>
claude --channels plugin:telegram@claude-plugins-official
```

- 基于 MCP 协议的双向通信 Channel
- 官方 Telegram 和 Discord 插件
- 工具：reply（发消息）、react（表情）、edit_message（编辑）
- 需要 Claude Code v2.1.80+ 和 claude.ai 登录
- 需要 Bun 运行时

**注意**：研究预览阶段，API 可能变化。

### 3.4 Remote Control — 从手机/浏览器驾驶本地会话

```bash
claude remote-control --name "My Project"
claude --remote-control
```

- 通过 claude.ai/code 或 Claude App 连接本地会话
- QR 码手机扫码连接
- 多设备同步，断线重连
- 支持 `--spawn worktree` 并发多会话

**适合**：需要手机临时接管的场景，但依赖 Anthropic 平台。

### 3.5 Dispatch (移动端派发)

- 通过 Claude 移动 App 直接派发任务到本地 Claude Desktop
- 无需 CLI 操作

---

## 4. 社区方案汇总

### 4.1 基于 tmux 键盘注入方案

| 项目 | 语言 | 特点 |
|------|------|------|
| **ccgram** (alexei-led) | — | TG Forum topic 绑定 tmux 窗口，多 Agent 支持 |
| **ccgram** (jsayubi) | — | Hook 触发 TG 通知 + tmux 键盘注入 |
| **ccbot** (six-ddc) | — | 1 topic = 1 tmux window = 1 session |
| **claudecode-telegram** (hanxiao) | — | Telegram bridge for Claude Code |

### 4.2 基于 SDK/子进程方案

| 项目 | 语言 | 特点 |
|------|------|------|
| **claude-code-telegram** (RichardAtCT) | — | 完整 TG Bot，会话持久化，目录沙箱 |
| **Claude-Code-Remote** (JessyTsui) | — | 多通道 (Email/TG/Discord/LINE)，Hook 通知 |
| **claude-code-telegram-control** | — | 移动优先的远程控制 |

### 4.3 Web UI 方案

| 项目 | 特点 |
|------|------|
| **CloudCLI** (siteboon) | 免费开源 WebUI，远程管理会话 |
| **247-claude-code-remote** (QuivrHQ) | Tailscale 安全连接 + Fly.io VM |
| **hello-halo** (openkursar) | 可视化 GUI，类似 Claude Cowork |
| **open-claude-cowork** (ComposioHQ) | 开源 Cowork + 500+ SaaS 集成 |

---

## 5. 特性对比表格

### 5.1 Happy vs Claude Pilot 核心对比

| 维度 | Happy Coder | Claude Pilot |
|------|-------------|--------------|
| **定位** | 商用级多平台客户端 | 个人级 Telegram Bot |
| **代码规模** | 数万行 (monorepo) | ~1500 行 (Python) |
| **IM 平台** | 自有 App (iOS/Android/Web) | Telegram |
| **与 Claude 集成** | SDK 直接调用 + JSONL 监听 | CLI `-p` + Hook + JSONL 监听 |
| **加密** | E2E (libsodium + TweetNaCl) | 无 (明文走 TG) |
| **认证** | 公钥签名 (无密码) | TG 白名单 (user_id) |
| **权限审批** | 7 种模式 + 沙箱 + 推送通知 | Hook → HTTP → TG 按钮 |
| **会话恢复** | 完整状态同步 (seq-based) | JSONL 读取 + `--resume` |
| **多 Agent** | Claude + Codex + Gemini | Claude only |
| **多设备** | 无缝切换 (Daemon + RPC) | 单设备 (TG) |
| **后台运行** | Daemon 进程管理 | tmux + Bot 进程 |
| **社交功能** | 好友、Feed、会话分享 | 无 |
| **部署复杂度** | 高 (PG + Redis + S3) | 极低 (pip install + .env) |
| **开箱体验** | 需要密钥管理/设备配对 | 配置即用 |
| **Markdown 渲染** | 客户端原生 UI | TG HTML 降级 |
| **实时性** | Socket.IO 推送 | JSONL 0.5s 轮询 |

### 5.2 全方案综合对比

| 特性 | Happy | Claude Pilot | 官方 Channel | 官方 Remote Control | Agent SDK |
|------|-------|-------------|-------------|-------------------|-----------|
| 移动端访问 | ✅ 原生 App | ✅ Telegram | ✅ Telegram | ✅ claude.ai App | ❌ 需自建 |
| E2E 加密 | ✅ | ❌ | ❌ | ✅ | ❌ 需自建 |
| 权限审批 | ✅ 多模式 | ✅ TG 按钮 | ❌ | ✅ | ✅ 编程控制 |
| 会话恢复 | ✅ | ✅ | ❓ 未知 | ✅ | ✅ |
| 多 Agent | ✅ 3种 | ❌ | ❌ | ❌ | ✅ 可扩展 |
| 部署难度 | 高 | 极低 | 中 | 低 (内置) | 中 |
| 离线运行 | ✅ | ✅ (tmux) | ❌ 需登录 | ❌ 需登录 | ✅ |
| 开源 | ✅ MIT | ✅ MIT | ✅ | ❌ 内置 | ✅ |
| 自主可控 | ✅ | ✅ | 部分 | ❌ 依赖平台 | ✅ |

---

## 6. 技术路径对比

与 Claude Code 集成有 4 种技术路径：

### 路径 1：Agent SDK (官方推荐, 最强大)

```
优点: 完整 API, 流式消息, 工具控制, 会话管理, 子代理支持
缺点: 依赖 SDK 版本, 需要编程集成
适合: 产品级应用
代表: Happy Coder (remote launcher)
```

### 路径 2：CLI `-p` 模式 (最简单)

```
优点: 一行命令, 支持管道, JSON/stream-json 输出
缺点: 每次调用启动新进程, 交互能力有限
适合: 脚本/Bot 后端, CI/CD
代表: Claude Pilot (TG 会话)
```

### 路径 3：Hook + HTTP 回调 (事件驱动)

```
优点: 不侵入 Claude Code, 生命周期完整 (Session/Tool/Stop)
缺点: 只能被动接收, 主动控制需额外手段
适合: 通知/审批/监控场景
代表: Claude Pilot (终端会话)
```

### 路径 4：tmux 键盘注入 (社区主流)

```
优点: 完全不依赖 SDK, 不受版本限制
缺点: 解析不精确, 竞态风险, 依赖 tmux
适合: 快速 hack, 个人使用
代表: ccgram, ccbot 等社区项目
```

**推荐组合**：**路径 2 (CLI -p) + 路径 3 (Hook)** — 兼顾主动发起和被动监听，部署简单，不依赖外部 SDK 包。如果需要更强大的能力，可升级到路径 1 (Agent SDK)。

---

## 7. 推荐关键特性清单

基于研究分析，为 Code Anywhere 项目推荐以下特性，按优先级分层：

### P0 — 核心必备 (MVP)

| # | 特性 | 参考来源 | 必要性说明 |
|---|------|---------|-----------|
| 1 | **Telegram Bot 基础通信** | Claude Pilot | 项目核心：通过 TG 发送消息，接收 Claude 回复 |
| 2 | **CLI `-p` 模式集成** | Claude Pilot | 最简单可靠的 Claude Code 调用方式 |
| 3 | **流式输出转发** | Claude Pilot, Happy | 实时看到 Claude 工作进展，不用等完整回复 |
| 4 | **权限远程审批** | Claude Pilot, Happy | 核心痛点：远离电脑时审批工具调用权限 |
| 5 | **会话管理** | Claude Pilot | 创建、列出、切换会话 |
| 6 | **Telegram Forum Topic** | Claude Pilot | 每个会话独立话题，清晰隔离 |
| 7 | **白名单认证** | Claude Pilot | 安全基础：只允许授权用户 |

### P1 — 体验提升 (亮点功能)

| # | 特性 | 参考来源 | 价值说明 |
|---|------|---------|---------|
| 8 | **会话持久化与恢复** | Claude Pilot, Happy | 长对话断点续接，`--resume` 集成 |
| 9 | **Hook 事件集成** | Claude Pilot | SessionStart/Stop/Notification 生命周期感知 |
| 10 | **Markdown 优雅渲染** | Claude Pilot | 代码块、列表等格式在 TG 中美观展示 |
| 11 | **消息智能分段** | Claude Pilot | 长消息按段落边界拆分，不破坏格式 |
| 12 | **工具调用可视化** | Claude Pilot, Happy | 清晰展示 Claude 每一步在做什么 |
| 13 | **Bypass 模式** | Claude Pilot | 一键关闭权限审批，信任模式快速执行 |
| 14 | **终端会话监控** | Claude Pilot | 电脑跑长任务，手机实时看进度 |

### P2 — 差异化特性

| # | 特性 | 参考来源 | 价值说明 |
|---|------|---------|---------|
| 15 | **多项目管理** | Claude Pilot | 快速切换不同项目目录 |
| 16 | **tmux 消息注入** | Claude Pilot | 向终端 Claude 会话注入消息 |
| 17 | **会话重命名/删除** | Claude Pilot | 会话生命周期完整管理 |
| 18 | **中断控制** | Claude Pilot | 远程中断正在执行的任务 |
| 19 | **会话信息查看** | Claude Pilot | /info 快速查看当前状态 |
| 20 | **错误处理与重试** | Claude Pilot | /retry 重发失败消息 |

### P3 — 远期规划

| # | 特性 | 参考来源 | 价值说明 |
|---|------|---------|---------|
| 21 | **Agent SDK 集成** | Happy, 官方 | 更强大的编程控制能力 |
| 22 | **多 Agent 支持** | Happy | Codex, Gemini 等多模型 |
| 23 | **E2E 加密** | Happy | 代码隐私保护 |
| 24 | **Web UI** | 社区方案 | 浏览器端访问 |
| 25 | **多用户支持** | Happy | 团队共享使用 |

---

## 附录：关键文档链接

| 资源 | 链接 |
|------|------|
| Agent SDK 概览 | https://platform.claude.com/docs/en/agent-sdk/overview |
| Agent SDK 快速开始 | https://platform.claude.com/docs/en/agent-sdk/quickstart |
| CLI Headless 模式 | https://code.claude.com/docs/en/headless |
| Channels (TG/Discord) | https://code.claude.com/docs/en/channels |
| Remote Control | https://code.claude.com/docs/en/remote-control |
| CLI 参考 | https://code.claude.com/docs/en/cli-reference |
| 官方 TG 插件源码 | https://github.com/anthropics/claude-plugins-official/tree/main/external_plugins/telegram |
| 官方 Discord 插件源码 | https://github.com/anthropics/claude-plugins-official/tree/main/external_plugins/discord |
