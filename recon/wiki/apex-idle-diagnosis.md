# Apex 停滞现场（2026-09-24 14:24）

- `%31` / sid `01a0ca50-7988-7302-8026-b7c9e004563a`：最后 task_complete 为 14:10:55，完成 TODO 与 rc.3 69 文件核验，未集成或发布。
- TG 最终回复回执 message 1350 / topic 202。Bot active、polling 正常、4 个 watcher 已恢复。
- 用户 14:14「那你继续一口气干完吧，加油」与 14:22「??」日志都显示 injected pane=%31，但原生日志未新增 UserMessage/task_started。注入成功不等于 CLI 已接受，具体未提交原因尚未证实。
- tmux 底部 Queued follow-up inputs / 2 questions 是旧的待答问题，不是这两条 TG 消息。用 Shift-Left 查看：①真实建群邀请的测试对象/是否延后人工验收；②Runner Anthropic 官方凭证 401，需有效凭证环境变量名/端点/模型等非敏感配置。不能把待答 UI 单独当作注入失败根因。
- 已用 Alt-Down 两次退回原主输入框；未回答/跳过问题、未重发任务、未重启 Codex，原业务未改。

## 14:32 复核

原生会话于 14:25:46 新增 task_started 和完整 UserMessage「那你继续一口气干完吧，加油」，没有新的 TG 注入日志；截至 14:32 终端 Working 6m，已执行 npm build、Playwright（1 passed）和 runtime browser-live（success/reloaded true、browser_errors 空）。两条 Queued follow-up questions 仍在，故不能断言它们阻塞执行。14:14 的输入至 14:25 才进入模型，具体恢复触发无法从当前记录还原（本会话只做过问题面板导航，没有发送 Enter 或重放）。确定的桥接缺口：`_inject_tmux` 只验证 send-keys/paste-buffer 命令退出成功就记录 injected，未核验 UserMessage/task_started；因此无法发现终端未接受输入的状态。未证实具体是焦点、TUI 队列还是其它终端行为。

## 2026-09-28 12:53 TG 输入失败
截图20260928_125828显示首条扫码问题投递失败。服务日志12:53:02接收，12:53:12在_inject_tmux单行send-keys -l阶段等待10秒超时，尚未执行Enter；具体tmux阻塞原因无现场证据。12:53:56重发记录injected pane=%31，但rollout未出现对应UserMessage/task_started。直到12:57:13终端hello、12:57:23扫码问题才成为真实输入并开始任务。第二次仅按键命令成功，不等于CLI接收。模型/权限配置正常，不能归因401。未重放消息、未中断当前业务，也未修改代码；需补输入确认、超时清理与不确定状态提示，避免自动重试造成重复提交。

## 2026-09-28 18:25 seatalk 同类输入未入会话
截图20260928_182716：先问“两个worker都被干了？那要重新扫码登录是吧”，9秒后问原因，回复只答后者，再重发首问。日志：18:25:32.693收到首问，33.050 injected %33；18:25:42.253收到原因问，42.599 injected；18:26:12.957收到重发，13.331 injected。原生rollout只有18:25:48.425原因问、18:26:24.032重发首问，首次首问未见。证明TG接收正常，首次按键成功不代表原生收到；当时agent仍在执行构建发布，未证实是CLI队列覆盖还是输入时机吞字，不应直接断言覆盖。无超时记录，不能等同12:53第一次超时。未重发或中断业务。
