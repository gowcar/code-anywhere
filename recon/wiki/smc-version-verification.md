# SMC / sdpcli 版本核查（2026-10-14）
smc plugin info sdpcli：installed v1.4.2；最新uploaded v1.4.2（10月10日），published v1.4.0（9月18日）。package_config.json installed.sdpcli记录v1.4.2、binary_path=/home/toc/SSE/.smc/python/bin/sdpcli，installed_at=2026-10-14T07:39:50Z。
strace execve实测smc sdpcli --version经过.local/bin/smc→.smc/python/bin/smc→同一sdpcli路径。程序自报dev；go version -m显示ldflags仅-s -w，revision e5277449d2db853045014087c779fd8089d82fe2，vcs.time2026-10-10T06:06:08Z，modified=true。因此确认执行安装登记v1.4.2对应路径，不能宣称程序自报1.4.2或已完成官方包哈希一致性核验。
发布状态不会在每次运行时选择替代已安装程序；list Latest当前指published。指定版本安装语法由smc install/update --help确认。未实测无版本update是否降级，不应断言；本轮未改安装。

用户授权实测smc update sdpcli：退出0，无错误，自动从v1.4.2降为published v1.4.0；技能同步成功。随后按用户此前最新版本要求执行smc update sdpcli@v1.4.2恢复，退出0，安装记录复核v1.4.2。更新提示现有Codex重启/Claude reload-plugins才加载新技能，未重启业务会话。

2026-10-14 用户确认新版sdpcli可直接操作live发布，要求全会话通知并记忆；业务实证来源是用户，不是本会话执行发布。持久说明 `docs/SDPCLI-LIVE-VERIFIED-20261014.md`；不再沿用CLI无法live的旧判断，仍遵从实际平台权限审批。
