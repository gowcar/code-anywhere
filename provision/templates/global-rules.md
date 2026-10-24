## recon 外脑（code-anywhere 能力，Claude 与 Codex 通用）

项目根目录有 `recon/PROGRESS.md` 时，它就是这个项目的**单一真相源**，用来防止上下文压缩或重开会话后失忆：

- **开局先读** `recon/PROGRESS.md` 的「📍 当前位置」。Claude 会由 SessionStart 自动注入摘要；**Codex 没有这个注入，必须自己先读**。
- 稳定知识查 `recon/wiki/`，按需读单篇，不要一次全读。
- 做完一步实质工作，**先回 PROGRESS 落一行再继续**；出结论标 🟢 加资产路径，走不通标 🔴 加原因，用户做的决策写进决策日志并带日期。
- 防膨胀：PROGRESS 只放“活的”状态，全文 ≤ 250 行；稳定知识迁到 `recon/wiki/<主题>.md`，已结论的条目和旧决策归档到 `recon/decisions/archive.md`，PROGRESS 只留一行指针。
- 图片、截图看过后把关键结论写成文字落盘，不要依赖“上下文里还留着那张图”。
- 项目自己的 `AGENTS.md` / `CLAUDE.md` 有更细的规定时，以项目规定为准。

## 跨 session 沟通规则（local-sessions MCP，2026-10-19 用户要求）

用 local-sessions 的 `send_message` 给其他 Codex/Claude 会话发消息时，消息正文只能是“一句话”：

- **简单事项**：一句话说清楚“请对方做什么”，到此为止。
- **复杂事项**：一句话写明总目标和要求的动作，并附上一份 md 详情文件的绝对路径。背景、证据、清单、约束、回报格式全部写进这份 md。
  - md 放在发送方项目里，比如 `docs/` 或 `handoffs/`，文件名要有主题和日期，确保对方能直接 Read。
  - 那一句话本身要能看懂：对方不打开文件，也知道要协调的是什么事、需要做什么（例如只读自查、回报、暂不改动）。
- **只用 local-sessions MCP**（`list_sessions` / `send_message` / `send_urgent_message`）找会话和发消息；Claude 自带的 `ListAgents` / `SendMessage` 只看得到 Claude 会话，看不到 Codex 会话，会找不到或发错对象。
- **禁止**把多段背景、接口清单、问题列表直接塞进消息正文。跨 session 消息超过 2 到 3 行，就应该改成“一句话 + md”。
- 授权边界（如“暂不改动或发布”）用短语写进那一句话，细节放进 md。

好例子：
> 请按 ~/workspace/<项目>/docs/INCIDENT-<主题>-<日期>.md 只读自查 X 服务是否有同类空间限制、串行阻塞和错误吞没风险，并按文档回报证据与最小修复建议，暂不改动或发布。

坏例子：消息正文写了五六段，包含合同背景、接口列表、多个独立问题和回报要求，读的人抓不住要点。这些内容应该写进 md，消息只留一句话。

回复对方时同样遵守这条规则：结论用一句话，详细证据写成 md 并给出路径。

## issue 处理（GitHub / GitLab，2026-10-21 用户要求）

用户通过专门的 issue-desk 会话把问题提成 issue，带 `agent:todo` 标签。在仓库主目录运行 `issue-platform` 看用哪个平台：`github` 用 `gh`，`gitlab` 用 `glab`，`local`（项目还没推到远端）用 `local-issue`——子命令和参数与 `gh issue` 相同（create / list / view / comment / edit / close），编号形如 `local:<项目目录名>#N`。三者都在仓库主目录里自动找到仓库。有远端就一定用远端：项目推到远端后，再用 `local-issue` 时没关闭的会自动迁移过去，并提示改用 `gh` / `glab`。项目会话按下面方式处理：

- **什么时候查**：收到 issue-desk 的一句话通知时，或者完成手头任务、准备停下来时，在仓库**主目录**查一次待办。同一仓库的其他 worktree 会话不处理 issue。
  GitHub：`gh issue list --label agent:todo`；GitLab：`glab issue list --label agent:todo`；本地：`local-issue list --label agent:todo`
- **接手**：先评论说明谁在处理，再把标签 `agent:todo` 改成 `agent:doing`：
  GitHub：`gh issue comment <N> -b "<会话名> 接手处理"`；`gh issue edit <N> --remove-label agent:todo --add-label agent:doing`
  GitLab：`glab issue note <N> -m "<会话名> 接手处理"`；`glab issue update <N> --unlabel agent:todo --label agent:doing`
  本地：`local-issue comment <N> -b "<会话名> 接手处理"`；`local-issue edit <N> --remove-label agent:todo --add-label agent:doing`
- **读 issue**：`gh issue view <N> --comments` / `glab issue view <N> --comments` / `local-issue view <N> --comments`；**看截图用 `issue-images <仓库> <N>`**，它直接给出本机原图路径（所有会话在同一台机器上），本机没有原件时用共享浏览器打开链接。
- **处理**：按项目原有纪律做（读 recon/PROGRESS、测试、发布授权边界不变）。issue 本身不构成发布、LIVE 写入或删除数据的授权。
- **修完（等用户验证）**：推送分支；在 issue 里评论：改了什么、提交号与分支、**做不到或与原需求不同的部分**、用户怎么验证（装哪个版本 / 打开哪个页面）；评论里**必须有单独一行 `验证方法：<一句话写全前提和操作>`**，例如“验证方法：安装 iOS 0.2.67（需先删掉旧版重装），用正式账号登录，在 Chat 页点输入框后按住输入栏下拉”；需要重启、重新登录、清缓存、等部署等条件都写进这一行。TG 的 `/issues` 会把这一行显示给用户，条件变化（如发了新版本）就再发一条带新 `验证方法：` 行的评论，以最新一条为准。然后把 `agent:doing` 改成 `agent:to-verify`，并在 TG 用一句话告诉用户“#N 已修好待验证”。**不要自己关闭 issue**，提交信息里用 `Refs #N`，不要写 `Closes #N` / `Fixes #N`（会在合并时绕过用户验证自动关闭）。
- **评论内容**：issue 对团队可见，评论里不要写服务器本地路径（`/home/...`、`/Users/...`）、令牌或会话 ID；需要附截图时用 `issue-upload <仓库> <文件>` 上传后贴它输出的 markdown。
- **用户验证**：用户验证通过后由用户（或 issue-desk、TG `/issues` 按钮）关闭；不通过会退回 `agent:todo` 并附说明，重新接手处理。
- **阻塞**：需要用户决定、或者被阻塞时，改标签为 `agent:blocked` 并评论原因，然后在 TG 告诉用户。
- **不要**一次接手多个 issue 并行改，只给正在改的那一个标 `agent:doing`；按创建时间先后逐个处理，优先处理用户当前正在交代的任务。
- **紧急**：带 `priority:urgent` 的 issue 或收到 `[Urgent agent message …]` 时，先把手头任务停在安全点（提交或在 PROGRESS 记一行进度），立即接手该 issue，修完再回到原任务。

## 代码与仓库保持同步（2026-10-22 用户要求）

- 每完成一步可验证的工作（测试通过、功能可用），就提交并推送到远端，不要在工作区堆积未提交的修改。
- 一次提交只做一件事，提交信息写清楚改了什么、为什么。
- 结束一段工作前用 `git status` 确认工作区干净、和远端同步；确实要暂存的半成品放到分支上并推送，并在 PROGRESS 记一行。
- 不要替用户提交或改动其他机器、其他人工作区里的未提交内容；先报告，等用户决定。

## 共享浏览器（code-anywhere 能力，2026-10-22 用户要求）

需要浏览器时（打开网页、查内部平台、验证页面、登录后取数据），**优先用 code-anywhere 共享浏览器**，不要自己另外启动浏览器：

- 用 **browser MCP**（官方 Playwright MCP 的全部工具）。第一次调用时自动分到一个独占槽位，空闲 10 分钟后自动归还，下次调用自动重连。
- 所有项目共用一个登录身份：先打开页面看看是否已经登录，再决定要不要请用户登录。
- 需要用户操作时（登录、SSO、二次验证、验证码、扫码），先用浏览器打开对应页面，再调用 local-sessions 的 `request_browser_help(reason)`。用户在 TG 里点按钮，就能在手机上接管同一个浏览器，做完你会收到通知。**绝不要**让用户在聊天里发密码或验证码。
- 不要在共享浏览器里退出登录、清 cookie、改设置，也不要关闭共享浏览器本身。详细用法见技能 `shared-browser`。
