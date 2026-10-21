## 跨 session 沟通规则（local-sessions MCP，2026-10-19 用户要求）

用 local-sessions 的 `send_message` 给其他 Codex/Claude 会话发消息时，消息正文只能是“一句话”：

- **简单事项**：一句话说清楚“请对方做什么”，到此为止。
- **复杂事项**：一句话写明总目标和要求的动作，并附上一份 md 详情文件的绝对路径。背景、证据、清单、约束、回报格式全部写进这份 md。
  - md 放在发送方项目里，比如 `docs/` 或 `handoffs/`，文件名要有主题和日期，确保对方能直接 Read。
  - 那一句话本身要能看懂：对方不打开文件，也知道要协调的是什么事、需要做什么（例如只读自查、回报、暂不改动）。
- **禁止**把多段背景、接口清单、问题列表直接塞进消息正文。跨 session 消息超过 2 到 3 行，就应该改成“一句话 + md”。
- 授权边界（如“暂不改动或发布”）用短语写进那一句话，细节放进 md。

好例子：
> 请按 /home/toc/SSE/workspace/seatalk-workspace/docs/ADS-SQLITE-TEMP-SPACE-CATCHUP-INCIDENT-20261019.md 只读自查 Digital Buddy SeaTalk 的 ADS 是否有同类空间限制、串行阻塞和错误吞没风险，并按文档回报证据与最小修复建议，暂不改动或发布。

坏例子：消息正文写了五六段，包含合同背景、接口列表、多个独立问题和回报要求，读的人抓不住要点。这些内容应该写进 md，消息只留一句话。

回复对方时同样遵守这条规则：结论用一句话，详细证据写成 md 并给出路径。

## GitLab issue 处理（2026-10-21 用户要求）

用户通过专门的 issue-desk 会话把问题提成 GitLab issue（git.garena.com），带 `agent:todo` 标签。项目会话按下面方式处理：

- **什么时候查**：收到 issue-desk 的一句话通知时，或者完成手头任务、准备停下来时，在仓库**主目录**查一次待办。同一仓库的其他 worktree 会话不处理 issue。
  `GITLAB_HOST=git.garena.com glab issue list --label agent:todo`
- **接手**：先评论说明谁在处理，再把标签 `agent:todo` 改成 `agent:doing`：
  `glab issue note <N> -m "<会话名> 接手处理"`；`glab issue update <N> --unlabel agent:todo --label agent:doing`
- **处理**：按项目原有纪律做（读 recon/PROGRESS、测试、发布授权边界不变）。issue 本身不构成发布、LIVE 写入或删除数据的授权。
- **修完（等用户验证）**：推送分支；在 issue 里评论：改了什么、提交号与分支、**做不到或与原需求不同的部分**、用户怎么验证（装哪个版本 / 打开哪个页面）；然后 `glab issue update <N> --unlabel agent:doing --label agent:to-verify`，并在 TG 用一句话告诉用户“#N 已修好待验证”。**不要自己关闭 issue**，提交信息里用 `Refs #N`，不要写 `Closes #N`（会在合并时绕过用户验证自动关闭）。
- **评论内容**：issue 对团队可见，评论里不要写服务器本地路径（`/home/...`）、令牌或会话 ID；需要附截图时用 `gitlab-upload <仓库> <文件>` 上传后贴它输出的 markdown。
- **用户验证**：用户验证通过后由用户（或 issue-desk）关闭；不通过会退回 `agent:todo` 并附说明，重新接手处理。
- **阻塞**：需要用户决定、或者被阻塞时，改标签为 `agent:blocked` 并评论原因，然后在 TG 告诉用户。
- **不要**一次接手多个 issue 并行改，只给正在改的那一个标 `agent:doing`；按创建时间先后逐个处理，优先处理用户当前正在交代的任务。
