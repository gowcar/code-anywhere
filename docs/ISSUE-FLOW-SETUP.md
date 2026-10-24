# 配置 issue 流程——给 Agent 的执行手册

用户在 code-anywhere 的 Thread 里说“配置 issue 流程”“接 GitHub / GitLab issue”之类的话时，照这份手册做。目标：用户在 issue-desk 的 Thread 里口头反馈问题，issue-desk 提成 issue、通知负责的项目会话；修好后用户在 TG `/issues` 里点按钮验收。

原则：一次只问一个问题；能自己查的不问（例如用 `gh repo list` 列出用户的仓库让他挑）。

## 1. 问平台

问：**“你的代码放在 GitHub 还是 GitLab？”** GitLab 再问域名（例如 `gitlab.example.com`）。

## 2. 装命令行并登录

- GitHub：没有 `gh` 就装（macOS `brew install gh`；Linux 有 sudo 用包管理器，没有就从 https://github.com/cli/cli/releases 下载到 `~/.local/bin`）。登录：
  `gh auth login --hostname github.com --git-protocol ssh --web`，把它打印的一次性码和网址发给用户，让用户在手机上打开网址、输入码授权；你等命令结束。也可以请用户建一个 fine-grained 令牌（Issues 读写、Contents 读写、Metadata 读）发到 Thread 里，用 `gh auth login --with-token` 登录，登录完马上提醒用户删掉那条消息。
- GitLab：没有 `glab` 就装（同上，`brew install glab` 或 releases）。请用户在 `https://<域名>/-/user_settings/personal_access_tokens` 建令牌，只勾 `api`；存到 `~/.config/gitlab-token`（权限 600），`glab auth login --hostname <域名> --stdin < ~/.config/gitlab-token`。
- 检查：`gh auth status` / `glab auth status`。

## 3. 问清仓库和说法

1. 列出用户的仓库（`gh repo list --limit 50` / `glab repo list`），问：**“哪些仓库要接进来？”**
2. 对每个仓库问一句：**“你平时怎么称呼它？比如‘App’‘后端’‘官网’。”** 再看本机哪个项目目录对应它（`git -C <目录> remote get-url origin`），那个目录名就是“负责会话”。本机还没有这个仓库时，问用户要不要 clone 到 `~/workspace/<名字>` 并 `/new <名字>` 开会话。
3. 同一个仓库有多端（例如 Web 和 Android 在一个仓库）时，可以拆成多行，用不同标签（`area:web`、`area:android`）。

## 4. 落地（一条命令）

```sh
scripts/issue-desk-config --platform github \
  --repo "<owner/repo>|<用户的说法，用顿号隔开>|<标签或 ->|<负责会话目录名>" \
  --repo "..."
# GitLab：--platform gitlab --gitlab-host <域名>
```

它会：记下平台、在每个仓库建好 `agent:todo / doing / to-verify / blocked / wontfix`、`priority:urgent` 标签、生成 issue-desk 的规则（旧的先备份）。

## 5. 生效与验证

1. 让 issue-desk 读新规则：用 local-sessions 的 `send_message` 给 issue-desk 发一句“issue 流程已配置，请重读本目录 CLAUDE.md”；如果它的会话还没启动或在忙，告诉用户在 issue-desk 的 Thread 里发 `/clear`。
2. 请用户在 issue-desk 的 Thread 里说一个测试问题（例如“测试一下 issue 流程，App 首页标题错了”），确认：issue 建在正确的仓库、带 `agent:todo`、负责会话收到了通知。
3. 用户在 TG 发 `/issues`，能看到这条；测试完用 `/issues` 里的“放弃”按钮关掉它。
4. 把结论写进 `recon/PROGRESS.md`（不要写令牌）。
