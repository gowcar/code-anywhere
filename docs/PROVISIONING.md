# 新机器部署（./setup）

一条命令完成 code-anywhere 的安装与自检，可反复执行：每一步先检查，缺什么才问你要不要补。

```sh
git clone git@github.com:gowcar/code-anywhere.git ~/workspace/code-anywhere
cd ~/workspace/code-anywhere
./setup            # 交互式安装 / 修复
./setup --yes      # 所有安装与写入都默认同意，一路装到底（bot token 等仍需你填）
./setup --check    # 只检查，不改任何东西
./setup --only telegram,service   # 只跑部分步骤
```

## 步骤

| 步骤 | 做什么 |
|---|---|
| deps | 全部自动装：git / curl / tmux（自动识别 apt、dnf、yum、pacman、zypper、apk、brew；Mac 无 Homebrew 时先装它）、uv、claude、Node 22（从 nodejs.org 下载到 `~/.local/share/node`，免 sudo）、codex（npm 装到 `~/.local`）、glab。Python 3.10+ 由入口脚本 `./setup` 先行安装 |
| python | 用 uv 建 `.venv`，装 `requirements-mcp.txt`（含 `requirements.txt`） |
| tools | `tools/` 下的 `t`、`tmux-tui`、`cc`、`gitlab-upload` → `~/.local/bin`，`tmux.conf` → `~/.tmux.conf`；已有且不同的先备份 |
| auth | Claude（`claude auth status` 或 `.env` 的 `ANTHROPIC_API_KEY`）、Codex（登录或 config.toml 网关） |
| telegram | 验证 BOT_TOKEN（getMe）；你建群、开话题、设 bot 为管理员（含“管理话题”）并发一句话；自动识别 CHAT_ID 和你的 TG 用户 ID 写入 `.env`（ALLOWED_USERS 只放你自己） |
| hooks | Claude hooks 写入 `~/.claude/settings.json` |
| mcp | local-sessions 注册到 Claude 用户级配置与 Codex |
| rules | `provision/templates/global-rules.md` 写入 `~/.claude/CLAUDE.md`、`~/.codex/AGENTS.md` 的标记块，不动其他内容 |
| gitlab | 可选：令牌存 `~/.config/gitlab-token`（600）并登录 glab；创建 `~/workspace/issue-desk`（Sonnet 5 / medium / bypass） |
| service | Linux：systemd 用户服务 + linger；macOS：launchd。PATH 按 claude/codex/node 实际位置生成，换 node 版本后重跑即可 |
| verify | 健康检查，并可往群里发一条上线消息 |

## 注意

- **一个 bot 同一时间只能被一台机器接收**。新机器新建 bot，或先停掉旧机器服务；setup 检测到 409 冲突会提示。
- 凭据不随仓库迁移：Claude/Codex 登录、GitLab 令牌、git SSH 公钥都在新机器上重新做。
- 话题与目录的绑定（`.bindings.json`）不迁移：新机器路径不同，项目首次在 claude/codex 里说话后 5 分钟内自动建话题。
- 服务由 systemd/launchd 管理，**不要**再用 `./code-anywhere.sh start`，会起第二个实例抢消息。
- 全局规则以仓库模板为准：要改跨 session 规则或 issue 规则，改 `provision/templates/global-rules.md` 后重跑 `./setup --only rules`。
