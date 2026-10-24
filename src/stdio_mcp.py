"""最小的 stdio MCP 服务端（只实现本机工具服务用到的部分：握手、列出工具、调用工具）。

为什么不用官方 mcp SDK：它为远程 MCP 的 OAuth 拖进 PyJWT[crypto] → cryptography，后者在 Intel Mac 上
没有现成安装包、要编译，2026-10-24 在 Alan 的机器上编译失败导致整个安装失败。我们的 MCP 只在本机经 stdio
给 Claude / Codex 提供几个工具，不需要这些。协议：每行一条 JSON-RPC 2.0 消息（MCP stdio 传输）。
"""
import asyncio
import inspect
import json
import sys
import types
import typing

PROTOCOL_VERSION = "2025-06-18"


def _schema(annotation) -> dict:
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _schema(args[0]) if len(args) == 1 else {}
    return {str: {"type": "string"}, int: {"type": "integer"}, float: {"type": "number"},
            bool: {"type": "boolean"}, dict: {"type": "object"}, list: {"type": "array"}}.get(annotation, {})


class StdioMCP:
    def __init__(self, name: str, version: str = "1.0"):
        self.name, self.version = name, version
        self.tools: dict[str, dict] = {}

    def tool(self):
        """装饰器：把 async 函数登记为工具；参数类型和默认值生成输入 schema，docstring 作为说明。函数本身不变。"""
        def register(fn):
            hints = typing.get_type_hints(fn)
            properties, required = {}, []
            for pname, param in inspect.signature(fn).parameters.items():
                properties[pname] = _schema(hints.get(pname, str))
                if param.default is inspect.Parameter.empty:
                    required.append(pname)
            self.tools[fn.__name__] = {
                "fn": fn,
                "spec": {"name": fn.__name__, "description": inspect.cleandoc(fn.__doc__ or ""),
                         "inputSchema": {"type": "object", "properties": properties, "required": required}},
            }
            return fn
        return register

    async def handle(self, message: dict) -> dict | None:
        method, msg_id, params = message.get("method"), message.get("id"), message.get("params") or {}
        if msg_id is None:            # 通知（如 notifications/initialized）不回复
            return None
        try:
            if method == "initialize":
                result = {"protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                          "capabilities": {"tools": {}}, "serverInfo": {"name": self.name, "version": self.version}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [t["spec"] for t in self.tools.values()]}
            elif method == "tools/call":
                tool = self.tools.get(params.get("name"))
                if not tool:
                    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": f"没有这个工具：{params.get('name')}"}}
                try:
                    value = await tool["fn"](**(params.get("arguments") or {}))
                    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
                    result = {"content": [{"type": "text", "text": text}], "isError": False}
                    if isinstance(value, dict):
                        result["structuredContent"] = value
                except Exception as exc:   # 工具出错按 MCP 约定放在结果里，模型能看到原因
                    result = {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True}
            else:
                return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"不支持的方法：{method}"}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": str(exc)}}
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def run(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=16 << 20)
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        write_lock = asyncio.Lock()
        pending = set()

        async def respond(message):
            reply = await self.handle(message)
            if reply is not None:
                async with write_lock:
                    sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
                    sys.stdout.flush()

        while line := await reader.readline():
            try:
                message = json.loads(line)
            except ValueError:
                continue
            for item in message if isinstance(message, list) else [message]:
                task = asyncio.create_task(respond(item))   # 并发处理：慢工具不挡住其他请求
                pending.add(task)
                task.add_done_callback(pending.discard)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
