# Claude / GPT（Codex）项目切换

2026-09-14。实现与现场验证记录。

## 结论与选择

可行，已实现原生 Codex 接入。Telegram 仍遥控本机 tmux 终端，PC 与 web 使用同一会话；不把开发任务退化为单次聊天 API 调用。

本机 Codex 配置选择 Yard，网关地址为 `https://hk.tckb.io/v1`，`wire_api=responses`；实测模型 `gpt-6-astra`。沿用用户已有凭证环境变量，代码不保存或打印密钥。安装版本 `codex-cli 0.154.0`。

评估的路径：

| 路径 | 评估 |
|---|---|
| Claude CLI 只换 GPT 模型名 | Claude 使用 Messages，当前 Codex 通道使用 Responses，不能据“网关有 GPT”就假定协议可以混用；这条路径未作兼容验证 |
| 原生 Codex + 现有 tmux/TG/web | 已选择；复用本机工具执行、原生上下文、终端交互，增加后端路由和日志适配 |
| 自建 Responses Agent 工具循环 | 可做，但需另建工具执行、权限和会话生命周期，超出本次切换需要 |

不做 429 自动重放：失败前可能已经执行过部分文件/命令操作，跨引擎重放可能重复产生副作用。用户明确切换后发送下一条任务。

## 使用

在已有项目 topic 中：

- `/model`：查看后端及已观测到的模型。
- `/model codex` 或 `/model gpt`：切到 GPT / Codex，之后直接发送开发任务。
- `/model codex full`：切到 GPT 并立即开启 Full，恢复原生会话。
- `/permissions full|workspace|read-only`：只切当前 Codex 权限；无参数查看当前权限及持久配置。
- `/model claude`：返回 Claude。
- `/status`：查看活动原生终端及模型。

切换仅作用于当前项目。默认仍为 Claude。切换发送 Escape 中断旧后端当前回合/重试，保留 CLI 与历史；停止旧后端 watcher，防止两边输出混到一个 topic。

GPT 模型沿用本机 Codex 配置。可在 `.env` 设置 `CODEX_MODEL` 覆盖新建/重启 Codex 终端使用的模型；已有终端模型由该终端管理。`/model` 用于选执行引擎，可带权限模式；不接受任意模型名参数。

## 实现边界

- `src/backends.py`：项目选择、Codex 凭证环境名发现、启动参数、原生 session 记忆、公共事件转换。
- `src/bot.py`：`/model`、按进程类型筛选终端、启动 Codex、多行粘贴、按项目串行投递。
- `src/api_server.py`：恢复选定后端、注册真实日志、转换后复用 TG/SSE、屏蔽失效后端输入和 hook。
- `.backends.json`：项目 → 后端；`.codex-sessions.json`：项目 → 原生 Codex session ID。独立于旧 `.state.json`，原子写入并忽略 Git。
- Codex 日志通过 `ps` 查 pane TTY 上的真实 Codex PID，再用 `lsof` 找该进程打开的 rollout，并验证 metadata.cwd。避免“同项目最近修改的文件”误绑定。
- 兼容当前分页日志的 `event_msg.item_completed`，以及旧版 `user_message/agent_message`；只发送公开消息和命令摘要，不转发 reasoning、系统指令、内部状态或重复的 `response_item`。
- 先固定文件偏移再接受输入；首次 Codex 日志在第一条消息后生成，则从该新日志开头消费。JSONL 未结束的行保留至下一次读取，包括中文 UTF-8 分片。
- Codex pane 关闭后再次发送消息会用记录的 `codex resume <session-id>` 恢复；Bot 自身重启也会重新关联仍在运行的终端。
- 新建 Codex 使用 `CLAUDE.md` 作为 `AGENTS.md` 缺失时的协约来源；跨引擎通过工作目录、协约、`recon/PROGRESS.md` 接续，**不转换 Claude 的隐藏上下文或原生会话 ID**。

## 权限及兼容限制

默认新建 Codex：已信任项目 `workspace-write`，未信任项目 `read-only`。2026-09-15 新增显式项目权限覆盖，保存在 `.permissions.json`：`full` = `danger-full-access`；`workspace` = 工作区写入并显式允许网络；`read-only` = 只读。均使用 `approval_policy=never`。`/permissions` 和 `/model codex full` 立即应用，优先于 `/trust` 默认值；不会自动把其它项目改为 Full。

本次保留原有项目发现来源（Claude 项目历史），适用于现有项目切换。Codex 专用项目自动发现、通用 Codex 原生审批/交互菜单转 TG 按钮、图片输出桥接未提供（权限已有快捷命令）；Claude 原有能力保留。Codex 会话使用本机 `codex resume` 管理，TG `/sessions` 会提示这一点。

## 验证

- `python -m unittest discover -s tests -v`：23 项通过。覆盖项目持久化、错误配置关闭路由、权限启动参数、当前/旧事件、内部记录过滤、真实 PID/cwd 绑定、JSONL 中文分片与去重、SSE、重启不重放、切换后旧 hook/输出隔离、tmux 失败不发送 Enter、命令授权、失效 web 输入。
- 现场建立新 Codex tmux pane，测试驱动构造带明确“自动验证”标识的本地 Telegram Update，经真实 `handle_message` 入口投递；**未伪称收到手机输入，也未另开 getUpdates 轮询**。
- 实际通过 Yard / `gpt-6-astra` 阅读 runner-coach 的两个测试及关联源码，并执行：

  ```sh
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_tg_render.py tests/test_lap_pattern.py
  ```

  **30 passed in 0.72s**，退出码 0；中文输出经 watcher 送至 runner-coach TG topic 10296，并产生 13 条公共 SSE 事件。首轮耗时约 53 秒（包括启动、模型推理和 Telegram 发送）。起始消息 ID 48708；本地详细记录 `logs/codex-validation.log`（不入库）。
- runner-coach 业务代码、数据库与服务不属于改造范围；测试不启停该项目的服务，不读真实跑者数据或凭证。
- 关闭测试用 Codex pane 后，`codex resume` 已恢复同一 session；最终 Bot 进程重新关联新 pane。通过生产 HTTP `/sessions/{id}/input` 投递续聊（200），SSE 收到回复，模型正确复述首轮两文件与 30/0/退出码 0；TG 续聊回执 message_id=48720。
- 最终服务 `/health` 返回 ok，11 个原生会话，Telegram getUpdates 持续成功。runner-coach 104 个源码/测试/协约文件哈希复核没有变化。
- 最后通知回执记在 `logs/codex-validation.log`。

## 官方资料

2026-09-14 实际读取的 OpenAI 官方页面（网页搜索工具未返回正文，使用 HTTPS 直接读取，返回 200）：

- `https://developers.openai.com/codex/noninteractive/`：非交互 `exec`、JSONL、resume。
- `https://developers.openai.com/codex/config-advanced/`：自定义 provider、Responses、自定义项目协约文件。
- `https://developers.openai.com/codex/config-reference/`：`wire_api`、`env_key`、`project_doc_fallback_filenames`。

实际落地使用交互式 tmux 而非 `exec`；启动/权限/恢复参数同时依据本机 `codex --help` 与真实运行验证。

## 2026-09-15 Full 权限修复

用户截图与实际 rollout 对照：yard 的 `permission_profile.network=restricted`，SSH 连接报 `Operation not permitted`；Playwright 临时目录也受只读沙箱限制。原先仅提供 `/trust`，无法从手机立即进入 Full，现补齐。

`src/permissions.py` 负责权限持久化与应用。先通过真实进程确认原 session ID，无法确认则拒绝重启；持有项目输入锁，Escape 中断当前回合，在同一 tmux pane 使用 `respawn-pane` + `codex resume <原ID> -s <mode> -a never` 启动；等待同 ID 的日志重新打开后才报告成功。模式设置失败会还原原持久配置，不伪报成功。会话历史保留，不自动重放中断前的业务任务。

新增 10 项权限回归：Full 启动参数/项目隔离、workspace 联网、无效配置拒绝、`/model codex full` 组合命令、无效组合不改状态、授权检查、状态查询不重启、无会话 ID 不杀终端、同 pane/同 sid 恢复、失败恢复原配置。加原有 13 项，共 23 项通过。

现场 yard 已使用组合命令切到 Full：pane `%178`，session `01a0a0a9-29e8-7340-81eb-bc0760959935`，恢复前后一致。生产 Bot 恢复后，同一会话实际 `turn_context.sandbox_policy.type=danger-full-access`、`permission_profile.type=disabled`。临时目录创建/写入/读取/自动清理成功；HTTPS HEAD HTTP 200；SSH 端口 TCP 连接成功后立即关闭（未登录）。TG 已回传探针结果，详见 `logs/permissions-validation.log`。

## yard-supervisor 与 TG yard 的会话对应诊断

现场只读核对：TG topic 31442 的生产 `/sessions/active` 指向 `alan-llk:3.1`（窗口 `yard-codex`、pane `%178`），真实进程为 Codex，session `01a0a0a9-29e8-7340-81eb-bc0760959935`、gpt-6-astra、Full。实际 `_find_pane_for_project(yard)` 也返回 `%178`，收发目标一致。

电脑端 `yard-supervisor:1.1`（pane `%155`）真实进程是 Claude；另有 `yard-kb` 的 Claude pane `%170`。三者工作目录相同，但不是同一会话。原因：切到 Codex 后按引擎筛选路由，保留旧 Claude；`_ensure_pane_for_project` 新建窗口时选择 `tmux list-sessions` 的第一个 session，因此 GPT 窗口落入 `alan-llk` 而非 `yard-supervisor`。该窗口落位容易误导用户，以为打开同名 tmux session 就是 TG 会话。

本次仅确认映射，未重绑、搬移窗口或中断会话。桌面要查看 TG 同一会话，应打开 `alan-llk` 下 `yard-codex`。后续若要统一入口，需要明确项目所属 tmux session 与活动 pane，不能仅按 cwd / 引擎猜测。
