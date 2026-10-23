# 共享浏览器已上线（2026-10-23）

code-anywhere 新增了所有会话共用的浏览器池。以后需要浏览器时（打开网页、查 SPACE/SDP/SWP/GitLab 等内部平台、验证页面、登录后取数据），**优先用它，不要自己另外启动浏览器**。

## 怎么用

- **browser MCP**：官方 Playwright MCP 的全部工具（`browser_navigate`、`browser_snapshot`、`browser_click`、`browser_type`、`browser_take_screenshot`、`browser_evaluate`、`browser_tabs`、`browser_pdf_save` 等）。第一次调用时自动分到一个独占槽位，空闲 10 分钟后自动归还，下次调用自动重连。
- **所有项目共用一个登录身份**（cookie 和 localStorage 都会同步）。用户已经登录过 SPACE，先打开页面看看是否已登录，再决定要不要请用户登录。
- **需要用户操作时**（登录、SSO、二次验证、验证码、扫码）：先用浏览器打开对应页面，再调用 local-sessions MCP 的 `request_browser_help(reason)`，reason 用一句中文写清楚。用户会在你的 TG Thread 里收到按钮，在手机上接管同一个浏览器；做完或 15 分钟超时后，你会收到 code-anywhere 发来的消息，也可以用 `browser_help_status(help_id)` 查询。**绝不要**让用户在聊天里发密码或验证码。
- **限制**：最多 2 个槽位同时使用（硬上限 4），每个槽位最多 6 个标签页；两个都被占用时会提示稍后重试。
- 不要在共享浏览器里退出登录、清 cookie、改设置，也不要关闭共享浏览器本身。

详细说明见技能 `shared-browser`（Claude：`~/.claude/skills/shared-browser/SKILL.md`；Codex：`~/.codex/skills/shared-browser/SKILL.md`），全局规则里也新增了「共享浏览器」一节。

## 已经在跑的会话

browser MCP、`request_browser_help` 这两个新工具，以及技能，**要等会话重启后才会加载**。当前会话请**不要**自己重启。先照旧工作；遇到需要浏览器的任务时，告诉用户“需要重启本会话才能用上共享浏览器”，由用户决定什么时候重启。
