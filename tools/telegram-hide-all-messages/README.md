# Hide Telegram All Messages

一个零权限的 Chrome / Edge 本地扩展，只在 `web.telegram.org` 页面里隐藏左侧的 **All Messages** 行：

- 保留所有 Topic；
- 不读取或修改消息；
- 不发送网络请求；
- Telegram Web 重绘侧栏后会自动再次隐藏。

## 安装

1. 解压交付的 ZIP。
2. Chrome 打开 `chrome://extensions`；Edge 打开 `edge://extensions`。
3. 打开“开发者模式”。
4. 点“加载已解压的扩展程序”，选择解压后的 `telegram-hide-all-messages` 目录。
5. 刷新 Telegram Web。

临时需要显示 All Messages 时，按 `Alt/Option + Shift + A`；再按一次重新隐藏。

这个扩展只改变当前浏览器的显示，不会删除 Telegram 云端数据，也不会影响手机或 Desktop 客户端。
