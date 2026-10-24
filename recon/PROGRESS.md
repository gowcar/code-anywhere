# PROGRESS — code-anywhere 作战地图
> 单一真相源。新 session 第一件事就读它。
## 📍 当前位置
**2026-10-24 自动换新会话 🟡 已上线待首次真实触发**：压缩满 3 次（SESSION_ROTATE_AFTER_COMPACTIONS）→ 一轮结束时发交接消息（写 PROGRESS、回“交接完成：继续/无待办”）→ 下一轮结束 /clear 或 /new，有待办自动续做；事件驱动（Claude compact_boundary/turn_duration，Codex compacted/task_complete），状态 .session-rotation.json，对所有会话生效含 code-anywhere 自己。待：首次真实触发后看日志 [rotation] 验证。另：install.sh 一行安装已写，公开方式待用户定（直接公开 vs 另建干净公开仓库；历史含公司名/内网 IP/nas.im/fatwen 等）。
**2026-10-24 digital-buddy 两个报错 🟢**：19:38 /model Codex→Claude，Claude 首次在该目录弹信任框未处理→restart failed；用户下一条消息被当回车选“退出”→Claude 退出、消息丢失、报未找到日志；19:40 ensure_pane 新开 %75（在 apex tmux 会话里）后正常。已修：model_switch 重启等待中自动接受信任框（3bd…）。遗留：旧 Codex %39 仍在跑（idle）待用户决定是否关；丢失的那条“能看到这个项目的状况吗？”需用户重发。
**2026-10-24 本地 issue 🟢**：没有远端的项目也能走 issue 流程（local-issue，本机 SQLite，/issues 汇总）；有远端就一定用远端——再用 local-issue 时自动迁移没关闭的并要求改用 gh/glab（用户决定：不做定时扫描、不问是否迁移）。资产 src/local_issues.py、tools/local-issue、tests/test_local_issues.py。
**2026-10-24 Alan Mac iOS 环境 🟡 自动进行中**：Xcode 26.3（Universal，Alan 为 2018 Intel MBP 最高 macOS 15，装不了 27.x）已装到 /Applications，许可协议与首次启动组件完成（用户在 Xcode 弹窗输密码），DevToolsSecurity 已开。权限：sshd-keygen-wrapper 屏幕录制/辅助功能/自动化(Safari)/完全磁盘访问已开；sudoers 免密未生效（不再需要）。Alan tmux `ca-setup:ios` 跑 ~/ca-setup-logs/ios-setup.sh：下载 iOS 26.3.1 模拟器（10.47GB，约 1.3MB/s，约 2 小时）→ 编译 ~/workspace/hello-world（XcodeGen 生成的 SwiftUI）→ 模拟器运行截图 hello-sim.png；会话内 cron 6ffc2912 每 :17/:47 检查。屏幕临时 1440×900，原配置 ~/ca-setup-logs/display-original.txt，完成后恢复。远程桌面入口：Cloudflare/lhr 隧道（tmux view 窗口、~/ca-setup-logs/cf-auto.pid）。用户说先不装 code-anywhere。公司证书（dev + 企业分发 p12）用户还在考虑，未导入。Alan 拉代码：只读部署钥匙 ~/.ssh/code_anywhere_deploy + ssh 别名 github-code-anywhere，`git clone github-code-anywhere:gowcar/code-anywhere.git` 已验证可读（10-24）。
**2026-10-24 Alan VPS 只读探测 🟡**：入口 NAS ssh config `Host alan`（alanvps@fatwen.com:30035，经 NAS 跳转，本机无直连钥匙）。macOS 15.7.9 Intel i7-8850H 12 核 16G（约 6G 空闲）、磁盘 428G 空闲、不休眠、GUI 已登录（launchd gui 域可用）、admin 组 sudo 需密码。已有：Homebrew、git、tmux 3.7b（/usr/local/bin，非登录 shell PATH 里没有）、node 26、Claude 2.1.263（已登录 oauth）、Codex 0.153.4；缺 uv、glab、cloudflared；Python 仅 3.9。网络 GitHub/TG/Anthropic/npm/Cloudflare 均通。Alan 已有 9 个近 30 天活跃 Claude 项目、多个 tmux 会话（anypost/carsharing/…），装后会自动建 Thread 并接管。风险：macOS 不支持共享浏览器但 setup 仍会注册 browser MCP 和规则（需先改）；issue-desk 模板为 Shopee 专用（Alan 应跳过 gitlab 步骤）。未改动任何东西。
**2026-10-23 产品化 rollout 准备 🟢**：用户这两天要在另一台电脑 rollout。setup 默认私聊 Thread 模式（引导 Threaded Mode、私聊发一句自动识别）；README 快速开始改 5 步（准备/建 bot/安装/`/new code-anywhere`/在 Thread 里说要什么能力）；远程查看改为默认 Cloudflare 免费临时隧道（用户 10-23 决定，零配置；cloudflared 装在 ~/.local/bin，setup 免 sudo 装 cloudflared/websockify/noVNC），实测外网经隧道拿到 VNC 握手、用户手机经 Cloudflare 地址看到画面并点[我弄好了]回调成功、收尾干净 🟢；新增 TG /vnc；自定义隧道（中转机/直连）保留为可选，手册 `docs/REMOTE-VIEW-SETUP.md`；本机 .env 的 NAS 配置已注释停用（NAS 钥匙的 permitlisten 放开仍在，无害）。仓库私有，新机器需 GitHub 访问权限。
**2026-10-22 共享浏览器池 🟢 已上线**：用户定：经 NAS 分时复用 6801（NAS 桌面 noVNC 也是随用随开）、2 槽位、所有项目共用一个身份、封装成技能+MCP 让所有会话可用。已完成：src/browser_pool.py（槽位 systemd-run 独立运行、按 CLI 进程租用、断开归还、空闲回收、cookie 共享）、browser MCP（scripts/browser-mcp → 官方 Playwright MCP 0.0.83 经桥接 CDP 中转）、local-sessions 加 request_browser_help/browser_help_status、TG 按钮、NAS 中转脚本（保存并恢复原 6801 服务）、setup 步骤 browser、技能 shared-browser（Claude/Codex）、全局规则与 workspace 规则、README。.env 已配 BROWSER_HELP_*。10-23 E2E 🟢：浏览器通路、NAS 钥匙放开（restrict,port-forwarding,permitlisten=127.0.0.1:16801,permitopen=127.0.0.1:9；备份 authorized_keys.bak-20261023）、用户手机经 noVNC 登录 SPACE 成功、超时收尾全部干净（NAS 6801 按 BROWSER_HELP_RESTORE 恢复）、紧急通知送达；身份同步加 localStorage（SPACE 令牌在 localStorage 的 session），槽位 2 打开 SPACE 已登录。[我弄好了]回调 10-23 已实测通过。10-23 08:2x 通知（docs/SHARED-BROWSER-NOTICE-20261023.md）：8 个空闲会话 received（apex/code-anywhere codex/server-config/issue-desk/ios codex/seatalk/seatalk-ws codex/smc-plugin）；digital-buddy %39 忙 not_sent 待补发；ios claude %58 忙、seatalk-ws claude %36 状态不明未发（重启后读全局规则）。状态卡同日收成两行（状态一行+最新一步一行）。方案 `docs/BROWSER-POOL-FEASIBILITY-20261022.md`
**2026-10-22 16:01 /clear 后误报“没发出去”+TG 收不到回复 🟢**：根因=hooks(session_start/stop/permission)初版遗留“cwd==code-anywhere 就跳过”，本项目 /clear 后桥接仍绑旧会话 a9961ee0（签收查旧日志→误报，watcher 看旧文件→回复不转发，外脑不注入）。已去掉跳过并手动补登记 55857b91；188 项测试通过。加固（用户要求）：桥接投递前核对 pane 会话标签、签收时查同项目新日志并自动改看，输入框判断只看输入框本身；191 项通过，16:11 已重启部署。待自然验证：下次 /clear 后 TG 正常收到回复、无误报。
**2026-10-22 下午收尾（清上下文前交接）🟡**：入口=新 bot @code_anywhere_sp_bot 私聊 Thread（9 项目 Thread 4699～4707，.bindings.json；名字登记 .topic-names.json）。私聊模式最终形态（用户逐项确认）：唯一置底状态卡（新消息 2 秒内挪回底部、8 秒刷新、工具在卡内滚动 4 条、[⏹中断][📸看终端]）；Thread 名末尾状态灯 🟢🟡🔴（>18 字符中间省略，放开头会被当图标、官方图标太花均否决）；不发“正在输入”、不点表情、不发消散汇总；输入框下方回复键盘[📸看终端][⏹中断]；All Messages 不转发只提示；投递后签收（queued_command 算签收、引用只取用户原文），会话忙时插入的消息不引用。All Messages 残留草稿、默认打开 All、原生停止按钮不显示均为 TG 客户端行为。Bot API 无法列出 Thread（unpinAll 对不存在的也返回成功，不可用作探测）；测试 Thread 均已删除，用户可见的是客户端缓存。新增 TG /clear（Claude 发 /clear、Codex 发 /new，会话忙则拒绝）供清上下文；Codex 状态卡的上下文%与本轮 token 改取会话日志 token_count。**待办**：⓪′ 重启后残留双卡（14:49 实例）：旧进程 14:48:53 发卡 601，14:49:25 新进程启动未删 601（日志无 deleteMessage），说明重启前后 .liveness-cards.json 记录与实际不一致；已手动删 601。排查：旧进程退出时是否覆盖写卡片文件、Monitor.run 清理与 recovery 补发的先后、多张卡并存时 _save_cards 竞争；兜底可在发新卡前删除该话题记录在案的旧卡。⓪ 统一状态信息（用户要求）：Claude 状态行是自定义的所以能读到 tokens/上下文；Codex 状态行也支持自定义（焦点在输入框时显示），配置成默认一直显示上下文用量与 token 等，与 Claude 一致，liveness 统一从屏幕读取（当前 Codex 临时改读会话日志 token_count，统一后可去掉）；① 紧急插队 🟢（d6457cd，18:35 Claude/Codex 临时会话实测：忙时普通 not_sent、紧急在下次工具调用插入并签收）；顺带修 80b256d：新版 Claude 忙时被判空闲、未带 --resume 的 Claude 无法投递。待：issue-desk 会话需重连 MCP（/mcp 或重启）才有新工具并读到新规则；② ./setup 默认私聊 Thread 模式（引导 BotFather 开 Threaded Mode、识别用户 ID 与 has_topics_enabled）；③ 富文本（10-22 02:13 在旧群发过【测试】富文本 /issues 消息待用户确认显示，随即搬家未确认；需在私聊 Thread 重发测试，确认后 /issues 每项自带按钮+验证方法折叠，再做 /status 表格、长输出折叠、思考块；sendRichMessage 需 do_api_request）；自动创建 Thread=现有能力 🟢（每 5 分钟按 Claude/Codex 会话日志发现 30 天内活跃的新目录建 Thread，私聊模式日志正常）。10-22 18:2x 用户排序：① 先做、② ok、③ 尝试做。已有结论、勿再列为待办：“郑和号”=语音转错，原意“整合好”；nas 改动用户已同意按建议纳入（三项已在 main 重做）；digital-buddy#1 验证方法由用户自己去说；/new、/model、新环境 provisioning、README 录屏由用户有空时自测。
**2026-10-22 GitLab issue 流程 🟢 已上线**：glab v1.124.0（`~/.local/bin`）以 PAT `~/.config/gitlab-token`(600, api) 登录 kong，git 协议 ssh；4 仓库均 Owner、issues 开启。kong/seatalk-workspace 实测 create/list/relabel/close 通过，测试 issue 已用 API 删除（注意 `glab issue delete --yes` 静默未删）。issue-desk：`~/workspace/issue-desk/CLAUDE.md` 别名路由 + 模板，bypass，Claude pane %64 sid 2d680dbf，TG topic 75269，watcher 已接。全局 CLAUDE.md/AGENTS.md 含「GitLab issue 处理」（agent:todo→doing→Closes/blocked）。
**2026-10-19 根盘写满应急 🟢**：/ 48G 100%（ENOSPC，CLI无法执行命令）；/tmp占13G。删除54项>6h未动、无进程引用的/tmp构建/临时目录约6.3G，根盘降至84%/余7.8G；清单 `~/.cache/tmp-cleanup-20261019.list`。保留浏览器profile(可能含登录)/sqlite/chromium/cloudflared约4.9G待用户确认；/home 23G（workspace 12G、.codex 4G）可迁 /data(273G空)。第二轮删3个apex旧工作树node_modules(git忽略,约3.6G)与4个旧cursor-server版本，根盘77%/余12G；apex vendor为git跟踪tgz(rc22-fix有未提交改动)未动。写满排查：无失控写入(2分钟根盘净增0MB)，属长期累积——/tmp各会话构建/浏览器profile约13G为主，其次Codex会话(digital-buddy/ios/seatalk三个10-14 rollout合计1.3G、thread_history_1.sqlite 847M，约0.3G/天)。建议.codex迁/data、定期清/tmp、加根盘告警。
**2026-10-14 02:35 全局变慢 🟡**：桥接health正常/7会话，8核load60、可用内存357Mi/15Gi且无swap，memory/IO PSI严重；大量Chrome renderer占用。业务会话多仍Working，apex空闲。seatalk已获用户资源治理任务，本轮未杀进程。证据 `recon/wiki/server-resource-pressure.md`。
**2026-10-14 安装码漏图 🟢**：digital-buddy-ios两次ImageView在distribution目录被排除，公开路径又是text围栏未被解析；文件存在，TG未尝试上传。已补单路径纯文本围栏识别，85测试通过、真实消息解析命中；systemd已部署/health正常。未补发旧码，内部ImageView范围不变。证据 `recon/wiki/tg-image-delivery.md`。
**2026-10-13 compact根因核查 🟢**：两样本replacement_history无assistant条目，已回复状态依赖摘要；无AGENTS/hook重答规定。属压缩恢复沟通状态缺口，非TG重投；建议恢复纪律，未改业务指令。证据 `recon/wiki/digital-buddy-input-audit.md`。
**2026-10-13 10:38 seatalk重复收到核实 🟢**：截图对应原生10:36:35 compacted→10:36:45重新“收到”，自10:29“好了”后无新user输入；重复确认发生于Agent压缩恢复，10:32–10:34/10:37属进展。证据 `recon/wiki/digital-buddy-input-audit.md`。
**2026-10-13 digital-buddy重复/from核查 🟢**：GLOBAL问题原生只收一次；四条独立commentary各转发一次，中间09:23:10明确compacted，压缩后重复确认。重复在Agent输出侧。自动来源识别与旧MCP兼容修复完成，新增排除dead pane防TTY复用误识别，83测试通过且systemd已部署，health正常，digital-buddy仅列%39；17:45实证seatalk旧MCP PID112802未重连，发给apex仍无源身份；长期存活客户端迁移未完成，期间兼容旧回执和投递。模型重复回应本身未修改。证据 `recon/wiki/digital-buddy-input-audit.md`。
**2026-10-12 话题整理完成 🟢**：用户明确授权清理无用入口；本地日志备份后删除旧seatalk/22125及重复apex/8772（均True），有历史的21681和420改名“历史 · …”并关闭（True）。历史话题原始消息未删。同步按resolve路径去重，旧seatalk精确排除，76测试通过。另已将digital-buddy-seatalk 16:54漏转回复补发22128/message22175。回执 `logs/topic-cleanup-20261012/actions.json`、`logs/digital-seatalk-final-backfill-20261012.json`。
**2026-10-12 digital-buddy-seatalk连接修复 🟢**：截图为%44、原sid01a12f47-6404-7473-9006-bef3c15748b8，topic22128已建但默认Claude未监听。新增无显式配置时按精确目录唯一活引擎识别并保存，显式偏好保留、双引擎不猜；实际已识别codex，76测试通过，systemd已部署，active API确认topic22128→%44原sid/gpt-5.6-sol/full。旧seatalk/topic22125保留。资产 `tests/test_backend_inference.py`。
**2026-10-12 digital-buddy误删恢复 🟢 / 原TG话题已删**：目录改名触发旧自动清理，10:26删除21059；已移除自动删除/取消置顶逻辑，支持原进程改名目录识别，71测试通过并部署。新同名话题21681接回原sid/%39；找回103条TG文字作为附件21683，原Codex历史完整保留并备份。证据 `recon/wiki/topic-rename-recovery.md`。
**2026-10-11 digital-buddy-supervisor关联 🟢**：话题21059已建，默认backend=claude导致18:36消息误启动%41后失败；真实Codex是%39/sid01a12a7d-4d15-7721-adb7-d4d00945779e。已设置项目codex并重启桥接，active API核实topic21059→%39原sid，gpt-5.6-sol/full；不重放历史输入。18:44截图确认显示的是18:36旧Claude失败提示，18:38:17已接入Codex，尚无修复后新入站消息验证。
**2026-10-10 协调消息显示简化 🟢**：按用户要求，TG把Agent message追踪头渲染为`⇢ from xxx`，原生编号和回执不变；助手正文前缀由🤖改为✦，用户随后选定单色组：用户›、协调⇢、回复✦；对应5项回归通过。资产 `src/renderer.py`、`src/api_server.py`、`tests/test_user_echo.py`；69测试通过，systemd已部署，health正常且4会话恢复。
**2026-10-09 21:12 资源/apex无响应核查 🟢**：apex 21:04:29收到TG问题，21:04:43 task_complete报server_overloaded/Selected model is at capacity；桥接healthy，非本机资源耗尽。8核load0.55，内存可用9.5GiB/15GiB，根盘73%剩14G，data33%剩316G；apex与seatalk-lite-web健康。seatalk-stream-maintenance、seatalk-input-v2健康检查失败，尚未查业务原因；最近2小时内核未见OOM记录。
**2026-10-09 00:05 一次性协调完成 🟢**：按Apex委托监控seatalk，原生00:05:18 task_complete且MCP waiting_input后，00:05:52通过MCP投递引用上下文需求；request_id `coordinate-seatalk-quoted-context-20261009-delivery-v1` 原生received，随后running。要求完整读需求、真实文字/图片闭环、追加结果并通知Apex；未打断旧任务，业务实现尚未完成。回执 `.agent-message-receipts.sqlite3`。
**2026-10-06 apex 图片未显示定位 🟢 / 交付缺口仍在**：00:37/00:39公开回复只用反引号给出/tmp/claude-passkey-display.png，解析器仅认Markdown链接，未触发上传；文件存在约1MB。证据 `recon/wiki/tg-image-delivery.md`；未补发登录图或改代码。
**2026-10-06 apex 新会话重关联 🟢**：旧pane `%31`已关闭，新Codex在 `%32` / sid `01a10cbf-e468-79d2-b0c1-195841319881`，终端GPT-5.6-Sol high；目录resolve变为`/data/home/toc/SSE/workspace/apex`使旧路径backend配置失配默认Claude。已补canonical路径codex/full并重启桥接，未中断业务；/sessions/active已确认原TG202→新sid/%32，model=gpt-5.6-sol、full；从EOF监听不补发历史。
**2026-09-28 18:25 seatalk 漏输入确认 🟢 / 修复待做**：首问TG已接收且tmux报成功，原生仅见9秒后的第二问及18:26重发首问；缺接收确认，同apex成功误报缺口，无超时。具体CLI吞输入机制未证实；截图/时间线见 `recon/wiki/apex-idle-diagnosis.md`。
**2026-09-28 apex TG 投递失败定位 🟢 / 修复待做**：12:53首条在tmux send-keys文字阶段10秒超时，未走Enter；重发按键成功但原生未收，12:57终端输入才开始任务。根因范围为TG→终端注入/确认，具体tmux阻塞未证实；见 `recon/wiki/apex-idle-diagnosis.md`。未重发/中断/改代码。
**2026-09-28 apex 模型切换 🟢**：按用户要求在原 pane `%31` 原生 `/model` 切为 `gpt-5.6-sol`，沿用默认 reasoning low；`/status` 实测 Model=gpt-5.6-sol、Permissions=Full Access，原 sid `01a0ca50-7988-7302-8026-b7c9e004563a` 保留。`.backends.json` codex / `.permissions.json` full 已一致。
**2026-09-26 19:40 seatalk 状态复核 🟢**：TG206 仍绑定 Codex `%33` 原会话 / GPT-6-Sol High Full；07:16 请求因上游401失败，并非持续执行。19:40:27 用户新发“看看？”已触发 task_started，随后终端有新的核对状态回复，当前恢复运行；未重启或注入消息。详见 `recon/wiki/model-auth-silent-failure.md`。
**2026-09-26 无回复诊断 🟡**：bot/polling 正常；07:16–07:21 两个 Codex 会话上游 401 共18次，入口租户钥匙有效。task_complete.error 被 normalize_codex 丢弃，TG 没有失败提示。当前独立原配置 Codex 请求3.58秒返回OK；未重启/改业务代码/重放历史，TG完整往返待新输入验证；错误漏报未修。详见 `recon/wiki/model-auth-silent-failure.md`。
**2026-09-25 出图慢时间线核实 🟢**：16:35 ImageView 仅查看 `.private` 内二维码，未上传；用户催链接后16:36:38触发，40秒回执5558，实际上传1.61秒。另有浏览器首次30秒超时重试，主要耗时在生成/交付触发而非TG网络；详见 `recon/wiki/tg-image-delivery.md`。
**2026-09-25 临时目录公开图片链接修复上线 🟢**：seatalk 13:29 ImageView 和公开链接均指向 `/tmp/seatalk-live-sync-profile-qr.png`，原范围规则静默跳过。已扩展公开图片链接支持当前用户拥有的临时文件，并对缺失/越界/超限给出 TG 提示；内部 ImageView 范围不扩大。54 项测试通过，实际原回复解析命中，systemd 已部署、health 正常；不重新生成或补发二维码。
**2026-09-25 11:08 seatalk 实时映射确认 🟢**：TG206→Codex `%33` / sid `01a0d2dc-d4f9-7e61-a26b-3ac935efdaca`，用户随后要求 GPT-6-Sol High Full，已用原生 /model 确认 High，thread_settings_applied 核实 model=gpt-6-sol、reasoning_effort=high、permission_profile=disabled、approval_policy=never，并补齐项目持久权限 full（此前失败回滚令保存值为默认只读）；用户新问模型的消息已进入并运行。Claude `%36` Fable5.1 High / bypass 仍保留空闲，未接TG。另一旧 Codex ce3f 仅历史。修复重启后未见历史重放。
**2026-09-25 Codex 旧历史重放修复上线 🟢**：10:57 `/model codex full` 恢复超时，紧接用户输入时 `codex_transcript` 暂不可见，被误当首轮；随后 `register_pane_watcher(from_start=True)` 从旧 rollout 0 偏移读，TG 重放昨天对话/工具/过期二维码。非模型重复执行。已重启 bot 止血（优雅停止挂住后仅终止 bot 主进程），保留所有终端。已改为注入前记录已有 rollout 字节偏移，注册后只读新增内容；真实新文件才从头读，新增3项回归验证恢复/首轮/调用顺序。全量50项测试通过，systemd 已上线；见 `recon/wiki/codex-history-replay.md`。
**2026-09-25 seatalk 看似卡住定位 🟢**：03:46:39 长测试已结束（not ok 5/9/10）；Claude 随后恢复本地 build-renderer 并重建，03:47:19 仍有工具活动，但未给自然语言回复。非继续等待旧测试；仅诊断未中断；详见 `recon/wiki/seatalk-claude-wait.md`。
**2026-09-25 seatalk Claude 无审批模式 🟢**：用户要求 access 无限制；在原 pane `%36` 用 `claude --resume cd9b8e2f-8de4-4105-a9aa-b58b6b2f0144 --model claude-fable-5-1 --effort high --dangerously-skip-permissions --permission-mode bypassPermissions` 恢复同会话。终端确认 `bypass permissions on`，Fable 5.1 High 保留；当前审批等待回合已中断，未自动重放生产任务。
**2026-09-25 seatalk 切换 Claude/Fable 🟢**：用户澄清先切 Claude 再选 Fable。项目 backend 已持久改 claude；在 seatalk-workspace 新窗口 `%36` 启动 Claude，信任现有项目，原生 `/model` 选择 Fable 5.1 / High、仅当前会话，终端回执确认。hook 已绑定 topic 206，新 sid `cd9b8e2f-8de4-4105-a9aa-b58b6b2f0144`；原 Codex `%33` 保留，跨引擎不搬聊天历史，依项目文件/recon 接续。
**2026-09-25 seatalk 会话模型切换 🟢**：按用户要求在 `%33` 原生 `/model` 选择 GPT-6-Astra / High，并用 `s session` 仅应用本会话。终端明确回执 `Model changed to gpt-6-astra high for this session only`，状态栏 GPT-6-Astra high；工作继续、未重启会话。
**2026-09-24 TG 两图相册核对 🟢 / 入站图回显待修**：seatalk 原生记录收到两张手机截图并一起分析；用户确认要相册合为一条多图输入；已实现按 chat/topic/user/media_group_id 收集、静默 1.2s 后按 message_id 排序合并引用和 caption，47 项测试通过，systemd 已上线、health 正常；待真实相册复验。ImageView 又回发了入站图（2600/2601），需补来源区分。截图结论与证据见 `recon/wiki/tg-image-delivery.md`。
**2026-09-24 ImageView 附件上传修复上线 🟢**：seatalk 新 sid 已绑定，文字回执 2274；图片只产生 ImageView、回复无 Markdown 链接，未命中上次上传逻辑。文件存在，未上传；码可能已过期。详见 `recon/wiki/tg-image-delivery.md`，20:09 按用户提供的新码补发成功（message 2291）；已实现受控 `.attachments/` ImageView 上传及去重/目录边界测试；42 项测试通过，systemd 已上线；用户要求只修复，不再补发或生成图片，待其重新生成验证。
**2026-09-24 旧话题显示问题与防重建 🟢**：用户授权谨慎清理；10 条本地历史归档 `logs/topic-cleanup-20260924.log`。旧 IDs 7/9/13/64/204 删除请求全部返回 Topic_id_invalid；用户刷新 TG 后确认旧条目均消失，系客户端陈旧列表，本次无删除成功回执。新增精确排除容器目录规则，子项目保留；40 测试通过、5 正式项目/4 活动会话正常。详见 `recon/wiki/topic-orphans.md`。
**2026-09-24 apex 停滞诊断 🟡**：14:14 TG 输入延至 14:25:46 才出现原生 UserMessage/task_started，14:32 已 Working 并执行构建/浏览器测试；两条旧问题仍在，非必然阻塞。具体恢复触发未证实；确定 `_inject_tmux` 只校验按键投递、不验证 CLI 收到消息，存在成功误报。证据 `recon/wiki/apex-idle-diagnosis.md`。
**2026-09-24 apex 长消息截断定位 🟢**：原生 UserMessage 完整 888 字符，TG echo 的 `text[:500]` 主动裁到 500，topic 202 / message 1343 与截图都停在 `badgeC`；非输入丢失。用户决策（2026-09-24）：TG/PC 同等展示，已实现完整原文分段（含 emoji 长度预算）、保留 TG 来源去重，39 项测试通过，systemd 已重启上线；证据与建议 `recon/wiki/tg-user-echo.md`。
**2026-09-23 Codex 图片交付修复上线 🟢**：文字已到 topic 206（message 648），Codex 回复的本地 PNG 链接未被上传；ImageView 也被转换层忽略。文件均存在，详见 `recon/wiki/tg-image-delivery.md`；已新增 `src/image_delivery.py` 并接入 Codex watcher：公开 Markdown 图片/链接按项目范围上传原图、单条去重、失败提示；全量 36 项测试通过（新增 5 项含 watcher 去重/内部图隔离/上传失败）；systemd 已上线、health 正常。按用户授权补发 topic 206 两张原图，TG 回执 foreground=666 / background=667，资产 `logs/codex-image-backfill.json`。
**2026-09-23 seatalk-workspace 失联定位 🟢**：项目已绑定 topic 206，但 `.backends.json` 未配置，默认 Claude 跳过实际 Codex pane `%33`；进程已定位原 sid `01a0ce3f-eeb7-7c52-b090-6c8bdeaf417b`。本次按用户要求接入现有工作，已将项目 backend 改 Codex；20:40:05 bot 重启后注册成功，`/sessions/active` 核实原 sid / `%33` / topic 206，原会话未重启；历史不补发。资产 `.backends.json`、`logs/code-anywhere.log`；新项目默认引擎问题仍待通用修复。
**2026-09-23 当前会话 TG 失联定位 🟢**：topic 11 的 Codex pane `%35` 首启停在 trust 提示，20:34:35 TG 输入未成为会话消息，20:34:41/20:35:04 watcher 注册失败；当前真实 rollout 已可由进程定位（sid `01a0ce42-bf12-7af3-9d33-b7815f182b6f`），但 `/sessions/active` 无该会话。20:37 经 systemd 重启 bot 后恢复监听，`/sessions/active` 已核实同 sid/pane → topic 11；不补发历史，首启 ready 判据缺陷仍待修。证据 `logs/code-anywhere.log`，机制 `src/api_server.py:register_pane_watcher`。
**2026-09-23 Codex 项目发现上线 🟢**：`discover()` 原先只扫 `~/.claude/projects/`，导致**只用 Codex、没跑过 Claude 的目录在 TG 里根本不存在**（真实踩中两次：pworkspace、apex）。现改为 Claude ∪ Codex 合并——新增 `discover_codex()` 扫 `~/.codex/sessions/**/*.jsonl`，只读首行前 8KB 正则取 `cwd`（整行含 base_instructions 可达几十 KB，全读没必要），同路径会话数相加、mtime 取较新。配套 `_is_ignored()`：默认排除 `$HOME` 本身与 `/tmp`、`/var/tmp`（否则"跑过 CLI 的地方"会刷屏 topic 列表），额外排除写 `.projectignore`（一行一绝对路径）。仅动 `projects.py`，`bot.py`/`api_server.py`/`/model` 逻辑未碰；新增 `tests/test_projects.py` 8 例，全量 31 例通过。**遗留**：新项目 backend 默认仍是 claude，纯 codex 项目需手动 `/model codex` 或写 `.backends.json`——可考虑按发现来源自动推断。
**2026-09-22 第二台机器部署（SZ dev）🟡**：Linux 开发机 `/home/toc/SSE/workspace/code-anywhere` 已复刻上线。代码经 `sync/from-nas` 分支中转（nas 工作区未提交改动已 commit+push，非直接落 main）；23 项回归通过。一机一 bot：`@arix_zhangxu_sz_bot` → 群 `Code_Anywhere_SZ`(`-1004330430159`)，与 nas 那套 bot 完全隔离，nas 服务未动。**双向链路已验证 🟢**：tmux→TG（watcher 经 `_recover_active_sessions` 按 cwd 绑 topic，**不走 SessionStart hook**——`hooks/session_start.py:20` 会 skip bot 自身目录）；TG→tmux 实测 `[tmux] injected pane=%23 chars=52`。**`/model codex full` 已真机验证 🟢**：server-config topic 76 切 Codex 成功（`gpt-6-astra` / YOLO），claude pane 不受影响，双引擎并存。**但暴露一个首启吞消息的坑 🔴**：Codex 在**新目录**首次启动会弹 `Do you trust the contents of this directory?`（与 Claude 的 trust 同源，`~/.codex/config.toml` 的 `[projects.*] trust_level` 就是它写的）。而 `ensure_pane` 的 `ready & stable` 只检测 `pane_processes`（进程在），停在 trust 提示上也算「就绪」→ 第一条消息注入后文本被吞、`Enter` 被当成 trust 确认，rollout 恰在该秒生成（实测 20:43:19 判 ready / 20:44:03 注入即建 rollout，消息在 rollout 里查无此句 = 真丢了），随后 `register_pane_watcher` 因 transcript 尚未生成而失败，TG 只回「已投递，但 Codex 尚未生成日志」。**修复方向**：ready 判据要等 rollout 出现或 capture-pane 确认 TUI 可交互，而非仅进程存活；或起 codex 前预写 trust_level。属首启一次性，trust 过后正常。常驻化 🟢：`~/.config/systemd/user/code-anywhere.service`（Restart=always + `loginctl enable-linger`），**改由 systemctl 管理，勿再用 `./code-anywhere.sh start`，会起第二实例抢 polling**。噪音项目治理：`projects.discover()` 无黑名单，删 topic 会被 5 分钟轮询重建；已将 `~/.claude/projects/` 下 home/workspace/tmp 三条移入 `projects-archived/`（可逆）。环境差异：本机原先无 `uv`（启动脚本硬依赖），已装；fnm 的 node 路径是 per-shell 动态的，service 里写死 `v22.22.0`。
**yard 会话映射已确认**：TG yard→`alan-llk/yard-codex` `%178`（Codex）；电脑 `yard-supervisor` `%155` 是保留的 Claude，会话不同。新窗口默认落入第一个 tmux session 导致入口混淆；仅诊断，未重绑。详见 `wiki/model-backends.md`。
**2026-09-15 权限切换 🟢**：`/model codex full` 与 `/permissions full|workspace|read-only` 已上线，项目独立持久化并立即恢复原会话；23 项回归通过。yard 同 pane/sid 切 Full，原生记录 `danger-full-access` / profile disabled；临时目录读写、HTTPS 200、SSH TCP 连接均真机通过。详见 `wiki/model-backends.md`、`logs/permissions-validation.log`。
**2026-09-14 GPT 切换 🟢**：项目级 `/model codex` / `/model claude` 已上线，runner-coach 当前 GPT（gpt-6-astra）。13 项回归通过；GPT 真机执行 runner-coach 测试 30 passed；关闭 Codex 再 resume 同 sid、Bot 重启恢复、web/SSE 续聊、TG 回传均通过。业务源码无改动。实现/边界/记录：`wiki/model-backends.md`。最终 TG 通知已送达：code-anywhere 48721 / runner-coach 48722。
**上一分支**：`阶段 7 TG 菜单交互支持` 状态 🟢（2026-08-27，阶段1+2 已上线并 sandbox 真机验证）
Claude Code 的 AskUserQuestion 菜单在 TG 端看不到→纯手机弹菜单即卡死（remote-xray/YARD 中招）。已打通 TG inline keyboard：watcher 检测 `tool_use(AskUserQuestion)`（等待时已结构化写入 transcript）→ 发按钮 → 注入按键。**三形态全部上线**：单问单选（点即提交）/ 单问多选（按钮累积✓+提交）/ 多页表单（逐问选+一次提交）。按键机制（实测）：单选项 `Down×idx`+`Enter`（选中且自动前进到下一页/Submit）；多选项 `Space` toggle + `Tab` 切页 + Submit 页 `Enter` 提交；每页光标初始第0项。序列生成 = `src/api_server.py:_build_keys`。真机验证：sandbox 造单选/多选/多页菜单，检测→发按钮→注入选中全部通过。
待阶段3：`Type something` 文本回退 + 原生菜单（plan accept/reject、`/`命令菜单，不写 transcript 需 capture-pane 截屏）。
---
**上一分支**：`阶段 6 项目外脑机制` 状态 🟢（2026-08-07）
每个 topic 自动外脑已上线并生产验证（用户反馈"流畅"）：`src/recon.py` + sync 自动建骨架 + SessionStart 注入「当前位置」+wiki 索引（递进式 ≤2KB + 体量哨兵）+ 防 PROGRESS 膨胀 + 附件清理。同批修复 👤 污染（human 白名单）、多行注入（bracketed paste）。详见决策日志 08-07 三行。
⚠️ 2026-08-13：topic 1587（code-anywhere 自身）曾因 `@ca_project` 标签被污染成 `/var/folders/.../tmp.*` 而完全无反应，已手动改回标签修复；**防护未做**（hook 打标签不校验路径、单 topic 哑掉无告警），详见决策日志 08-13。
无阻塞。可选遗留：runner-coach 移植 polling 修复(4.2 ⚪)；多行注入待真实 claude REPL 实测；5.2 僵尸 topic 待裁决；4.4 watcher sid 漂移未启。
---
**上一分支**：`single-session 收敛` 状态 🟢（2026-05-19）
定位收敛：code-anywhere = "PC/手机/web 3 端一体，完美同步单 session"。多 session 改造（阶段 2a/2b）已全面回滚 — 实际跑下来路由、UI、状态恢复都太脆。
回滚完成项：
- 删 `state.session_topics` 字段及 `bind/unbind/get_topic_for_session/get_session_for_topic`
- 删 `@ca_session` tmux 标签（只留 `@ca_project`）
- 删 `/sessions` POST、`/sessions/{id}` DELETE、`/sessions/{id}/rename` 三个写端点（保留只读 `GET /sessions/active`、`POST /sessions/{id}/input`、`GET /events/stream`）
- 删 `_session_titles` 及 `.session-titles.json`
- 删 `src/bot.py` 的 `_find_pane_for_session`、`_revive_claude_in_pane`、`bound_sid` 路由分支
- 简化 `_recover_active_sessions`：单路径 — 扫 tmux 找 claude pane，按 cwd→topic 用最新 jsonl 起 watcher
- TG 端已删除残留的 multi-session topic（14843、15342）
- 清理 `.state.json` 里残留的 `session_topics` 字段
保留的基础设施（与单/多 session 无关）：
- `_pane_has_claude(pane_id)` — 用 `ps -t <tty>` 反查实际前台进程（解决 macOS `pane_current_command` 总是返回 shell 名的坑）
- `_watchers: dict[sid, Task]` — watcher 按 sid 索引（避免同 topic 多 jsonl 抢踩）
- pane 实时反查（`@ca_project` 标签）
下一步：重启 `./code-anywhere.sh restart` 验证。
## 🎯 终极目标
PC ↔ 手机 ↔ web 3 端一体，完美同步单 session 远控本机 Claude CLI。每个项目唯一一个 session，绑定一个 TG topic。
## 🌲 作战地图
### 阶段 0：基础体验（已上线）
- 🟢 TG ↔ Claude CLI 双向打通；Forum Topic = 项目
- 🟢 架构：CLI `-p` + Hooks（SessionStart / PreToolUse / Notification / Stop）+ tmux 注入 / SDK fallback 双路径
- 🟢 文件附件代理、引用消息、流式渲染（content_block_delta 节流编辑）
- 🟢 项目级 trusted 模式（`.state.json` trusted_dirs，permission.py 直接 allow）
- 🟢 外部项目接入：runner-coach 的 web admin 反代 `:5000`（完全依赖 code-anywhere 作为 session 后端）
### 阶段 1：发现多 session 痛点（已完成）
- 🟢 1.1 tmux pane 陈旧 id 注入失败 → 改用 `@ca_project` 标签 + 实时反查（`hooks/session_start.py`, `src/bot.py:_find_pane_for_project`）
- 🟢 1.2 typing indicator 注入路径回归 → `pending_typing` dict + `api_server` watcher / `session_stop` 联动停（`src/bot.py:start_typing/stop_typing`）
- 🟢 1.3 `send_ephemeral` 5s TTL + 👤 图标是有意 UX，沉淀到全局 memory（`feedback_ephemeral_design.md`）
- 🟢 1.4 诊断出 watcher 按 `topic_id` 索引导致 multi-session 互踩（runner-coach 22:41 的 `57b882fc` 把 `caac3450` 的 watcher 顶掉，老 session 失联）
### 阶段 2：多 session 改造 🔴（已废除，2026-05-19）
**结论**：多 session 模型不适合 code-anywhere 的"3 端一体完美同步"定位。改造已全面回滚。
**废除原因**：
- 2a 上线后真实使用中暴露：3 个 session 消息全串到一个 topic，根因排查反复（pane 死活检测、macOS `pane_current_command` 缺陷、bot restart 内存映射丢失）
- 即使路由层修对了，UI/状态恢复的复杂度（哪个 sid 对应哪个 topic、哪个 pane 死了、resume 哪个 jsonl）也远大于价值
- 外部项目（如 runner-coach）若需多 session 自行实现，不再通过 code-anywhere 反代
**被回滚的改动**：见「📍 当前位置」回滚清单
### 阶段 2.5：Claude → TG 发图 🟢
原图优先 `send_document`，工具结果缩略图兜底；详见 `wiki/tg-image-delivery.md`。
### 阶段 4：Bot 假死根因攻坚 🟡（2026-07-26）
#### 4.1 轮询协程静默死亡 🟢（根因已证实）
**结论**：假死 = PTB `Updater` 的 `__polling_task` 被**非 `TelegramError` 异常**打死，且异常永远不会被打印。进程、事件循环、网络全部正常，只有重启才恢复。
**证据链**（`runner-coach/logs/runner-coach.log.2026-07-24`）：
- 06:27:53 最后一次 `getUpdates` **200 OK 正常返回**，之后 7h48m 再无任何 getUpdates 请求发出，直到 14:15:37 手动重启
- 同期 `AsyncIOScheduler`（跑在**同一事件循环**上）正常触发 418 行 job 日志，`executed successfully` → **事件循环健康**
- 同期 coros/httpx 其它出站请求全部 200 → **网络健康**
- 全程 **0 条 ERROR**，0 条 `Task exception was never retrieved`
- 同类空档还有：07-22 +21.4h、07-24 +7.8h、07-25 +56m、07-23 +75m —— 「三天两头」吻合
**机理**（`telegram/ext/_utils/networkloop.py:159-199`）：
```python
while effective_is_running():
    try: await do_action()
    except RetryAfter / TimedOut / InvalidToken / TelegramError: ...   # 只捕获这些
```
任何非 `TelegramError` 异常（含 socks 代理层异常、`CancelledError`、httpx 未被 PTB 映射的异常）直接冒泡出 while → task 以异常态结束。而该 task 被 `Updater.__polling_task` 长期持有引用，**从不被 await、从不被 GC** → asyncio 的 "Task exception was never retrieved" 兜底打印也永远不触发 → **完全静默**。

**为何 timeout 方案治不到**：timeout 只能踢掉"挂住的请求"；这里协程已经死了，没有任何进行中的请求可超时。日志佐证：code-anywhere 8 天 25 万行 getUpdates，间隔从未 >30s，轮询层从未卡过。

**为何 runner-coach 比 code-anywhere 高发**：runner-coach 是多租户**多 Application 实例**（`bot/main.py:_run_multi`）并发轮询，触发面成倍放大。

**已排除**：全局网络/代理层（其它出站请求正常）、双实例抢 getUpdates、handler 阻塞事件循环（scheduler 证伪）、update 未派发（code-anywhere 113 个带负载 update 全部落地 handler）。

#### 4.2 修复：自管轮询循环 🟢（code-anywhere 已落地，待重启验证）

**决策**：用户选定「自管轮询循环」—— 不依赖 PTB 私有属性，不重启进程，异常必定留痕。

**资产**：
- `src/polling.py`（新增）— `poll_forever()` 自己拉 update 塞进 `app.update_queue`；异常分级：`InvalidToken` 冒泡终止 / `RetryAfter` 按 retry_after 退避 / `TimedOut` 立即重试 / `TelegramError` 1→30s 指数退避 / **`Exception` 记全栈后退避重试（PTB 漏的就是这条）**。`run_app()` 接管完整生命周期（initialize → post_init → delete_webhook → start → 轮询），注册 SIGINT/SIGTERM 优雅退出
- `src/main.py` — `app.run_polling()` → `asyncio.run(run_app(app, allowed_updates=[...]))`

**已验证**（假 bot 注入异常序列）：RuntimeError → ValueError → TimedOut → TelegramError → 正常 update → KeyError，循环全程存活，update 正常入队，cancel 可正常退出。`code-anywhere.sh stop` 走 SIGTERM，与新的信号处理兼容。

**待办**：
- ⚪ 开发者执行 `./code-anywhere.sh restart` 验证线上行为
- ⚪ runner-coach 的 bot（`src/runner_coach/bot/main.py:_run_multi`，多实例并发轮询、假死高发）同步这套修法 — 待用户确认

#### 4.3 runner-coach review 🟢（只读，未动手 — 该项目 WIP 未提交 16 个文件）

**★ 新发现：RichHandler markup 地雷（实测确认）**

`runner-coach/src/runner_coach/common/logger.py:30-35` 的 root console handler 用 `RichHandler(markup=True)`。而 `logging.Handler.handle()` 不包 try/except，`RichHandler.emit()` 内部也没有标准 `StreamHandler` 那层 `except Exception: self.handleError(record)` —— 于是**日志调用本身会把异常抛给调用方**。

实测（runner-coach venv）：
```
log.warning("failed: %s", "[引用消息]abc[/引用消息]")  → ★ 抛 MarkupError 给调用方
log.exception("safe msg", exc_info=exc)                 → 安全（traceback 渲染不走 markup）
```
即：**任何把用户文本/异常字符串拼进 message 的 log 调用，都是一颗雷**。已在生产炸过：`runner-coach.log.2026-07-24:3039` — `main.py:648` 的 `log.warning("coach stream path failed: %s", e)` 因用户消息含 `[/引用消息]` 抛 MarkupError，整轮对话失败。

注：`[引用消息]...[/引用消息]` 这个格式正是 code-anywhere 的引用功能生成的（`src/bot.py:718-725`），跨项目传染。

与 4.1 的关系：这是「非 TelegramError 异常」的一个确凿产地。但 PTB 轮询路径上的拼接式 log 都是 `_LOGGER.debug`，而 runner-coach `log_level=INFO`（`common/config.py:51`）不 emit —— 所以**这条链在 INFO 级下不闭合**，尚不能断定它就是杀死轮询协程的那个异常。是目前最强候选，且无论如何都该修。

**其它 review 发现**：
- `bot/main.py:1407` `start_polling(drop_pending_updates=True)` — 假死数小时后重启，TG 服务端积压的 update 全被丢弃，**用户在假死期间发的消息永久消失**。雪上加霜，建议改 `False`
- 全项目未注册 `add_error_handler` — handler 异常只留一行「No error handlers are registered」，用户侧完全静默。至少该回一句「处理失败」
- 轮询假死修复需改**两条**路径：`_run_multi`（多实例，1403-1410）+ `run()` 里 `.env` 单 bot 回退分支的 `app.run_polling`（1438）

**code-anywhere 不受 markup 地雷影响**：用的是标准 `logging.Formatter` + `StreamHandler`（`src/log.py`），无 rich。

### 阶段 5：项目发现与 topic 卫生 🟡（2026-07-28）

#### 5.1 路径编解码 🟢

已统一到 `projects.py` 并验证 15 个目录往返；诊断与验证详见 `wiki/project-discovery.md`。

#### 5.2 僵尸 topic 积累 🟡（已盘点，待用户裁决）

19 条绑定中 **13 条是僵尸**（工作目录都还在，但 30+ 天没跑过 claude）：
- **7 个** transcript 已被 Claude 按 30 天策略清理（目录在、jsonl=0）：dotfiles(365) / mdx(369) / momentflow(371) / test(379) / arix(2465) / resume(2719) / workspaceone(2721)
- **6 个** 连 `~/.claude/projects/` 目录都没了：research(373) / router(375) / mk-arix(377) / smart-buddy(1094) / appraisal(10260) / site-security(16299)

**为何积累**：`sync_all_projects` 的清理条件是「工作目录消失」（`bot.py:844`），而这些目录都还在 → 永远清不掉。缺一条「长期不活跃」判据。

**活跃的 6 个**：code-anywhere(1587) / mywork(18330) / remote-cc(17813) / runner-coach(10296) / remote-xray(17954) / sandbox(17862，16 天前)
#### 5.3 项目未出现在 topic 列表 = 该目录从没跑过 claude 🟢（2026-08-06）
**不是 bug，是设计**。`discover()`（`src/projects.py:133`）的数据源是 `~/.claude/projects/`，只有产生过 jsonl transcript 的目录才会被枚举（`if not sessions: continue`）。
实例：`/Users/zhangxu/workspace/llm-gw` 目录存在且在活跃开发，但 `~/.claude/projects/` 无对应条目 → 列表里没有。**解法：在该目录跑一次 `claude` 即可**，之后 `sync_all_projects` 自动建 topic。
排查此类问题的判据顺序（下次直接照做，别再从编解码查起）：
1. `ls ~/.claude/projects/ | grep <名字>` — 空 → 从没跑过 claude，到此结束
2. 有目录但 jsonl=0 → 被 Claude 30 天策略清理（同 5.2 僵尸）
3. 有 jsonl 却仍不显示 → 才是 5.1 那类编解码 / 子目录过滤 bug
#### 4.4 遗留：watcher sid 漂移 ⚪（未启）
`_watchers` 按 sid 索引，新 sid 是新增 key，旧 watcher 会一直 tail 一个永不增长的 jsonl（不报错、不退出，pane 活着所以 `_pane_alive` 也不触发）。若某条换 sid 的路径漏掉 SessionStart hook，该 topic 会静默失联。与 4.1 是两个独立故障，触发条件出现时再启动。
### 阶段 3：将来可能（未启）
- ⚪ LLM 自动总结 session name（haiku，目前用户选择手动命名兜底）
- ⚪ code-anywhere 自家 web admin（不依赖 runner-coach）
## 📒 决策日志
- 2026-10-23 用户决策：Thread 名不缩写（撤销 >18 字符中间省略），只在末尾加状态灯，名字长短用户自己控制。
- 2026-10-23 用户决策：共享浏览器池只按内存限制（可用<2 GiB 不启动），不受全局浏览器进程数阈值约束；其他会话自启浏览器仍按原规则；同时保留数量限制：槽位硬上限 4（默认 2）、每槽位最多 6 个标签页。
- 2026-10-22 用户决策：私聊模式采用“唯一置底状态卡”（否决草稿气泡、消散汇总、固定官方图标、名字开头状态灯）；私聊不点表情、不发正在输入；紧急修复需要插队机制（待做）。
- 2026-10-22 用户决策：外脑规则是 code-anywhere 能力的一部分，必须覆盖 Codex（已写入全局规则模板）；TG 入口改为私聊话题模式（整体切换，群留作存档），待用户在 BotFather 开启 bot 话题后验证并迁移。
| 日期 | 决策 | 背景 | 影响 |
|---|---|---|---|
| 2026-08-13 | `@ca_project` 标签被污染成临时目录 → topic 1587 完全无反应（已手动修复，防护待做） | 用户报「TG 的 code-anywhere topic 没反应」。pane `code-anywhere:1.1` 里 claude 真实 cwd 正是 `~/workspace/github/code-anywhere`（`lsof -d cwd` 实证），但 `@ca_project` 标签写成了 `/var/folders/.../T/tmp.sdq5NB1Io1`。`_find_pane_for_project` 按标签匹配 → 找不到 pane → 消息投不进去。bot 轮询/收发全程正常（getUpdates 每 15s、其它 topic 照常），所以从外部看像「bot 挂了」，实际只有这一个 topic 哑掉 | 已 `tmux set-option -p -t code-anywhere:1.1 @ca_project <正确目录>` 修复，`_find_pane_for_project` 复验命中 `%30`，无需重启。**遗留**：① 标签为何被写成 tmp 目录未查明（疑似某次 claude 在临时目录启动，SessionStart hook 按当时 cwd 打标签后覆盖）② hook 打标签时应校验目录（存在 / 非 `/var/folders` 临时路径 / 是 git 仓库），或改用进程真实 cwd 兜底 ③ 这类「单 topic 哑掉」目前无任何告警，只能靠用户发现 —— 与 runner-coach 同类的静默失效 |
| 2026-08-13 | 移植 runner-coach 的 TG DC 故障转移传输层 | code-anywhere 收不到消息、新项目建不出 topic；我先误判为「网络全局阻断需代理」，用户以「同机 runner-coach 却正常」反驳并证伪 | 根因＝系统 DNS 把 api.telegram.org 解析到失效 IP（本机实测 .167.220 通 / .166.110 时通时不通 / .167.99 .175.50 不通）。新增 `src/tg_transport.py`（移植自 runner-coach `bot/tg_transport.py`，2026-08-11 同款事故），`create_app` 改用 `build_request`。重启后日志实证「出口切换: 系统DNS → 149.154.167.220」、getUpdates 恢复、积压 topic 补建 |
| 2026-08-07 | 落地「外脑」机制（recon 自动骨架 + SessionStart 注入 + 防膨胀 + 附件清理） | 用户要求：每 topic 自动外脑、防 compact 失忆、控 session 上下文、防 PROGRESS 膨胀（曾发生） | 新增 `src/recon.py`；sync 时为所有项目建骨架+项目 CLAUDE.md（不覆盖已有）；`session_start.py` 注入「当前位置」+wiki 索引（递进式、陈述句、≤2KB、体量哨兵）；`.attachments` 保留最近 20。PreCompact 不能注入上下文→靠 SessionStart 在 compact 后重注入闭环 |
| 2026-08-07 | 修 👤 污染：只 echo `origin.kind=='human'` 的真人消息 | ephemeral→永久后，compact 摘要/harness 注入/interrupted 等非真人 user 条目被永久刷屏 | `api_server.py` watcher 文本 echo 加 human 白名单；真实数据验证 8 真人保留、10 污染拦掉 |
| 2026-08-07 | 多行注入改 bracketed paste | `send-keys -l` 遇内嵌换行逐行提交→多行脚本散架（老坑，单行无碍） | `_inject_tmux` 多行走 `set-buffer`+`paste-buffer -p`，单行不变；待真实 claude REPL 实测 bracketed paste 生效 |
| 2026-05-16 | tmux pane 实时反查，不存陈旧 id | resume/restart 后 pane id 失效，消息丢 | hook 给 pane 打 `@ca_project` 标签；bot 注入时实时扫 tmux |
| 2026-05-19 | typing indicator 跨模块协作停 | 注入路径无反馈 typing 不见 | `pending_typing` dict；watcher 收 assistant 文本块或 session_stop 时停 |
| 2026-05-19 | `send_ephemeral` 5s TTL + 👤 图标是有意 UX，不动 | 用户严重纠正，曾被错改成永久消息 | 沉淀到 memory；今后不擅改这类"过程性消息" |
| 2026-05-19 | runner-coach 100% 走 code-anywhere `/sessions` 反代 | Explore 调研：`runner-coach/web/admin/server.py:45 CODE_ANYWHERE_BASE` | code-anywhere 是核心入口，多 session 改造势在必行 |
| 2026-05-19 | 手动 tmux 敲 `claude` 全归默认 topic（C2 兜底） | 不能 break 用户终端工作流 | SessionStart 未在 `session_topics` → 默认 topic |
| 2026-05-19 | 多 session topic 命名：创建时用户传 name，不做 LLM 自动总结 | 受控入口 A/B 都能传 name | `service.create(name=...)` 必传；topic 命名 `<project>#<name>` |
| 2026-05-19 | Phase 2 分 2a / 2b 两段 | 改动 8 个点风险大，分批稳 | 2a 先上 → 验证 → 2b（生命周期 + TG `/new`） |
| 2026-05-19 | 先只在 runner-coach 行使多 session；不做白名单 | 用户自控范围，简化 | 其他项目自然走单 session，体验零变化 |
| 2026-05-19 | 阶段 2a 验证通过收官 | `bug-fix` session 端到端测试：topic 自动建、独立路由、不串老 session | 2a 🟢；2b 留作触发条件出现时启动 |
| 2026-05-19 | **阶段 2 整体废除，code-anywhere 收敛为单 session 模型** | 真实使用中 3 个 session 串 topic，根因排查反复消耗信任；用户决定："3 端一体完美同步"才是定位 | 删除所有 multi-session 代码路径与状态，每项目 1 session 绑定 1 topic；外部多 session 需求由各自实现 |
| 2026-07-26 | **假死根因判定：PTB 轮询协程被非 TelegramError 异常静默打死** | runner-coach 7-24 空档 7.8h：getUpdates 停发但 AsyncIOScheduler 在同一 loop 正常跑、网络正常、0 条 ERROR | 修复必须落在「轮询协程存活」这一层；见 4.1 |
| 2026-07-26 | **纠正：客户端 timeout 方案本身正确，但治不到此病** | 用户提出 timeout 重连，我先前误答"不生效"。实为：timeout 只踢挂住的请求，而此处协程已死无请求可踢 | `HTTPXRequest` timeout + 禁 keepalive 的改动保留（防的是另一类故障） |
| 2026-08-06 | 项目"看不见"先查 `~/.claude/projects/` 有无条目，再查代码 | llm-gw 未出现在 topic 列表，实为该目录从没跑过 claude；差点又按 5.1 编解码 bug 方向排查 | 见 5.3 三步判据 |
| 2026-05-19 | **TG 端用户消息按来源 channel 分流** | "3 端一体"要求任意端打开都能看到完整问答；之前 watcher 一律 `send_ephemeral` 👤 5s 自动删，从 web/终端发的消息 TG 看不到 | bot.py 注入 TG 源消息后 `mark_tg_input(topic_id, text)`；watcher user event 一对一消费匹配项 → skip；未匹配（来自 web/终端）→ 永久 `send_message` 👤 |
## 🗂 资产索引
### 入口
- `code-anywhere.sh start|stop|restart|status|log|debug`
### 源码
- `src/bot.py` — TG handler、pane 实时反查（`@ca_project` 标签）、注入路由、typing 管理
- `src/api_server.py` — Hook 回调、JSONL watcher（按 sid 索引）、SSE 总线（只读）、`_recover_active_sessions`
- `src/claude_runner.py` — SDK fallback 路径（CLI `-p`）
- `src/state.py` — `.state.json` 持久化（trusted_dirs + session_ids 映射）
- `src/renderer.py` — 消息渲染、`send_ephemeral` 5s TTL、HTML 转换、流式分段
- `src/projects.py` — cwd ↔ topic_id 映射
- `src/config.py` — `.env` 加载、`PROJECT_ROOT` 常量
### Hooks（`~/.claude/settings.json` 注入）
- `hooks/session_start.py` — Claude 启动 → 打 `@ca_project` tmux 标签 + POST `/session_start`
- `hooks/permission.py` — PreToolUse → POST `/permission`（TG 审批，trusted 直 allow，bypass 模式跳过）
- `hooks/notification.py`、`hooks/stop.py`、`hooks/common.py`
### 持久化
- `.env`（敏感配置，不入库）
- `.state.json`（trusted_dirs + session_ids + topic_dirs + last_dir）
### 日志
- `logs/code-anywhere.log` 主日志，按日轮转
### 设计文档（设计阶段产物，活的状态以本 PROGRESS 为准）
- `docs/plans/implementation-plan.md`
- `docs/plans/research-report-final.md`
- `docs/plans/research-report-v1.md`
- `docs/plans/phase1-research.md`
### 外部接入（只读消费）
- 任何外部 web admin 都可以 `GET /sessions/active`、`GET /events/stream?session_id=`、`POST /sessions/{id}/input`
- 不再提供 `/sessions` POST/DELETE/rename — 外部若需要 spawn claude，自行 tmux 启动，hook 会自动回报
## 🧭 新 session 启动协议
每次新 Claude session 进来：
1. 读本文件「📍 当前位置」
2. 复述当前分支 + 计划下一步，等用户确认/修正
3. 用户确认后再动手
4. 任何决策、结论、废除 → **立即** 回来更新本文件
