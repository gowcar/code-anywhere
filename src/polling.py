"""自管 Telegram 轮询循环 — 替代 PTB 的 Updater。

为什么不用 `app.run_polling()`：
PTB 的 `network_retry_loop`（telegram/ext/_utils/networkloop.py）只捕获 TelegramError
家族，任何其它异常（socks 代理层、CancelledError、httpx 未被映射的异常……）都会冒泡出
while 循环，让 `Updater.__polling_task` 以异常态结束。而该 task 被 Updater 长期持有引用，
从不被 await、从不被 GC，于是 asyncio 的 "Task exception was never retrieved" 兜底打印
也永远不触发 —— 协程死了，异常被带进坟墓，日志上一个字都看不到。

表现就是"假死"：进程活着、事件循环正常、其它出站请求正常，就是再也不拉 update，
只能重启进程。实测 runner-coach 单次假死长达 7h48m。根因证据见 recon/PROGRESS.md 阶段 4.1。

本模块自己跑轮询：任何异常都记全栈并退避重试，循环永不退出。
"""

import asyncio
import logging
import signal

from telegram.error import InvalidToken, RetryAfter, TelegramError, TimedOut

log = logging.getLogger("code-anywhere.polling")

# 单次 long-poll 的挂起时长（秒）。HTTPXRequest 的 read_timeout 必须大于它。
POLL_TIMEOUT = 10
# TelegramError 后的退避上限（秒）
MAX_BACKOFF = 30

# 最后一次成功轮询的时间（monotonic），供诊断用
last_success: float = 0.0


async def poll_forever(app, allowed_updates: list[str] | None = None) -> None:
    """拉 update 塞进 app.update_queue，直到被 cancel。任何异常都不会终结这个循环。"""
    global last_success

    bot = app.bot
    offset: int | None = None
    backoff = 0.0
    consecutive_errors = 0

    log.info("[polling] 自管轮询启动")
    last_success = asyncio.get_running_loop().time()

    while True:
        try:
            updates = await bot.get_updates(
                offset=offset,
                timeout=POLL_TIMEOUT,
                allowed_updates=allowed_updates,
            )
        except asyncio.CancelledError:
            log.info("[polling] 收到 cancel，退出轮询")
            raise
        except InvalidToken:
            # token 错了重试多少次都没用，让它冒泡
            log.critical("[polling] BOT_TOKEN 无效，轮询终止")
            raise
        except RetryAfter as e:
            backoff = e.retry_after + 0.5
            log.warning(f"[polling] 限频，{backoff:.1f}s 后重试")
        except TimedOut:
            # long-poll 超时是常态，立刻重来
            backoff = 0.0
        except TelegramError as e:
            consecutive_errors += 1
            backoff = 1.0 if backoff == 0 else min(MAX_BACKOFF, backoff * 1.5)
            log.warning(f"[polling] TelegramError（连续 {consecutive_errors} 次）: {e}，{backoff:.1f}s 后重试")
        except Exception as e:
            # ★ PTB 漏掉的就是这一条 —— 假死的根因。记全栈，然后继续转。
            consecutive_errors += 1
            backoff = 1.0 if backoff == 0 else min(MAX_BACKOFF, backoff * 1.5)
            log.error(
                f"[polling] 非 TelegramError 异常（连续 {consecutive_errors} 次）: "
                f"{type(e).__name__}: {e}，{backoff:.1f}s 后重试",
                exc_info=True,
            )
        else:
            if consecutive_errors:
                log.info(f"[polling] 已恢复（此前连续 {consecutive_errors} 次失败）")
            consecutive_errors = 0
            backoff = 0.0
            last_success = asyncio.get_running_loop().time()

            for update in updates:
                await app.update_queue.put(update)
                offset = update.update_id + 1

        # 正常情况下 get_updates 自己会挂 POLL_TIMEOUT 秒，这里 sleep(0) 只是保证
        # 万一 TG 持续秒回空列表时，忙循环不会把事件循环饿死
        await asyncio.sleep(backoff)


async def run_app(app, allowed_updates: list[str] | None = None) -> None:
    """接管 Application 的完整生命周期（等价于 run_polling，但轮询走 poll_forever）。"""
    await app.initialize()
    if app.post_init:
        await app.post_init(app)

    # PTB 的 bootstrap 会做这步：确保没有残留 webhook，否则 getUpdates 直接 409
    try:
        await app.bot.delete_webhook(drop_pending_updates=False)
    except Exception as e:
        log.warning(f"[polling] delete_webhook 失败（忽略）: {e}")

    await app.start()
    poll_task = asyncio.create_task(poll_forever(app, allowed_updates), name="ca:polling")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    try:
        # 轮询任务只会因 cancel 或致命错误（InvalidToken）结束，两者都该收摊
        done, _ = await asyncio.wait(
            [poll_task, asyncio.create_task(stop.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if poll_task in done:
            poll_task.result()  # 有异常就在这里炸出来，不再静默吞掉
    finally:
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error(f"[polling] 轮询任务退出异常: {e}", exc_info=True)

        if app.post_shutdown:
            try:
                await app.post_shutdown(app)
            except Exception as e:
                log.warning(f"[polling] post_shutdown 失败: {e}")
        await app.stop()
        await app.shutdown()
        log.info("[polling] 已停止")
