"""Telegram API 的 DC 故障转移传输层。

**为什么需要**：`api.telegram.org` 是多 IP 入口，系统 DNS 给哪个纯看运气，而某几个
IP 在国内网络下时通时不通。DNS 一旦解析到失效 IP，`getUpdates` 就一路 `ConnectError`
—— bot 进程还活着、定时任务照常跑、日志看着正常，但**消息接收静默停摆**。

实测（2026-08-13 本机）：`.167.220` 通、`.166.110` 时通时不通、`.167.99` / `.175.50`
不通。同一时刻不同 IP 表现完全不同，指望 DNS 自己修好是没有指望的。

**做法**：包一层 httpx transport ——
- 正常情况完全不干预，走系统 DNS（DNS 对的时候没必要绕）
- 连不上（`ConnectError` / `ConnectTimeout`）时，依次换已知 DC IP 直连重试
- 哪个 IP 成功就**记住它**，后续请求直接用，直到它也失败为止

直连 IP 时必须保住 TLS 正确性：URL 的 host 换成 IP，但 `Host` 头 和 **SNI**
（`extensions["sni_hostname"]`）都保持 `api.telegram.org`，所以证书照常按域名校验
—— 不降级、不 `verify=False`。

源自 runner-coach `bot/tg_transport.py`（2026-08-11 事故的产物），同机同网络环境，
code-anywhere 撞上同一问题后移植过来。
"""

from __future__ import annotations

import logging

import httpx

log = logging.getLogger("code-anywhere.transport")

TG_HOST = "api.telegram.org"

# Telegram 的 API 入口 IP。多给几个，任意一个通就能活。
# 顺序无所谓 —— 第一个连通的会被记住。
_DC_IPS: tuple[str, ...] = (
    "149.154.167.220",
    "149.154.167.99",
    "149.154.175.50",
    "149.154.166.110",
)

_CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout)


class TelegramFailoverTransport(httpx.AsyncHTTPTransport):
    """连不上 api.telegram.org 时自动换 DC IP 直连重试。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._pinned: str | None = None      # 已验证可用的 IP，None=走系统 DNS

    def _pin(self, ip: str | None) -> None:
        if ip != self._pinned:
            log.info("telegram 出口切换: %s → %s", self._pinned or "系统DNS", ip or "系统DNS")
        self._pinned = ip

    @staticmethod
    def _rewrite(request: httpx.Request, ip: str) -> httpx.Request:
        """把请求指向 IP，但 Host 头与 SNI 仍是域名（证书照常校验）。"""
        req = httpx.Request(
            method=request.method,
            url=request.url.copy_with(host=ip),
            headers=request.headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": TG_HOST},
        )
        req.headers["Host"] = TG_HOST
        return req

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != TG_HOST:
            return await super().handle_async_request(request)

        # 已经钉住某个可用 IP → 直接用它
        if self._pinned:
            try:
                return await super().handle_async_request(self._rewrite(request, self._pinned))
            except _CONNECT_ERRORS as e:
                log.warning("telegram 出口 %s 失效（%s），重新选路", self._pinned, e)
                self._pinned = None

        # 先按系统 DNS 试一次；DNS 正常时这条路径零开销
        try:
            resp = await super().handle_async_request(request)
            self._pin(None)
            return resp
        except _CONNECT_ERRORS as first:
            last: Exception = first

        # 系统 DNS 给的 IP 连不上 —— 挨个试已知 DC
        for ip in _DC_IPS:
            try:
                resp = await super().handle_async_request(self._rewrite(request, ip))
                self._pin(ip)
                return resp
            except _CONNECT_ERRORS as e:
                last = e
                continue

        log.error(
            "telegram 全部 %d 个 DC IP 都连不上 —— 大概率是本机网络/代理断了，"
            "不是 DNS 单点问题", len(_DC_IPS),
        )
        raise last


def build_request(**kwargs):
    """构造带故障转移的 PTB HTTPXRequest。

    PTB 没有暴露"换 transport"的口子，但 `httpx_kwargs` 会原样传给
    `httpx.AsyncClient(...)`，而 `transport=` 正是 AsyncClient 的合法参数。
    """
    from telegram.request import HTTPXRequest

    httpx_kwargs = dict(kwargs.pop("httpx_kwargs", {}) or {})
    httpx_kwargs["transport"] = TelegramFailoverTransport(
        retries=1, limits=httpx_kwargs.get("limits", httpx.Limits()),
    )
    return HTTPXRequest(httpx_kwargs=httpx_kwargs, **kwargs)
