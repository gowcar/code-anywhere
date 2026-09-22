# PROGRESS — code-anywhere 作战地图

> 单一真相源。新 session 第一件事就读它。

## 📍 当前位置
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
