"""按 /model 偏好重启项目终端，并接回同一个原生会话（历史不丢）。

调用方持有该项目的输入锁；与 permissions.apply_mode 相同，先中断当前回合再 respawn。
"""
import asyncio
import logging

log = logging.getLogger("code-anywhere.model")


async def restart(cwd: str, topic_id: int) -> str:
    """返回给用户看的结果说明；无法安全重启时抛 RuntimeError。"""
    from src import api_server, backends, bot
    backend = backends.get_backend(cwd)
    pane = await bot._find_pane_for_project(cwd)
    if not pane or not await backends.pane_processes(pane, backend):
        return "当前没有运行中的终端，下一条消息启动时生效。"
    if backend == "codex":
        found = await backends.codex_transcript(pane, cwd)
        if not found:
            raise RuntimeError("无法确认当前 Codex 会话 ID，未重启终端。先发一条消息后再切换。")
        sid = found[0]
        backends.remember_codex(cwd, sid)
        argv = backends.codex_launch_args(cwd, resume_id=sid)
    else:
        sid = next((s for s, v in api_server.terminal_sessions.items()
                    if v.get("tmux_pane") == pane and v.get("backend", "claude") == "claude"), None)
        argv = backends.claude_launch_args(cwd, resume_id=sid)
    await backends.command("tmux", "send-keys", "-t", pane, "Escape")  # 中断当前回合，不退出 CLI
    await asyncio.sleep(0.5)
    bot.stop_typing(topic_id)
    await backends.command("tmux", "respawn-pane", "-k", "-t", pane, "-c", cwd,
                           *backends.launch_env_args(backend), *argv)
    log.info("[model] restarted %s pane=%s sid=%s argv=%s", backend, pane, sid, argv[1:])
    for _ in range(40):
        await asyncio.sleep(0.5)
        # 第一次在这个目录跑这个 CLI 会弹“是否信任此文件夹”：默认选项是退出，不处理就会卡住，
        # 用户紧接着发的消息还会被当成回车把 CLI 退掉（2026-10-24 digital-buddy 从 Codex 切 Claude）
        await bot._accept_trust_prompt(pane, wait=0.5)
        if not await backends.pane_processes(pane, backend):
            continue
        if backend == "codex":
            resumed = await backends.codex_transcript(pane, cwd)
            if resumed and resumed[0] == sid and await api_server.register_pane_watcher(pane, cwd):
                break
        elif sid:  # Claude resume 后日志续写在同一文件里，按最新日志接回
            if await api_server.register_pane_watcher(pane, cwd):
                break
        elif await bot._hook_session_for_pane(pane, wait=1):  # 之前就没有会话：等启动 hook 上报新会话
            break
    else:
        raise RuntimeError(f"{backend} 重启后未能接回会话，请看终端 {pane} 的启动提示。")
    if backend == "codex":
        from src import permissions
        mode = permissions.get_mode(cwd)
        await backends.command("tmux", "set-option", "-p", "-t", pane, "@ca_permission", mode)
        if sid in api_server.terminal_sessions:
            api_server.terminal_sessions[sid]["permission_mode"] = mode
    return "已重启终端并接回原会话。" if sid else "已重启终端（新会话）。"
