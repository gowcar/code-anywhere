# Codex 失败状态对 TG 可见（第一阶段实现）

## 实证
- 2026-10-14 22:09:22 SGT，digital-buddy-seatalk sid01a13a32-604f-71a1-9aa8-f546310a1683 的 task_complete.error 为 server_overloaded，文案 Selected model is at capacity. Please try a different model.，last_agent_message=null。
- 同日17:29:52 digital-buddy、17:30:19 digital-buddy-ios也有同结构失败。分叉rollout会复制事件，统计应按原生session/turn去重，不按文件行数当次数。
- 将真实事件输入当前normalize_codex，仅得到type=result/subtype=task_complete；error、turn_id、时间均丢失。watcher结果分支只stop_typing，没有失败通知。
- /status 当前对注册会话一律显示绿色；local-sessions反馈侧主要由终端提示启发式判断busy/idle，不能稳定说明上一轮失败。
- 是本轮模型调用失败结束，不等于CLI退出，也不能据此断言其后台工具/子进程已全部停止。capacity文案描述上游模型容量，不是本机内存不足证据。
- 本轮检查时三个digital-buddy相关会话均Working，不能把历史失败当成它们当前仍失败。

## 建议第一阶段
1. 保留turn_id、失败类型和脱敏错误类别，将完成时携error的事件转换为明确failed状态；同时覆盖独立error、正常完成、中断、明确自动重试。重试中的临时错误不能冒充最终失败。
2. 每次最终失败给对应topic一条永久通知：项目/时间/原因/本轮已失败、等待重试；停止typing。同轮独立error与task_complete.error去重，不能用回复正文相似度去重。原文不直接外发，防止API key/URL凭据泄漏。
3. /status与local-sessions反馈共用状态：进行中、重试中、空闲、上一轮失败、已断开、未知；附最后事件时间/原因。按原生session+turn关联，旧轮迟到事件不能覆盖新轮运行状态。
4. 持久化最近状态与通知回执，重启不重放旧用户消息；启动只核对当前会话最后状态，不扫描历史批量补发错误。TG发送失败与模型失败分开记录。

## 后续可选
- “查看状态”“继续尝试”按钮：后者需要验证当前会话和失败轮次仍匹配、目标空闲，点一次只投递一次；原生接收确认后才显示已提交。不得盲目重放旧长消息/工具命令，不自动换模型。
- 超过约3分钟无新事件可提示一次“暂未观察到新进展，最后活动时间…”，不能仅凭静默宣称卡死；长工具和后台任务可能正常运行。
- 不默认循环重试；capacity恢复不确定，先解决可观测性与用户明确控制。

## 回归验收
真实task_complete.error样本、独立error、双事件去重、自动重试后成功、正常/用户中断不误报、失败后新轮恢复、迟到事件、服务重启、通知失败与凭据脱敏。TG用隔离测试验证topic与文本，不给业务会话制造真实容量错误。

## 实现记录
已实现src/turn_status.py（脱敏分类、SQLite状态及通知回执、启动最近2MiB回读不补发历史），normalize_codex保留失败信息，watcher每5秒检查待通知失败，TG失败后30秒重试。/status与MCP反馈通过原生回合状态和终端状态综合展示；显式忙碌/审批提示优先于旧完成记录。无will_retry标记的独立error先保留待定状态，等待结束事件，避免把临时错误冒充最终失败。
通知回执只能在TG确认成功后保存；若TG已收但网络返回丢失或本机在落盘前崩溃，重试可能重复，不能保证跨网络故障的严格exactly-once。历史回读窗口内若无生命周期事件且无持久记录，依靠终端提示或显示unknown，不能推断任务成功。旧失败时启动的后台进程是否还在运行不由失败通知保证。
本阶段不增加自动重跑、换模型、重试按钮或静默超时提醒。

验收：96项测试通过，部署后health=ok/7终端，实际会话列表区分running/waiting_input/unknown，历史idle状态正常恢复。真实TG失败通知等待自然故障触发，未注入业务测试。
