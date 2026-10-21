# 终端用户长消息在 TG 被截断（2026-09-24）

## 已证实

用户截图 `.attachments/20260924_141042_photo_AQADqhFrG7auoFV-.jpg` 中 apex 的 👤 气泡结束于 `badgeC`。原生 Codex session `01a0ca50-7988-7302-8026-b7c9e004563a` 的 2026-09-24T06:09:17.227Z UserMessage（本地 14:09:17）完整长度 888 字符，开头「请接手 SeaTalk 1.0.0-rc.3」，第 500 字符处正好 `badgeC`，之后还有 388 字符（worker/API 要求、验收范围、Manifest SHA256 和排 todo 要求）。

`src/api_server.py` watcher 真人 user echo 直接 `html_lib.escape(text[:500])`，发到 TG topic 202。14:09:18 sendMessage 请求与回执 message_id 1343 都是 502 字符（👤+空格+500 原文），与截图相同。不是 tmux/Codex 输入缺失，不是 TG 客户端折叠，而是桥接代码主动截断且无提示；原生会话保有完整任务。本次仅诊断，未改代码、未补发。后续修复应将 user echo 完整分段发送，保留来源去重，避免转义后长度超限。

## 2026-09-24 用户决策与修复

用户要求 TG 与 PC 同等展示，批准优化。`git log -S "text[:500]" -- src/api_server.py` 追溯至初始提交 `2976973`，当时已是 send_message 的 500 字回显；没有找到解释该阈值的注释或决策依据，不能声称为平台限制或必要设计。

新增 `renderer.split_user_message`：完整原文顺序切块，每块含 👤 前缀按 UTF-16 单元保守预算 4000，保留全部空白/换行/字面 Markdown；watcher 以纯文本发送，避免 HTML 转义膨胀或标签解析影响。Claude/Codex 共用真人 echo 路径，既有 TG 来源消重及事件去重保留。新增 3 项测试，含 888 字完整、emoji/边界/多段还原、实际 watcher 顺序与消重；全量 39 项通过。systemd 已重启上线；历史消息未自动补发。
