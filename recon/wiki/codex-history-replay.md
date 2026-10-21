# 切换 Codex 后 TG 重放历史（2026-09-25）

10:57 用户 `/model codex full`，权限切换恢复超过等待窗口；10:57:19 用户输入紧随。`codex_transcript` 当时未找到打开的 rollout，被 `_handle_message` 误判为 codex_first_turn；注入后找到原 sid `01a0d2dc-d4f9-7e61-a26b-3ac935efdaca`，却以 from_start=True 注册 watcher，旧文件从字节0开始发。日志10:57:22起回放昨天用户问答/命令摘要，11:03连过期二维码也转发。用户确认是昨天全部消息重复。不是模型再次执行历史命令，也不是两个活动 Codex 会话互发。

应急重启 bot 停重放，优雅停止在关闭 transport 后挂住，随后仅 SIGKILL bot 主进程完成重启；所有原生终端保留。未批量删除已发消息，未替用户选定/关闭有歧义的会话。当前 topic206 指向 Codex %33，另有 Claude %36，历史 Codex ce3f 未运行。

修复：取消 from_start 参数。未发现打开日志时，在输入前快照所有现存 rollout 的字节长度，注册 watcher 时既有文件从快照位置读，快照后真正新建的文件从0读；正常注册仍从当前末尾读。增加 offset 日志用于复核。涉及 src/backends.py、src/bot.py、src/api_server.py。新增3项回归：恢复旧日志仅发新增、真实新日志保留首条回复、真实 handler 快照在注入之前。全量50项测试通过，systemd重启部署。
