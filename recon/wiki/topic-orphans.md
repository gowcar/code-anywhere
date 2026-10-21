# TG 孤立旧话题（2026-09-24）

用户截图 `.attachments/20260924_144325_photo_AQADtRFrG7auoFV8.jpg` 显示 pworkspace、workspace 两个、tmp、SSE。日志创建记录：2026-09-22 SSE=7，workspace=9/64，tmp=13；2026-09-23 pworkspace=204。当前 `.bindings.json` 仅 11/76/202/206/420 五个正式项目，discover() 实际也仅这五个。现存日志中没有上述旧 topic 的 deleteForumTopic 记录，也没有它们近期重新创建记录。因此证据支持旧 TG 话题遗留，不能说是本次重启重建。

同步只能枚举本地绑定；旧话题一旦从绑定移除，就不会再被清理。过滤 discover 也不删除 Telegram 已有话题。`.projectignore` 尚不存在，且其规则是整棵子树排除，不能直接加 workspace 根目录（会误排 apex 等真实项目）。本次仅定位，未删除 TG 话题或改配置。清理需明确按旧 topic ID 处理，避免同名误删与删除历史。

## 2026-09-24 用户授权清理及服务端复核

用户要求谨慎清理。保存本地日志中 10 条相关记录与 bindings 快照到 `logs/topic-cleanup-20260924.log`（0600，非完整 TG 历史导出）。确认旧 IDs 与现有绑定/4 个活动会话无交集。新增 `.projectignore` 的 `=绝对路径` 精确排除语法（原裸路径子树规则不变），配置精确排除 SSE、workspace、pworkspace；tmp 已默认排除。新增子项目保留回归，全量 40 项通过，实际 discovery 仍仅 5 正式项目。systemd 已重启，health 正常。

按历史 IDs 7/9/13/64/204 请求 deleteForumTopic，服务端全部返回 Topic_id_invalid，未取得任何删除成功回执。不能声称本次已删除，亦不能仅凭旧日志确定截图条目仍是这些 IDs。可能是客户端陈旧列表；需要用户重开群核对，若仍显示则需要当前话题 ID 再定位，禁止按同名猜测删除。此前“未清 TG 所以残留”只由本地记录推断，服务端结果未支持其仍存在。

## 用户最终确认（2026-09-24）

用户刷新 Telegram 后确认旧条目已经消失，显示异常归因为客户端陈旧列表。本次未取得删除成功回执，不宣称由删除请求清除了话题；问题已闭环，精确排除规则保留。
