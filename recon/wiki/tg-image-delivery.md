# TG 图片交付（已验证）

### 阶段 2.5：Claude → TG 发图通道 🟢（已上线）

触发场景：runner-coach 截了图想发到 TG 看，但 Claude 自己说"发不了"（Anthropic Messages API output 无 image block）。

落地（`src/api_server.py:_watch_transcript`）：
- 🟢 watcher 在 assistant.tool_use 阶段按白名单（`mcp__Playwright__browser_take_screenshot`/`Read`/`Write`/`MultiEdit`/`NotebookEdit`）抓 `filename` / `file_path` → 拼成绝对路径存 `tool_use_image_paths[tool_use_id]`
- 🟢 看到对应 tool_result（任意 inner，含 text 也行）→ 文件存在则 `send_document` 发原图
- 🟢 退化路径：原图缺失 + tool_result inner 有 image block → `send_photo` 发 base64 缩略图
- 🟢 去重：`sent_image_tool_ids` 集合，每个 tool_use_id 只发一次

关键坑：
- TG `send_photo` 强制压缩长边到 ~1280px 并转 JPEG（长图必糊）→ 原图统一走 `send_document`，客户端仍能 inline 预览图片，但字节无损
- Playwright MCP 的 tool_result 不一定带 image block（行为不稳定）→ 不能依赖 image block 作触发条件，要靠 tool_use 阶段抓 path

效果：runner-coach 让 Claude 截图后 TG 自动出原图，零额外提示词，runner-coach 项目无感知。


## 2026-09-23 Codex 截图未交付（已修复上线）

seatalk-workspace 原 session `01a0ce3f-eeb7-7c52-b090-6c8bdeaf417b` 在 22:15:59 最终回复引用 `.private/workspace-preview/foreground.png`（Markdown image）及 `background.png`（普通 Markdown link），两文件均存在。22:16:00 bot 仅调用 sendMessage，TG 返回 message_id 648 / topic 206；本地路径成为 HTML href，未上传图片。`src/backends.py:normalize_codex` 仅转换文字/命令，ImageView 被丢弃；已有 watcher 原图交付依赖 Claude tool_use/tool_result，无法覆盖 Codex。结论：会话关联正常，缺 Codex 图片交付适配；本次仅诊断，未补发、未改代码。后续应优先支持明确出现在公开回复中的项目内图片附件，避免把内部查看的每张图片都自动外发。

### 修复与验证

新增 `src/image_delivery.py`：mistune AST 提取公开回复的 Markdown image/link（含引用式链接、空格编码），只上传 cwd 内实际存在的图片，解析符号链接后限制项目范围，忽略远程 URL、代码示例与内部 ImageView；同条文本按路径去重。Codex watcher 经现有事件 UUID 去重后调用，原图使用 send_document；逐文件上传失败给出可见提示，成功记录 message_id。Claude 路径不变。当前支持 png/jpg/jpeg/gif/webp；项目外文件、纯文本路径、内部看图不自动发。重启仍跳过旧历史。

`tests/test_image_delivery.py` 新增 5 项，包含 watcher 读取真实事件结构、重复事件只上传一次、内部 ImageView 不发、代码/越界 symlink 排除、原始字节与 topic、失败后继续。全量 36 项通过。systemd 重启后 health=ok、4 个会话恢复。用户明确授权补发后，从原 AgentMessage 提取两图，使用同一生产 helper 上传 topic 206：foreground.png → message 666，background.png → message 667。回执 `logs/codex-image-backfill.json`。

## 2026-09-24 20:07 二维码未交付（ImageView 路径缺口）

seatalk 当前新 sid `01a0d2dc-d4f9-7e61-a26b-3ac935efdaca` 已正确绑定 topic 206。20:04:57 和 20:06:04 为 ImageView，路径 `file:///home/toc/SSE/workspace/seatalk-workspace/.attachments/user1-apex-qr-20260924.png`，文件存在 5105 B。20:06:04 公开回复只说“在这里”，不含 Markdown 图片链接。TG 只收到文字（message 2274），未触发图片上传。上次修复仅处理公开回复中的 Markdown 链接，normalize_codex 仍忽略 ImageView，因而本次漏发；不是 TG 发送失败或 session 失联。需进一步明确图片交付事件/受控附件目录约定，不能把所有内部 ImageView 一律外发。原会话称二维码约 3 分钟有效，20:07 已可能过期；本次只诊断，未补发或申请新码。

## 2026-09-24 ImageView 附件交付修复上线

`normalize_codex` 保留带 ID 的 ImageView 为 image_view 事件；watcher 复用 type+uuid 去重。新增 send_viewed_attachment，仅转发当前项目 `.attachments/` 内的图片，支持 file URI/空格编码，拒绝远程 URL、越界路径、目录 symlink 与逃逸 symlink；原有 Markdown 图片路径保留。42 项测试通过，包含真实事件 watcher 只发一次、路径边界。systemd 重启部署，旧历史不回放。

20:09 曾补发用户提供的 current 二维码（回执 2291）；随后用户明确要求“只负责修好，由那边重新生成”，已遵从，不再补发或生成。后续在项目 `.attachments/` 生成图片并 view_image 即自动转发；不宣称修复后的新二维码已真机验收。

## 2026-09-24 21:42 两图相册实测与入站回显缺陷

用户截图 `.attachments/20260924_214240_photo_AQADtRJrG7auqFV-.jpg` 显示两张不同手机截图并排在同一 TG 相册：左为会话列表、右为 Kong 私聊，caption 为“这是手机端看到的”。seatalk rollout 21:42:01 有两条 UserMessage，分别引用 214142_photo 和 214144_photo：第一条带 caption，第二条默认“看看这个”；21:42:12 两条 ImageView，21:42:44 回复明确分析两张图，证明都已收齐。

同时发现受控 .attachments ImageView 规则仍有来源缺口：入站 TG 图片也下载到同一目录，被看图后回发，日志 21:42:15/16 回执 2600/2601。不是丢图，也不是两张变一张；应补入站来源标记，内部查看用户刚上传的图片不自动回发，生成的新产物照常发。本次仅核对记录，未改代码。

## 2026-09-24 TG 相册合并上线

用户明确要求同一相册多图+说明只成为一条后台输入。`src/bot.py` 以 chat/topic/user/media_group_id 隔离缓冲，最后一条到达后静默 1.2 秒收齐，按 message_id 排序/去重。异步 task 不阻塞 PTB 接收后续图片；收齐后在原项目锁中逐图下载，将全部 @path 引用与原始 caption 合并，一次注入、一次接收回执。任一下载失败不注入半组；单图原逻辑不变。TG 未提供相册结束事件，使用时间窗口，超窗口迟到的附件不能保证仍合组；待发送缓冲为内存，重启期间未投递相册可能需重发。

新增 `tests/test_media_groups.py` 五项（顺序/caption/去重、跨话题隔离、下载失败、单图、鉴权），全量 47 项通过；systemd 已加载，health 正常。未代用户向业务会话补发测试相册，待下一次真实相册验证。入站 ImageView 回显属于另一遗留问题，本次按用户澄清聚焦合并，不宣称已修。

## 2026-09-25 /tmp 公开图片链接修复

13:29 seatalk 的 ImageView 与公开 Markdown 链接均引用 `/tmp/seatalk-live-sync-profile-qr.png`（7859 B、当前用户所有）；旧 helper 仅允许项目内文件，静默跳过，没有上传尝试。修复明确公开链接：允许项目内图片，以及直接引用 /tmp 或 /var/tmp 下当前服务用户拥有的图片；file:// 本地链接也支持。项目内 symlink 不可借临时路径逃逸。缺失/不可读/越界/超过50MiB给TG可见提示并记录原因。其他内部 ImageView 仍仅允许 .attachments，不放宽到任意临时文件。

新增4项测试覆盖临时链接/URI、缺失越界提示、属主与symlink、超限；全量54通过，实际原回复解析已命中二维码路径。systemd已部署，health正常。遵从用户此前“只修复，由那边重新生成”，未补发过期码、未发起新的认证实验。尚未用新二维码实际验证上传；现有普通项目图片/Markdown路径保留。泛用非图片文件不在本次新增支持范围，入站图ImageView回显遗留未修。

## 2026-09-25 16:36 出图慢时间线

截图 `.attachments/20260925_163641_photo_AQADvBFrGwsEsFV-.jpg` 显示16:35声称“下面这张请扫码”但无图，用户16:36要求路径。原生记录：16:33:14用户发码；首轮浏览器30秒等待超时重试；16:35:10 ImageView 指向 `.private/account-data-validation-20260925/first-qr.png`，不在自动ImageView发送目录；16:36:08用户催链接；16:36:16助手称旧码过期并重生成；16:36:21新文件落盘；16:36:38.804公开链接出现；16:36:38.999开始sendDocument；16:36:40.610回执5558。真实TG上传约1.61秒，链接后总计约1.81秒，文件生成到链接约17.6秒。主要延迟来自出码流程重试和“只看图、未公开引用”的交付等待，非TG上传网络慢。不得因此自动外发所有.private图片。未生成/补发任何二维码。

## 2026-10-06 00:39 apex 代码格式路径未触发上传
截图20261006_003943显示回复两次给出/tmp/claude-passkey-display.png。原生00:37:42与00:39:03均使用反引号codespan，不是Markdown link/image。文件存在1016177字节，00:35:11生成；当前解析器仅识别link/image，因此无上传触发。不是文件不存在或上传慢，亦非会话失联。本次仅诊断，未补发登录截图或修改解析规则。

### 公开反引号图片路径修复
解析公开回复codespan中的独立绝对路径、./相对路径、.attachments/路径及file:/// URI，复用原链接的范围/属主/大小检查与同条路径去重。围栏代码及命令示例不识别，内部ImageView范围不变。新增3项回归，全量57项通过。尚不支持任意裸文本路径；跨消息同图仍可重复发送。未补发登录截图。

## 2026-10-06 00:56 Viewed image 未交付复核
apex原生00:56:24.036 ImageView路径file:///tmp/claude-slides-present-main.png，无公开回复引用该路径，无该图上传回执。临时目录内部ImageView仍被send_viewed_attachment排除；反引号公开路径修复不覆盖此事件。用户看到Viewed image但TG无图属当前范围缺口，不是上传延迟。

## 2026-10-14 00:36 digital-buddy-ios安装码
截图20261014_003606：00:31/00:32宣称给码但无图，00:34用户催路径后给text围栏代码块。原生sid01a13543：16:31:11/16:32:08 UTC两次ImageView为项目distribution/live/0.1.0/2610131459/install-qr.png，不在.attachments；16:34:28公开路径放在text围栏，旧解析器只认link/image/codespan，TG仅sendMessage26842/topic25894，无上传。文件10862字节存在。补纯文本围栏中唯一独立路径识别（空/text/plaintext/txt），保留命令/多行/Markdown示例排除及原路径验证。未开放任意内部ImageView。

本次85测试通过，真实00:34公开原文解析命中install-qr.png；systemd已部署且health正常。遵循此前只修复由业务Agent再生成/发图的要求，未补发旧二维码。
