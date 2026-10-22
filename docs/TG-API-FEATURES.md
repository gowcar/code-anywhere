# Telegram Bot API 新特性评估（2026-10-22，为切换私聊话题模式做准备）

范围：Bot API 9.0（2025-04）～ 10.3（2026-08）。只列对 code-anywhere 有用的；按价值排序。
本机库：python-telegram-bot 22.8（封装到 Bot API 10.0）；10.1～10.3 的新方法可用 `bot.do_api_request(...)` 或各方法的 `api_kwargs` 直接调用，不必等库升级。

## 一、直接解决我们已知的问题

| 优先级 | 特性 | 版本 | 用在哪 / 解决什么 |
|---|---|---|---|
| ★★★ | **私聊话题**：`User.has_topics_enabled`、私聊 `createForumTopic`（9.4）、`message_thread_id` 收发、`editForumTopic` / `deleteForumTopic` | 9.3 / 9.4 | 切换私聊模式的基础。额度约为群组 3 倍，不用建群、设管理员 |
| ★★★ | **`sendMessageDraft` 流式草稿** | 9.3，9.5 起所有 bot 可用，10.0 允许空文本 | ① 回复边生成边显示；② **底部状态卡改用草稿气泡**：天然在最底部、实时更新，且不走 `editMessageText` 的额度——正是“状态卡固定在底部、内容不停变化”的理想形态 |
| ★★★ | **草稿的停止按钮**：`sendMessageDraft` / `sendRichMessageDraft` 的 `can_stop`、`keep_on_stop`，以及 `Update.stopped_message_generation`（`MessageGenerationStopped`） | 10.3 | 用户点 Telegram 原生的“停止生成”，bot 收到更新 → 直接映射为 `/abort`。比我们自己画的 [⏹ 中断] 按钮更自然 |
| ★★★ | **富文本消息** `sendRichMessage` / `sendRichMessageDraft`；`editMessageText` 的 `rich_message` | 10.1 | 标题、列表、表格、代码块、引用、**可折叠块 `RichBlockDetails`**、**思考块 `RichBlockThinking`**。长工具输出折叠、模型思考单独呈现、`/status` 用表格 |
| ★★★ | **富文本内按钮** `RichBlockButtons` / `RichMessageButton`；紧凑表格 `is_compact`；可展开引用 `RichBlockExpandableBlockQuotation`；文档块 `RichBlockDocument` | 10.3 | **按钮可以放在每条内容旁边**——`/issues` 每个 issue 自带按钮，不再需要“列表 + 详情”的折中 |
| ★★ | **按钮颜色与图标**：`InlineKeyboardButton.style`、`icon_custom_emoji_id` | 9.4 | 通过 = 绿、放弃 = 红、中断 = 红，一眼区分（库已支持 `style`） |
| ★★ | **禁用按钮** `DisabledButton` / `InlineKeyboardButton.disabled` | 10.3 | 操作进行中把按钮置灰，防重复点击（如中断、通过） |
| ★★ | **`force_reply` 可与内联键盘同用** | 10.3 | “不通过请回复原因”时，同一条消息既有按钮又直接弹出回复框 |

## 二、能提升体验的

| 特性 | 版本 | 用法 |
|---|---|---|
| 消息表情回应 `setMessageReaction`（已有） | — | 收到用户消息先回 👀，完成回 ✅：用户知道“已收到”，又不多发一条消息、不占额度 |
| 自定义 emoji（bot 所有者有 Premium 时可在消息里用） | 9.4 | 状态卡用**动态 emoji** 做转圈动画，比 ◐◓◑◒ 更像动画 |
| `date_time` 实体 | 9.5 | “最近活动 5 分钟前”之类按用户本地时区显示 |
| BotFather 可禁止用户自行建 / 删私聊话题；`User.allows_users_to_create_topics` | 9.4 | 话题由桥接统一管理，避免用户误删导致绑定失效 |
| `ForumTopic.is_name_implicit` | 9.3 | 区分“系统默认名”与“自定义名”，改状态符号时更安全 |
| `message_effect_id` 用于转发 / 复制 | 9.3 | 可选：任务完成时的庆祝效果（低优先级） |

## 三、可用于产品化 / 推广

| 特性 | 版本 | 用法 |
|---|---|---|
| **`t.me/newbot/...` 链接**、托管 bot（`KeyboardButtonRequestManagedBot`、`getManagedBotToken`） | 9.6 | `./setup` 可以给出一个预填好的“新建 bot”链接，甚至由一个总控 bot 帮新用户创建 bot、自动取 token，省掉手动找 @BotFather 的步骤 |
| bot 之间按用户名互发消息（双方开启） | 10.0 | 多台机器的桥接 bot 互通的可能性（暂无需求） |

## 四、与我们无关（略）

商业账号、礼物、星币、故事、频道私信与建议帖、投票、实况照片、社群、群组内“仅一人可见”的临时消息（10.2，群专用）、Mini App 存储等。

## 建议的实施顺序（私聊模式迁移时）

1. 私聊话题基础（建话题、路由、改名）。
2. 状态卡改为 `sendMessageDraft` 草稿气泡 + `can_stop` 原生停止 → `/abort`。
3. 助手回复流式输出（草稿流 → 结束时正式消息）。
4. 按钮加颜色、操作中置灰；收到消息用 👀 回应代替回执消息。
5. 富文本：`/issues` 每项自带按钮、`/status` 表格、长工具输出折叠、思考块。

注意：Bot API 各版本说明里**没有公布新的频率限制**；私聊约 1 条/秒、群约 20 条/分钟仍是经验值。草稿流本身也要节流（社区建议自行限频）。

来源：[Bot API changelog](https://core.telegram.org/bots/api-changelog)、本机 `python-telegram-bot` 22.8 实测（`send_message_draft`、`InlineKeyboardButton.style`、`set_message_reaction` 可用；`send_rich_message` 未封装，需 `do_api_request`）。
