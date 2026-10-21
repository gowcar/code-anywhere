# 2026-09-26 模型认证失败与 Telegram 错误漏报

## 结论与边界

本轮仅诊断。服务器、隧道、SSH、bot 主进程和 Telegram polling 均存活。上午两个 Codex 会话遇到上游 401；桥接程序忽略完成事件内的 error，造成手机端没有失败提示。当前独立模型调用已成功，但尚未修复漏报逻辑，也未重放用户之前的任务。

## 实测证据（北京时间）

- 以 SSE 用户、现有 Mac 公钥经香港跳板成功登录。bot 为 user systemd 服务，自 9-25 13:33:41 运行，NRestarts=0。
- 9-26 19:24 前后 getUpdates 每约 10 秒成功返回，health=ok，4 个原有会话仍注册。
- Telegram 在 07:16:13、07:16:26 收到 seatalk 两条输入，07:19:26、07:21:22 收到 code-anywhere 两条输入；投递日志分别记录相应终端 pane。第二条 seatalk 输入的完整消费状态未独立确定，不能把 4 条输入都当作已完成调用。
- 香港源日志对应 UTC 9-25 23:16–23:21（不是 UTC 9-26）：共 18 次 Codex 上游 401，code=invalid_api_key/type=invalid_request_error。已识别租户 zx、provider=openai-codex，说明家庭入口钥匙验证成功；拒绝来自上游，不是 SSH 或本地租户钥匙缺失。
- 新节点两个 Codex 进程使用的入口钥匙摘要相同，匹配香港有效的 zx 注册项；配置入口仍为 hk.tckb.io/v1，无进程级 HTTP/SOCKS 代理，域名解析到实际香港网关。
- 两个终端保留 Incorrect API key provided / 401。原始 rollout 的 task_complete 含 error={message,codex_error_info}，last_agent_message 为空。
- src/backends.py 的 normalize_codex 对 task_complete/task_completed/turn_aborted 直接返回 result，丢弃 error；只处理独立的 error 事件。watcher 在 result 时停止 typing，但没有失败文字，因此 Telegram 用户看到无回复。
- 上游拒绝的具体触发原因（令牌到期、撤销、服务端暂态等）未证实，不据错误文案推断密钥永久失效。

## 当前恢复验证

- 香港日志在 19:05 后已有同通道成功调用；这只是首次观测到成功，不能反推出精确恢复时刻。
- 使用新节点正在运行的 Codex 可执行文件和进程环境，以 gpt-6-sol 在临时目录执行独立 ephemeral/read-only 请求，不接 Telegram、不恢复旧业务会话。要求不使用工具，仅回复 OK。
- 3.58 秒、exit=0；事件 thread.started/turn.started/item.completed/turn.completed，实际回复 OK。临时目录自动清理。
- 无服务重启、无业务源码或认证配置修改、无历史消息补发。未验证用户重新发送消息后的完整 TG 往返，不能把独立模型调用等同于全链路验收。

## 后续修复方向

为完成事件中的 error 保留失败状态并转发可读且脱敏的错误提示；保证仍停止 typing、避免重复通知。补针对真实 task_complete.error 结构的回归，再按既有 systemd 管理方式部署。不要通过更换用户钥匙或重启所有终端来掩盖漏报。

## 2026-09-26 19:40 seatalk 复核
TG206仍绑定原Codex会话01a0d2dc-d4f9-7e61-a26b-3ac935efdaca、pane %33。07:16:30 task_complete携401错误，回合已失败结束。用户19:40:25新发“看看？”，19:40:27成功注入并产生task_started，约19:40:36终端已输出将核对浏览器、同步日志、线上状态的回复。当前恢复执行，不能据此宣称上游认证或TG错误漏报已修复；本次只读检查，无重启、无代发。

## 2026-10-15 capacity漏报复核
已确认2026-10-14 22:09:22 SGT digital-buddy-seatalk的task_complete.error=server_overloaded仍被normalize_codex丢弃；真实事件重放只返回result/subtype。/status一律绿色亦掩盖上一轮失败。诊断/优化设计 `docs/CODEX-FAILURE-VISIBILITY-20261015.md`，本轮仅检查及方案，未改运行代码、未自动重试。

### 2026-10-15 第一阶段修复部署
用户ok授权后实现src/turn_status.py：保留并脱敏task_complete.error；每轮持久化状态/通知回执，TG永久失败提示，失败发送30秒重试，历史启动不批量补发。/status和local-sessions反馈增加failed/retrying等状态，保留终端安全投递status。96项回归（真实错误结构/通知去重/重试成功/迟到事件/重启回执/脱敏/失败重试/状态展示）通过；systemd已部署，health=ok、7个终端，真实列表读取成功。未制造上游错误或给业务会话发送测试通知，下一次自然失败的实机TG回执仍待观察。仅实现状态可见性，无自动重跑/换模型/按钮/静默超时提醒。
