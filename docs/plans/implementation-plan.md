# Code Anywhere — 实施作战计划

## 核心体验

**坐在电脑前 → 随时拿起手机继续 → 回到电脑无缝衔接**

```
场景 A：电脑 → 手机
  用户在终端跑 claude，交给它一个大任务
  起身离开 → 打开手机 TG → 看到 Claude 的实时进展（自动同步）
  手机上回复/审批权限/发新指令 → Claude 继续工作

场景 B：手机 → 电脑
  用户在 TG 上和 Claude 聊了一会
  回到电脑 → 终端里 claude --resume → 接上手机的对话继续

场景 C：纯手机
  用户不在电脑前 → TG 上 /cd 选项目 → 直接开始编码对话
```

---

## 架构

```
┌─────────────────────────────────────────────────────────┐
│                    用户手机 (Telegram)                     │
│   话题 A (项目X)    话题 B (项目Y)    话题 C (恢复会话)     │
└─────────────┬───────────────────────────────────────────┘
              │ Telegram Bot API (Polling)
              ▼
┌─────────────────────────────────────────────────────────┐
│                   Code Anywhere Bot                      │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ TG 消息   │  │ 会话管理  │  │ 输出渲染  │              │
│  │ 处理器    │  │ (Session) │  │ (Render) │              │
│  └────┬─────┘  └────┬─────┘  └──────────┘              │
│       │              │                                    │
│       ▼              ▼                                    │
│  ┌──────────────────────────┐  ┌────────────────────┐   │
│  │  Claude 进程池             │  │  HTTP API Server   │   │
│  │  (长连接 stream-json)      │  │  (Hook 回调)       │   │
│  │  topic → 长驻进程          │  │  port 5000         │   │
│  └──────────────────────────┘  └────────────────────┘   │
│              │                          ▲                 │
│              ▼                          │ HTTP POST       │
│         claude CLI                 Hook 脚本              │
│    (--input-format stream-json)   (PreToolUse/Session/   │
│                                    Notification/Stop)     │
└─────────────────────────────────────────────────────────┘
              ▲
              │ 终端用户在电脑上跑 claude
              │ Hook 自动通知 Bot → TG 同步
```

---

## Milestone 拆分

### M0: 项目骨架 & 重构 PoC（0.5 天）

**目标**：从 PoC 单文件重构为正式项目结构

```
code-anywhere/
├── src/
│   ├── __init__.py
│   ├── main.py              # 入口
│   ├── config.py             # 配置管理
│   ├── bot.py                # TG Bot + 命令路由
│   ├── session.py            # 会话管理（进程池、状态持久化）
│   ├── claude_runner.py      # Claude CLI 长连接封装
│   ├── renderer.py           # Markdown → TG HTML 渲染
│   └── api_server.py         # HTTP API（Hook 回调）
├── hooks/
│   ├── common.py             # 共享工具
│   ├── permission.py         # PreToolUse Hook
│   ├── session_start.py      # SessionStart Hook
│   ├── notification.py       # Notification Hook
│   └── stop.py               # Stop Hook
├── start.sh
├── .env.example
├── requirements.txt
└── docs/plans/
```

**验收**：项目结构建好，PoC 功能迁移到新结构并正常运行

---

### M1: 核心通信 + 会话管理（1 天）

**目标**：完整的 TG ↔ Claude 双向通信

特性：
- [x] TG Bot 基础通信（PoC 已有）
- [x] Claude CLI 长连接（PoC 已有）
- [x] stream-json 双向流式（PoC 已有）
- [x] 会话恢复 resume（PoC 已有）
- [x] Forum Topic = 会话（PoC 已有）
- [x] /cd 选项目 + 历史会话列表（PoC 已有）
- [ ] Markdown → TG HTML 渲染（mistune）
- [ ] 消息智能分段（≤4096 字符，段落边界）
- [ ] 工具调用可视化（图标 + 摘要）
- [ ] 白名单认证（装饰器）

**验收**：在 TG 中能流畅对话，Markdown 格式正确，工具调用清晰可见

---

### M2: PC ↔ 手机无缝切换（1.5 天）⭐ 核心

**目标**：电脑终端 Claude 会话实时同步到 TG，手机可接管

#### 2.1 终端 → 手机（Hook 驱动）

- [ ] SessionStart Hook：终端启动 claude → Bot 自动创建 TG 话题
- [ ] Notification Hook：Claude 输出 → 实时转发到 TG 话题
- [ ] PreToolUse Hook：权限审批 → TG 按钮（允许/拒绝）
- [ ] Stop Hook：会话结束 → TG 通知

流程：
```
用户在终端: claude
  → SessionStart Hook → Bot 创建话题 "项目名 (终端)"
  → Claude 工作中 → Notification Hook → TG 实时显示进展
  → Claude 要执行工具 → PreToolUse Hook → TG 弹出审批按钮
  → 用户在手机上点"允许" → Hook 返回 allow → Claude 继续
  → 用户在 TG 话题里发消息 → Bot 通过 tmux send-keys 注入终端
  → Claude 看到新指令，继续工作
```

#### 2.2 手机 → 电脑（Resume 驱动）

- [ ] 手机上发的所有消息通过 `--resume` 写入同一个 session
- [ ] 用户回到电脑：`claude --resume <session_id>` 接上
- [ ] 或者：终端会话仍在运行，TG 消息通过 tmux 注入

#### 2.3 会话状态同步

- [ ] 话题标题实时更新（显示当前状态：运行中/等待/已完成）
- [ ] /status 展示所有活跃会话（终端 + TG）

**验收**：
1. 终端跑 claude → 手机看到实时进展 → 手机审批权限 → 手机发消息 → 终端继续
2. 手机对话 → 电脑 resume → 上下文完整

---

### M3: 体验打磨（1 天）

- [ ] Bypass 模式（/bypass 一键关闭权限审批）
- [ ] 图片支持（TG 发图 → 本地保存 → Claude Read）
- [ ] 交互式选择 → Inline Keyboard
- [ ] /interrupt 远程中断
- [ ] /info 查看会话信息
- [ ] 错误处理 & 重试
- [ ] TG 速率限制处理（缓冲 + 编辑消息）
- [ ] 进程健康检查 & 自动重连
- [ ] 启动 banner & 品牌

**验收**：完整体验流畅，各种边界情况处理到位

---

### M4: 部署 & 稳定性（0.5 天）

- [ ] Hook 脚本自动安装到 claude settings.json
- [ ] 后台运行模式（nohup / tmux）
- [ ] 日志管理（滚动、清理）
- [ ] .gitignore 完善
- [ ] 一键启动脚本

**验收**：`./start.sh` 一键启动，所有配置自动化

---

## 当前进度

**阶段**：准备启动 M0
**状态**：等待用户确认计划
