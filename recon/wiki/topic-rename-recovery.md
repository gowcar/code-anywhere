# 2026-10-12 digital-buddy 目录改名导致话题误删

## 证据与原因
- 原TG话题21059绑定digital-buddy/digital-buddy-supervisor。
- 原Codex会话01a12a7d-4d15-7721-adb7-d4d00945779e在10:25完成monorepo重组，把目录改为digital-buddy/supervisor。
- sync_all_projects每5分钟执行：旧路径不存在即delete_forum_topic。10:26:26.967 Telegram返回True，话题及TG消息消失。这是破坏性的错误清理策略，非用户手工删除或Codex清空历史。
- 原生rollout约5.2MB仍存在且原进程%39仍在。原话题未原样恢复。

## 修复
- 移除定时同步的delete_forum_topic及用unpin_all_forum_topic_messages探测存在的逻辑，目录消失只警告并保留绑定。保留用户置顶，不以目录状态删除历史。
- codex_transcript在原metadata cwd已消失时，仅以实际持有rollout的进程/proc/PID/cwd确认改名后的目录，仍拒绝无关项目和subagent；不猜最新日志。
- 新增2项回归：路径消失不删话题/不取消置顶/不清理绑定；改名只认实际进程cwd且原目录存在不放宽。全量71测试通过。

## 恢复
- 先备份原生history、10/11和10/12桥接日志、旧bindings，目录logs/recovery-digital-buddy-20261012，文件0600目录0700，gitignore排除。
- 从两日日志按原TG消息ID恢复103条文字记录，telegram-history.md；另导出68条原生公开消息native-public-history.md，原始jsonl另存。
- 新建同名TG话题21681；恢复说明message21682；历史Markdown附件message21683。只作为文件查阅，不自动回放任务。不能保证媒体或日志未记录内容完整。
- 新路径设codex/full并保存原sid；systemd重启部署，active API确认topic21681→%39→原sid，gpt-5.6-sol/full。
- 本次未重启Codex、未改写原始历史、未注入业务消息。旧TG消息ID未恢复。
