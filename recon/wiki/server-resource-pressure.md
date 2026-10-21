# 2026-10-14 02:35 全局变慢核查
桥接systemd active，health ok/7 terminal_sessions，TG仍getUpdates/sendMessage成功。8核load60.19/67.63/43.56，15Gi内存可用357Mi，无swap；memory PSI full avg10=45.56%，IO full78.48%，多数工作被内存/IO等待拖慢。RSS榜由大量Chrome renderer占据，单进程约250–350Mi；不能把RSS直接相加当独占物理内存。
终端：apex空闲/goal stalled；seatalk、digital-buddy、digital-buddy-seatalk、digital-buddy-ios均Working，iOS有输入待队列。seatalk终端已有用户要求调查并释放资源，当前未由本会话关闭浏览器或杀进程，避免与其并行处理冲突。

用户2026-10-14明确要求浏览器操作前检查数量并控制资源。已增加只读scripts/browser-resource-check，32进程/6GiB RSS/2GiB可用内存阈值，非0拒绝继续浏览器操作。共享规则追加到两个真实workspace根的AGENTS.md/CLAUDE.md，保留原内容；要求复用、单任务2页、finally清理自己的资源、不得杀他人页面。属于指令约束，无全局锁，不热注入现有Agent。
