# 项目发现路径编解码（已验证）

#### 5.1 路径编解码丢下划线项目 🟢（已修并验证）

**同一个假设（`cwd.replace("/", "-")` 漏掉 `_` 和 `.`）散落 6 处，造成四个叠加症状**：

1. `_reverse_path()` 把 `newbee-intro` 还原成 `newbee/intro` → `is_dir()` 判否直接丢弃
2. 还原修好后又撞上**子目录过滤** —— `mywork` 先被收录，`mywork/newbee_intro` 被当子目录吃掉

（注：最初统计「被子目录过滤丢弃 = 0 个」是假象 —— 因为它在第 1 关就没进候选集，根本走不到第 2 关。）

3. topic 建出来后仍无消息 —— **正向编码另有 5 处副本**：`cwd.replace("/", "-")`（`api_server.py:140,899` + `state.py:100,139,188`）同样漏掉 `_` 和 `.`，导致 `_recover_active_sessions` 找不到 transcript 目录 → watcher 起不来 → topic 是空的

4. topic 的 welcome 消息退化成一句「直接发消息开始对话」—— `_send_topic_welcome`（`bot.py:772`）本该带最近 3 轮对话摘要，但建 topic 时（23:20:50）`state.get_recent_history()` 仍是坏版本（23:24 才重启生效），读到 0 轮 → 走退化分支

**教训**：第一次只修了解码方向就报「已修复」，用户追问「你确信吗」才查出正向编码还有 5 份副本；用户再追问「其他 topic 怎么有消息」才发现 welcome 也是受害点。**同一个假设散落 6 处**是这个 bug 能活这么久的原因 —— 每次只修一处都会留下"看起来还是坏的"的表象。

**排查中被证伪的两个猜想**（记下来避免重走）：
- ❌「其他 session 一直在产出所以有消息」— 实测今天(07-29)除 code-anywhere(22 条)外**所有 topic 都是 0 条新消息**，用户看到的是历史消息堆积
- ❌「建 topic 建晚了」— 时间点无关；只要 `state.py` 没修，任何时刻建 topic 的 welcome 都是空的

**修法**：编解码统一收敛到 `src/projects.py`，其余模块一律调用，不再自己拼字符串：
- `reverse_path(hash_name)` — 目录名 → 真实路径，逐段拿 `[-_.]` 正则去父目录实际匹配（替代只试连字符的字面拼接）
- `claude_dir(project_dir)` — 真实路径 → transcript 目录，先按 `[/_.]→-` 编码直取，失败则反查兜底
- 子目录过滤改判据 — 只过滤「与父项目**同属一个 git 仓库**」的子目录（如 `repo/poc`）；容器型目录下的独立项目（`~/workspace/mywork/*`）各自成项目

**验证**：
- discover 6 → 7 个，`newbee_intro` 正确收录；同 repo 子目录仍被过滤（回归通过）
- 编解码往返一致性：15 个 transcript 目录全部 `name → path → name` 闭合，0 不一致
- 端到端：重启后 `[sync] newbee_intro → topic 20870` → `[recover] newbee_intro → pane=%18 sid=dd0e6606 topic=20870` → `[watcher] start` 全链路打通

---

<details><summary>原始诊断记录</summary>

**现象**：`ws-zhangxu` pane（`%18`，cwd `~/workspace/mywork/newbee_intro`）在 TG 上没有 topic，消息完全不通。

**根因**：`src/projects.py:75-91 _reverse_path()`。Claude 的 transcript 目录名把 `/` `_` `.` 统统编码成 `-`，还原时只尝试合并成连字符，于是：
```
-Users-zhangxu-workspace-mywork-newbee-intro
  → /Users/zhangxu/workspace/mywork/newbee/intro   ← 不存在
  → discover() 第 100 行 is_dir() 判否 → 丢弃
  → 永远不建 topic → session_start 收到回调也只能「未绑定话题，跳过」
```
日志实证：`logs/code-anywhere.log:28051-28052`（2026-07-28 18:25:03）。

**影响面**：当前仅 1 个（newbee_intro），但 `~/workspace/mywork/` 下 `team_by_topic`、`mi_service_http`、`slides_processor` 等含下划线目录一旦跑 claude 都会中招。

**修法**：还原时每段候选用 `[-_.]` 正则匹配父目录下的实际条目名，而非只试连字符。

</details>

