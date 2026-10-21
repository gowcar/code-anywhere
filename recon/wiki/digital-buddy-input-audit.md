# 2026-10-13 Digital Buddy 重复输入核查

核对本机保留的TG getUpdates回执（按update_id/message_id）与原生response_item user记录，而非把UserMessage与response_item两个表示重复计数。

当前Digital Buddy sid01a12f36/topic22143：4条实际TG用户文字、无重复update回执，每条各一次进入原生会话。包括10/12 17:06的确认、10/13 01:23仓库地址及“你试试”、09:21 CID/Config Center问题。仓库地址及“你试试”在01:23:25相邻接收，属于两条不同消息。
原生9条外部输入（终端/TG/Agent）无相同正文重复；另5条codex_internal_context goal自动继续提示，不是TG再次输入。旧supervisor sid01a12a7d的26条外部输入、digital-buddy-seatalk sid01a12f47的106条外部输入也未检出相同正文重复。仅覆盖现存本地记录，不能断言所有历史绝无丢失或重复。

from agent原因为session_mcp.send_message及agent_sessions.send_message的sender默认agent，由调用者自行填报。用户展示的过夜授权为来自对等Agent的MCP消息，原生只出现一次，TG也只echo一次（23452）；属于协调转述，非TG同消息重复投递。

## 2026-10-13 四条回复逐条对照
用户 GLOBAL / 平台化 Build/Run Config / Config Center 问题只有一次原生输入（01:21:39 UTC）。四条独立 commentary 分别在新加坡09:22:04、09:23:20、09:24:30、09:25:03产生，对应TG23878、23880、23901、23907，各转发一次。09:23:10有明确compacted事件；压缩后再次确认并重复解释。重复回答发生在原生Agent输出层，未发现桥接重投。后两条混有新进展/回读证据，不宜按近似文本丢弃。不能仅凭压缩时间关联证明四条全部由压缩造成。

## 来源名称修复交付
2026-10-13：src/session_mcp.py 按CLI祖先进程PID/start ticks识别source_session_id，忽略模型自填sender；src/agent_sessions.py从活会话生成项目显示名，多实例追加引擎/窗口。旧stdio不热更新，保留旧sender及三项fingerprint兼容；默认agent在TG标为来源未识别（旧客户端），不伪造归属。客户端重新连接后启用自动识别，未强行重启业务CLI。
真实列表发现dead pane %13保留复用的pts/2，把digital-buddy同一PID误列两次；discover已跳过pane_dead，部署后仅%39。此现象没有证据导致四次回答。
83项测试通过（含来源、旧回执、dead pane回归）；真实stdio list_sessions通过，实际调用进程识别code-anywhere %46；systemd重启后health正常/6终端监听。未向业务会话发送测试消息。模型侧重复commentary未通过桥接过滤或修改其他项目指令来处理。

## 2026-10-13 10:38截图：seatalk-workspace同类现象
截图20261013_103835：10:29收到了有效dump；10:32–10:34连续核查更新；10:36再次“收到，dump和日志都已上传”；10:37报告新证据。原生rollout 2026/09/24/rollout-2026-09-24T18-01-23-01a0d2dc-d4f9-7e61-a26b-3ac935efdaca.jsonl确认：10:29:02用户“好了”后没有新的user消息，10:36:35.484明确compacted，10:36:45.367重新生成收到，随后10:37:02新进展。该次重复确认在压缩后原生输出中已存在；中间进展不是重复输入。图片内容已文字沉淀。

## 2026-10-13 17:45 来源未识别截图核查
截图消息来自seatalk-workspace→apex，request_id=seatalk-apex-mask-coordination-20261013-v1。源rollout 01a0d2dc在17:44:17调用local-sessions.send_message未带sender；持久回执sender=agent/source_session_id=null，17:44:21原生received。源MCP PID112802/父Codex112580自10月12日16:25:46运行，未重连，仍持有旧实现。不是新识别算法匹配失败，而是旧客户端没有上报源身份。未重启业务会话、未重发消息。此次截图正文已文字记录；新功能尚未覆盖长期存活MCP客户端。

## 2026-10-13 compact恢复机制核查
只读检查seatalk-workspace/digital-buddy根AGENTS.md、全局Codex配置及本仓库Claude SessionStart hook。两项目AGENTS无compact后重复回答规定；Codex配置未见自定义compact/instruction/hook字段；code-anywhere的SessionStart配置在Claude settings中，内容为进度/索引注入，不要求重答，不能作为这两个Codex样本的触发原因。code-anywhere自身PROGRESS旧启动协议有“复述当前分支并等确认”，但不在这两个项目AGENTS里，不可泛化为本次原因。
关键实证：seatalk 10:36:35 compacted.replacement_history为388 user+3 developer、0 assistant；digital-buddy09:23:10为11 user+3 developer、0 assistant；两者retained_context.verified_answers均为空。不能据此断言模型完全不知道已回答，因为摘要仍包含工作事实；但逐条assistant回复（包括commentary）未保留在replacement_history，沟通状态需依赖摘要。digital-buddy摘要Config Center answer未明确标记此前已告知；seatalk摘要反而明确下一步应报告新符号证据，但恢复后仍先重复“收到”。因此当前证据更支持压缩后沟通状态丢失/恢复开场习惯，而非TG重投或本地hook要求重答。仅验证两个样本，不宣称每次compact必现。
建议后续在作用域明确的AGENTS加入恢复纪律：compact继续同一任务，不重复收到/复述/已答问题；只报新发现/阻塞/结果；交接摘要单列“已向用户说明”“待回复”“下一动作”。本轮仅诊断，未改业务项目AGENTS或运行会话。官方文档搜索/打开无有效返回，本结论依本机记录。

## 2026-10-14 seatalk→buddy来源标记复核
用户怀疑digital-buddy-seatalk给digital-buddy消息无from。最近四次received：19:27:21、19:29:58、19:31:58、19:32:34原生user均带[Agent message ... from digital-buddy-seatalk]。TG发送成功回执topic22143/message32241、32250、32270、32275正文均以⇢ from digital-buddy-seatalk开头。紧接的digital-buddy自己的回复32243/32252/32272/32277以✦开头、不带from，符合当前来源区分。尚无用户具体截图定位，因此不认定所有路径无问题。

## 2026-10-15 digital-buddy-seatalk compact 后重答旧 KMS 问题
原生 rollout `01a13a32-604f-71a1-9aa8-f546310a1683` 显示：用户在 18:23 SGT 发 KMS 邮件截图；Agent 于 18:24、18:39、18:41、18:44 已多次解释。18:39:17 输出 A 线交接 final 后立即发生 `compacted`，直到 18:58:44 都没有新的 user message；恢复后 18:39:23 又从“核对这张邮件截图”开始，18:58:44 再次回答 18:23 的问题。TG 未重投，桥接只转发原生新输出。

compact 摘要把已答的 KMS 邮件作为独立背景章节保留，并写明用户已接受频繁发布期间邮件；但没有显式标注“已向用户说明、勿重复”。恢复模型把该背景误当成仍需处理的事项，同时继续了真实 A 线工作。因此不适合在 TG 层按相似度拦截：这段输出混有真实新进展，过滤会误伤。

用户决定采用提示词纠正。已在 workspace 共享 `AGENTS.md` 增加 Codex compact 恢复纪律：没有新 user message 时不得重答旧题；摘要区分 closed/pending/current/next；不确定时查原生输出或 PROGRESS；仍允许持续任务的新进展。当前证据来自 Codex，不能据此断言 Claude 不会发生同类问题，也不能保证提示词绝对消除模型偏差。
