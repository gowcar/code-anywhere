# 建立 VNC 通道（远程查看共享浏览器）——给 Agent 的执行手册

用户在 code-anywhere 的 Thread 里说“建立 VNC 通道”“配置远程查看”之类的话时，照这份手册做。目标：`request_browser_help` 发出的 [🖥 打开浏览器] 按钮，用户在手机上点开就能操作共享浏览器。

原则：一次只问一个问题，用户不懂的名词换成大白话；能自己查的不问；改别的机器之前先说清楚要改什么，等用户同意。

## 1. 先问清楚手机怎么连到这台机器

问用户：**“你的手机能直接打开这台机器上的网页吗？比如有公网 IP 或域名、在同一个局域网、或者手机连了能访问它的 VPN。”**

- 能 → **直连模式**（第 2 节）。还要问：手机用什么地址访问它（IP 或域名），可以用哪个端口（有路由转发的话，问清对外端口和转到本机的端口）。
- 不能 → 问：**“有没有另一台机器，手机能访问它，这台服务器也能 ssh 上去？”** 有 → **中转机模式**（第 3 节）；没有 → 告诉用户目前做不了，可以以后再配，到此结束。

## 2. 直连模式（noVNC 在本机临时启动）

1. 装 noVNC：`sudo apt-get install -y novnc websockify`（会装到 `/usr/share/novnc`）；没有 sudo 就 `git clone https://github.com/novnc/noVNC ~/.local/share/noVNC` 和 `git clone https://github.com/novnc/websockify ~/.local/share/noVNC/utils/websockify`。
2. 证书（建议）：手机通过公网访问时用 https。用户有域名证书就用它；否则可以先用 http（链接里的密码一次性、15 分钟有效），告诉用户这一点，由用户决定。
3. 写 `.env`（项目根目录），按实际情况填：
   ```
   BROWSER_HELP_LISTEN=0.0.0.0:<本机端口>
   BROWSER_HELP_PUBLIC_URL=<http 或 https>://<手机访问的地址>:<手机访问的端口>
   BROWSER_HELP_WEBSOCKIFY="websockify --web /usr/share/novnc"   # https 时追加 --ssl-only --cert <证书> --key <私钥>
   ```
   不要写 `BROWSER_HELP_SSH_HOST`（留空就是直连模式）。
4. 防火墙要放行这个端口（`sudo ufw allow <端口>/tcp` 之类，先问用户）。

## 3. 中转机模式（经另一台机器转出去）

1. 问清：中转机的 ssh 地址、端口、用户名；手机访问中转机用的地址和端口；中转机上 noVNC 监听的地址和端口（有路由转发时，是转发目标的那个内网地址和端口）。
2. 给本机生成专用钥匙：`ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519_ca_relay -C code-anywhere-relay`，在 `~/.ssh/config` 加：
   ```
   Host ca-relay
       HostName <中转机地址>
       Port <ssh 端口>
       User <用户名>
       IdentityFile ~/.ssh/id_ed25519_ca_relay
   ```
3. 让用户把下面这一行加到中转机的 `~/.ssh/authorized_keys`（只允许在中转机本机 16801 端口开反向隧道，其他转发都禁止）：
   ```
   restrict,port-forwarding,permitlisten="127.0.0.1:16801",permitopen="127.0.0.1:9" <公钥内容>
   ```
   用户授权的话，也可以由你用现有的登录方式代加；改之前先备份，改完马上用新钥匙测试登录。
4. 中转机上装 noVNC（不需要 sudo）：
   ```
   git clone https://github.com/novnc/noVNC ~/noVNC && git clone https://github.com/novnc/websockify ~/noVNC/utils/websockify
   ```
   中转机上要有 python3、`nc`、`lsof`（macOS 自带）。
5. 写 `.env`：
   ```
   BROWSER_HELP_SSH_HOST=ca-relay
   BROWSER_HELP_LISTEN=<中转机上 noVNC 监听的地址:端口>
   BROWSER_HELP_PUBLIC_URL=<https 或 http>://<手机访问中转机的地址>:<端口>
   BROWSER_HELP_WEBSOCKIFY="<中转机上的绝对路径>/noVNC/utils/websockify/run --web <绝对路径>/noVNC"   # https 时加 --ssl-only --cert <绝对路径> --key <绝对路径>
   ```
   路径必须写绝对路径（`run` 会先切换目录）。
6. 如果中转机上这个端口平时被别的服务占着（例如用户自己的远程桌面），接管时会临时让出、结束后恢复：把恢复命令写进 `BROWSER_HELP_RESTORE="..."`，先在中转机上手动执行一遍确认能起来。不写的话结束后只停不恢复，要提前告诉用户。

## 4. 生效与验证

1. 重启服务：Linux `systemctl --user restart code-anywhere`，macOS `launchctl kickstart -k gui/$(id -u)/com.code-anywhere`。
2. `curl -s 127.0.0.1:5000/browser/status` 里 `help_available` 应为 `true`。
3. 实测：用 browser MCP 打开一个网页，调用 `request_browser_help("测试远程查看：打开后随便点一下页面，再点我弄好了")`，请用户在手机上点按钮；用户点完“我弄好了”后，`browser_help_status` 应为 `done`。中转机模式再确认中转机上被临时让出的端口已恢复。
4. 打不开时按顺序查：手机能否打开 `<PUBLIC_URL>/vnc.html`；中转机上 `nc -z 127.0.0.1 16801`（隧道）；`~/.code-anywhere/novnc.log`（中转机）或桥接日志 `logs/code-anywhere.log` 里的 `[browser]` 行。
5. 把配置结论写进 `recon/PROGRESS.md`（不要写令牌或私钥内容）。
