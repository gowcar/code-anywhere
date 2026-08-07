# code-anywhere — 项目协约

本项目启用 `recon/` 外脑机制（防上下文压缩失忆）。**单一真相源 = `recon/PROGRESS.md`。**

## Session 纪律
- 开局先读 `recon/PROGRESS.md` 的「📍 当前位置」（SessionStart 已自动注入其摘要）。
- 稳定知识查 `recon/wiki/`——**按需 Read 单篇，不要一次全读**（递进式加载省上下文）。
- 做完一步实质工作 → **先回 PROGRESS 落一行再继续**（事件驱动，别攒到最后一次性写）。
- 出结论标 🟢 + 资产路径；走不通标 🔴 + 原因；用户做决策 → 写决策日志（带日期）。

## 防膨胀（重要，别重蹈"PROGRESS 越滚越大"的覆辙）
- `PROGRESS.md` 只放"活的"状态，全文 **≤ 250 行**。
- 稳定 / 领域知识 → 迁 `recon/wiki/<主题>.md`。
- 已结论 🟢 分支的正文、旧决策日志 → 归档到 `recon/decisions/archive.md`，PROGRESS 里只留一行指针。
- 定期精简，别让 PROGRESS 只增不减——否则外脑退化成又一坨大上下文。

## 图片 / 大产物（控上下文）
- 图片、截图**看过后把关键结论提炼成文字**写进 PROGRESS/wiki；图本身可随 compact 丢弃。
- 不要依赖"上下文里还留着那张图"——落盘的文字才是长期记忆。
