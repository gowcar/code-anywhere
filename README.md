# Code Anywhere

**在手机 Telegram 里，远程驱动你服务器上的 Claude Code 和 Codex。**

每个项目对应和 bot 私聊里的一个 Thread（也支持话题群组）。你在 Thread 里说话，消息会被送进服务器上那个项目的 CLI 会话；CLI 的回复、状态、截图、权限审批，会实时回到同一个 Thread。电脑上的终端和手机上的 TG 用的是**同一个会话**，随时切换，不丢上下文。

```mermaid
flowchart LR
    phone["📱 Telegram 私聊<br/>Thread = 项目"] <-->|Bot API| bridge["code-anywhere<br/>常驻服务"]
    bridge <-->|tmux 注入 / 读取会话日志| cli1["Claude Code<br/>项目 A"]
    bridge <--> cli2["Codex<br/>项目 B"]
    bridge <--> cli3["…更多项目"]
    laptop["💻 电脑终端<br/>tmux attach"] <--> cli1
```

## 快速开始（5 步）

**1. 准备好这些**
- 一台 Linux 机器（Ubuntu / Debian 最省事，能 `sudo` 装包更好；macOS 也行，但共享浏览器暂不支持）
- 这台机器能拉这个仓库：仓库是私有的，把机器的 SSH 公钥加到有权限的 GitHub 账号（或加成仓库的 Deploy key）
- Telegram 账号，以及 Claude 账号（订阅或 API key）；想用 Codex 再准备 ChatGPT 账号

**2. 建一个新 bot**：Telegram 找 [@BotFather](https://t.me/BotFather) → `/newbot` → 记下 token。再发 `/mybots` → 选这个 bot → Bot Settings → **Threaded Mode** → 打开。
> 一个 bot 同一时间只能被一台机器接收，新机器请用新 bot。

**3. 安装**
```sh
git clone git@github.com:gowcar/code-anywhere.git ~/workspace/code-anywhere
cd ~/workspace/code-anywhere && ./setup
```
全程只在两处需要你：粘贴 bot token，在和 bot 的私聊里随便发一句话（自动识别你的账号，只允许你本人用）；Claude / Codex 没登录时会提示你登录。其余（依赖、服务、MCP、技能、规则）全自动，不会问你“要不要装”，也不会覆盖你已有的配置。可以反复执行，`./setup --check` 只检查不改动。

**4. 开始用**：私聊里会自动出现两个内置 Thread：**code-anywhere**（总体协调）和 **issue-desk**（反馈问题）。新项目发 `/new 项目名`（加 `codex` 用 Codex）。

**5. 想要更多能力，直接在 code-anywhere 的 Thread 里说**，比如“配置 issue 流程”（GitHub 或 GitLab：在 issue-desk 里口头提问题，修好后 `/issues` 点按钮验收）、“用自己的隧道做远程查看”。

它会问你需要的东西，然后帮你配好。

## 日常怎么用

**开始一个项目**：在私聊里发 `/new 项目名`（默认 Claude，`/new 项目名 codex` 用 Codex），立刻出现同名 Thread，进去直接说话。已有的目录也可以：在服务器上进入目录运行一次 `claude` 或 `codex` 说句话，5 分钟内自动出现话题。

**在话题里**：

| 你做的事 | 效果 |
|---|---|
| 直接发文字 | 送进该项目的 CLI 会话，回复实时推回来 |
| 发截图、文件 | 存到项目的 `.attachments/`，CLI 直接能看 |
| 发视频 | 只保存到 `.attachments/videos/`（上限 20MB），不发给模型，回复保存路径 |
| Claude 要执行需审批的操作 | 话题里弹出审批按钮，点一下允许 / 拒绝 |
| CLI 向你提问（单选、多选、表单） | 以 TG 按钮菜单呈现，点选即可 |
| CLI 生成或查看了图片 | 自动上传到话题 |

**命令**：

| 命令 | 作用 |
|---|---|
| `/new 名字` | 在服务器上建项目目录 + 话题并启动 CLI，全程不用登录服务器 |
| `/screen` | 当前项目终端的截图（保留排版和颜色） |
| `/vnc` | 打开共享浏览器的画面：看 Agent 在浏览器里干什么，或提前登录网站（所有项目共用登录状态） |
| `/abort` | 中断正在跑的回合，并丢弃已发出但还没处理的排队消息（会把丢弃的原文回给你） |
| `/model` | 查看当前模型；`/model list` 列出全部；`/model claude sonnet high full` 一句切换引擎、模型、effort、权限，历史不丢 |
| `/permissions` | Codex 权限：`full` / `workspace` / `read-only` |
| `/status` | 所有会话的活动状态（进行中、空闲、失败） |
| `/issues` | issue（GitHub / GitLab）按状态汇总，待验证的附验证方法；每条带按钮：✅ 通过 / ↩️ 不通过 / 🗑 放弃（见下文） |
| `/projects` `/sessions` | 项目列表、历史会话 |
| `/trust` `/whoami` | 项目信任开关、你的 TG ID |

**在电脑上**：`t` 打开 tmux 会话选择器，进入任意项目的会话，和手机上看到的是同一个。

## 它为什么稳

作者日常重度使用了几个月，下面这些都是踩过坑后的设计：

- **服务挂了会自己起来**：systemd / launchd 常驻，开机自启，异常退出 5 秒内重启。
- **重启不丢消息**：每个会话转发到哪里都记在磁盘上，服务重启后从断点补发。
- **只认真实的 CLI 会话**：按 tmux 进程和会话日志定位，不会把消息投错项目。
- **新目录首次启动不吞消息**：自动处理 Claude / Codex 的“信任此目录”确认框。
- **私聊 Thread 模式（推荐）**：工具调用逐条出现、几秒后消散；长时间思考时每分钟冒一条会消散的“仍在运行”心跳；输入框下方常驻 [📸 看终端] [⏹ 中断] 两个快捷按钮，只作用于当前 Thread。
- **一眼看出在不在干活**：话题名前的 🟢（干活）🟡（等你）🔴（重试或疑似卡住）；干活时底部有一张状态卡，转圈、用时、token、最近 4 个动作实时滚动，自带 [⏹ 中断] [📸 看终端] 按钮；5 分钟没有真实进展会主动提醒。
- **失败看得见**：Codex 的模型限流、网关报错等失败会推到话题里，不会默默卡住。
- **防失忆**：每个项目自动维护 `recon/PROGRESS.md`，会话压缩或重开后先读它接续进度。

## 进阶

### 会话之间互相协作

内置 `local-sessions` MCP（Claude 和 Codex 都已注册），让一个项目的 Agent 能给另一个项目的 Agent 派活、查进度。约定：消息只写一句话，复杂内容放进 md 文件、消息里给路径。详见 [docs/session-mcp.md](docs/session-mcp.md)。

### 共享浏览器：Agent 用浏览器，需要登录时你在手机上接管

所有会话共用一个浏览器池（默认 2 个槽位，有界面，跑在虚拟屏幕里），通过 `browser` MCP 提供官方 Playwright MCP 的全部能力。每个会话第一次用浏览器时自动分到一个独占槽位，空闲 10 分钟自动归还；所有项目共用一个登录身份。

遇到登录、SSO、二次验证、验证码时，Agent 调用 `request_browser_help`，你在它的 Thread 里收到 **[🖥 打开浏览器] [✅ 我弄好了]**：点开就在手机上通过 noVNC 直接操作同一个浏览器，密码只由你自己输入，模型看不到。你也可以随时发 `/vnc` 打开画面。

远程画面默认经 **Cloudflare 免费临时隧道**送到手机（`https://….trycloudflare.com`，不用注册、不用配置），用完即关、地址作废，画面还有一次性密码。想走自己的隧道（中转机或公网地址），在 code-anywhere 的 Thread 里说一声即可（Agent 手册：`docs/REMOTE-VIEW-SETUP.md`）。目前只支持 Linux。

### 用 issue 管理反馈（GitHub / GitLab）

内置的 **issue-desk** Thread 专门收问题。第一次用前，在 code-anywhere 的 Thread 里说“配置 issue 流程”，它会问你用 GitHub 还是 GitLab、接哪些仓库，然后帮你配好：

```mermaid
flowchart LR
    you["你在 issue-desk 说<br/>“App 聊天界面滚动有问题”"] --> desk["issue-desk<br/>判断仓库、整理、附截图"]
    desk -->|gh / glab| gl[("issue<br/>agent:todo")]
    desk -->|一句话通知| dev["对应项目会话"]
    dev -->|修复、评论、验证方法| gl2[("agent:to-verify")]
    gl2 -->|/issues 查看| you2["你验证后在 issue-desk 说<br/>“#1 验证通过”→ 关闭"]
```

- issue-desk 维护一张**别名路由表**（“App”→ App 仓库，“服务端”→ 后端仓库……），想加仓库或改说法，在 code-anywhere 里说一声。
- 状态：`agent:todo` → `agent:doing` → `agent:to-verify` → 你验证后关闭；`agent:blocked` 表示需要你决定。
- 在 TG 发 `/issues` 就能处理：点 **✅ 通过** 直接关闭；点 **↩️ 不通过** 退回待处理，并可直接回复原因（会写进 issue、通知负责会话）；不想做了点 **🗑 放弃**（二次确认）。负责会话按项目的 git 远端自动找到。
- 截图会附到 issue 里（GitHub 存在仓库的 `issue-assets` 分支），同时在本机留原件，项目会话用 `issue-images <仓库> <编号>` 直接取。

## 常见问题

- **一个 bot 能给两台机器用吗？** 不能，同一时间只能被一台机器接收。换机器就新建 bot，或先停掉旧机器的服务。
- **服务怎么重启 / 看日志？** `systemctl --user restart code-anywhere`，日志在 `logs/code-anywhere.log`。不要用 `./code-anywhere.sh start`，会起第二个实例抢消息。
- **换了 Node 版本后 CLI 起不来？** 重跑 `./setup`，它会按新位置重写服务的 PATH。

## 开发

```sh
.venv/bin/python -m unittest discover -s tests -q
```

代码结构：`src/bot.py`（TG 入口与命令）、`src/api_server.py`（会话日志监听与转发）、`src/backends.py`（Claude / Codex 启动与识别）、`hooks/`（Claude hooks）、`provision/`（安装程序）、`tools/`（`t`、`tmux-tui` 等个人工具）。项目进度与设计决策见 `recon/PROGRESS.md`。
