# 私聊话题模式计划（2026-10-22，用户已决定方向，待新 bot 就绪后实施）

## 一句话目标

用一个**新 bot 的私聊话题**作为 code-anywhere 的唯一入口：额度更高、可流式输出、底部状态更自然；现有群与旧 bot 保持不动，切换后留作存档。

## 为什么

| | 群组（现状） | 私聊话题 |
|---|---|---|
| 发送额度 | 约 20 条/分钟，所有话题共用 | 约 1 条/秒（约 60 条/分钟） |
| 流式输出 | 不支持 | `sendMessageDraft`：边生成边显示 |
| 底部状态 | 只能删旧卡重发，最快 45 秒挪一次 | **草稿气泡天然在最底部、实时更新，且不占编辑额度** |
| 权限配置 | 要建群、开话题、设管理员并勾选“管理话题” | 不需要 |
| 话题管理 | 支持 | 支持（建话题、改名、`message_thread_id`、按钮） |

依据：Bot API 9.3 起支持私聊话题与 `sendMessageDraft`，9.5 起所有 bot 可用 `sendMessageDraft`；本机 python-telegram-bot 22.8（Bot API 10.0）已有 `send_message_draft`。

## 用户要做的

1. 在 @BotFather 新建 bot，并为它打开话题（Threaded Mode / Topics）。
2. 把 token 交给 `./setup`（或在新服务器上首次安装时使用）。

## 实施步骤

1. **能力验证（在私聊里逐项实测）**：`getMe.has_topics_enabled`；建话题、`message_thread_id` 收发、改话题名（状态符号）、按钮回调、`sendMessageDraft` 在话题里的显示与收尾、私聊里改名是否产生需删除的系统消息。
2. **桥接支持私聊模式**：`CHAT_ID` 指向用户私聊；绑定、话题创建、`_update_cwd`、消息过滤器在私聊话题下按 `message_thread_id` 路由（现有私聊分支是“无话题单会话”逻辑，需要改）。
3. **状态卡改为草稿气泡**：运行中用 `sendMessageDraft` 在话题底部实时显示（转圈、用时、tokens、最近 4 个动作），频率约 1～2 秒；结束后草稿消失，必要时发一条正式结论。中断 / 看终端按钮仍放在一条普通消息上（草稿不能带按钮时）。
4. **回复流式输出**：助手文本先以草稿流式显示，完成后 `sendMessage` 落为正式消息。
5. **`./setup` 默认私聊模式**：不再引导建群；验证 token → 让用户给 bot 发一句话 → 识别用户 ID 与 `has_topics_enabled`。
6. **迁移**：为每个项目在私聊建话题并重新绑定（会话原生历史与 recon 不受影响）；旧群发一条“已迁移”说明后不再写入。

## 可用的新特性

逐项评估见 [TG-API-FEATURES.md](TG-API-FEATURES.md)：草稿原生“停止生成”（10.3 `can_stop` → `/abort`）、富文本消息内按钮（10.3，`/issues` 每项自带按钮）、按钮颜色 / 置灰、表情回应代替回执等。

## 风险与待确认

- 有项目反馈“私聊里多个话题的消息落到同一会话”，需按 `message_thread_id` 严格路由并实测。
- `sendMessageDraft` 是临时预览，结束时必须发正式消息，否则内容会丢失。
- 草稿是否支持按钮、在话题里的具体呈现，需实测后定稿状态卡设计。

## 参考

- [Bot API changelog](https://core.telegram.org/bots/api-changelog)
- [python-telegram-bot：Bot API 9.3 支持](https://github.com/python-telegram-bot/python-telegram-bot/issues/5077)
- [私聊话题共享会话的问题记录（KiroCrew #4927）](https://github.com/kirodotdev/KiroCrew/issues/4927)
