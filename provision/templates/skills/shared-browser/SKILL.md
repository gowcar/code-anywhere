---
name: shared-browser
description: 用 code-anywhere 共享浏览器（browser MCP，Playwright 全部能力）打开网页、操作页面、截图、抓取信息；遇到登录/SSO/二次验证/验证码等需要人的步骤时，用 request_browser_help 在 TG 里请用户一键接管同一个浏览器。凡是需要浏览器的任务（查内部平台 SPACE/SDP/SWP/GitLab 网页、验证网页功能、登录后取数据）都优先用它，不要自己另外启动浏览器。
---

# 共享浏览器（code-anywhere）

本机有一个所有会话共用的浏览器池（2 个槽位，有界面，跑在虚拟屏幕里）。你通过 **browser MCP** 使用它：工具就是官方 Playwright MCP 的全部工具（`browser_navigate`、`browser_snapshot`、`browser_click`、`browser_type`、`browser_take_screenshot`、`browser_evaluate`、`browser_tabs`、`browser_pdf_save`、网络与控制台查看等）。

## 基本用法

1. 直接调用 browser MCP 的工具。第一次调用时会自动给你分配一个槽位，你独占这个槽位，不会和其他会话抢标签页。
2. **所有项目共用一个登录身份**：别的会话登录过的网站，你这里通常也是登录状态。先打开页面看看，不要一上来就请用户登录。
3. 用完不需要手动关闭。空闲 10 分钟后会自动断开、归还槽位，下次调用工具时自动重新连上（页面可能已经变了，先重新 `browser_snapshot`）。
4. 每个槽位最多 6 个标签页，超过会报错：关掉不用的标签页（`browser_tabs` 的 close）再开新的。
5. 两个槽位都被占用时，工具会报“槽位都在使用中”。等一会儿再试，不要自己另外启动浏览器。

## 需要用户操作时（登录、SSO、二次验证、验证码、扫码等）

1. 先用浏览器工具打开需要用户操作的页面。
2. 调用 local-sessions MCP 的 `request_browser_help(reason)`。reason 用一句中文说清楚要用户做什么、做完你接着做什么，例如：“请登录 SPACE（SSO），登录完成后我继续查询发布记录”。
3. 用户会在你所在的 TG Thread 里看到 [🖥 打开浏览器] [✅ 我弄好了]，然后在手机上直接操作同一个浏览器。用户操作期间，你的浏览器调用会暂停等待，这是正常的。
4. 用户点“我弄好了”，或者 15 分钟超时后，你会收到一条 code-anywhere 发来的消息。也可以用 `browser_help_status(help_id)` 查询状态。收到后先 `browser_snapshot` 确认结果，再继续。
5. 超时或用户没弄好时，不要反复请求轰炸。用一句话说明卡在哪里，等用户回复。
6. 用户自己也可以随时在 Thread 里发 `/vnc` 看浏览器画面；如果用户说“我已经在浏览器里登录好了”，直接继续，不必再请求。

## 规矩

- **绝不要**让用户在聊天里发密码或验证码。密码只能由用户在浏览器里自己输入。
- 截图可能拍到敏感信息（令牌、个人信息），只截任务需要的部分，不要把截图贴到 issue 或公开的地方。
- 不要在共享浏览器里退出登录、清 cookie、改浏览器设置，这些会影响所有会话。
- 不要关闭共享浏览器本身。只关你自己打开、已经不用的标签页。
- 返回 `unavailable` 表示这台机器缺远程查看组件，告诉用户在 code-anywhere 的 Thread 里说一声修好（`./setup --only browser`）。
