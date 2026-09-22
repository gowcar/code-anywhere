# TG 图片交付（已验证）

### 阶段 2.5：Claude → TG 发图通道 🟢（已上线）

触发场景：runner-coach 截了图想发到 TG 看，但 Claude 自己说"发不了"（Anthropic Messages API output 无 image block）。

落地（`src/api_server.py:_watch_transcript`）：
- 🟢 watcher 在 assistant.tool_use 阶段按白名单（`mcp__Playwright__browser_take_screenshot`/`Read`/`Write`/`MultiEdit`/`NotebookEdit`）抓 `filename` / `file_path` → 拼成绝对路径存 `tool_use_image_paths[tool_use_id]`
- 🟢 看到对应 tool_result（任意 inner，含 text 也行）→ 文件存在则 `send_document` 发原图
- 🟢 退化路径：原图缺失 + tool_result inner 有 image block → `send_photo` 发 base64 缩略图
- 🟢 去重：`sent_image_tool_ids` 集合，每个 tool_use_id 只发一次

关键坑：
- TG `send_photo` 强制压缩长边到 ~1280px 并转 JPEG（长图必糊）→ 原图统一走 `send_document`，客户端仍能 inline 预览图片，但字节无损
- Playwright MCP 的 tool_result 不一定带 image block（行为不稳定）→ 不能依赖 image block 作触发条件，要靠 tool_use 阶段抓 path

效果：runner-coach 让 Claude 截图后 TG 自动出原图，零额外提示词，runner-coach 项目无感知。

