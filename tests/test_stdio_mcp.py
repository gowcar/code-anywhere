"""最小 stdio MCP：握手、列工具（schema 由函数签名生成）、调用工具、出错放在结果里。"""
import unittest

from src.stdio_mcp import StdioMCP


class StdioMCPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mcp = StdioMCP("t")

        @self.mcp.tool()
        async def echo(text: str, times: int = 1, note: str | None = None) -> dict:
            """Repeat text."""
            return {"out": text * times}

        @self.mcp.tool()
        async def boom() -> dict:
            raise ValueError("坏了")

    async def test_protocol(self):
        init = await self.mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
        self.assertIsNone(await self.mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        tools = (await self.mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))["result"]["tools"]
        echo = next(t for t in tools if t["name"] == "echo")
        self.assertEqual(echo["inputSchema"]["required"], ["text"])
        self.assertEqual(echo["inputSchema"]["properties"]["times"], {"type": "integer"})
        self.assertEqual(echo["inputSchema"]["properties"]["note"], {"type": "string"})
        self.assertEqual(echo["description"], "Repeat text.")
        call = await self.mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                      "params": {"name": "echo", "arguments": {"text": "a", "times": 2}}})
        self.assertEqual(call["result"]["structuredContent"], {"out": "aa"})
        error = await self.mcp.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "boom"}})
        self.assertTrue(error["result"]["isError"])
        self.assertIn("坏了", error["result"]["content"][0]["text"])
        self.assertEqual((await self.mcp.handle({"jsonrpc": "2.0", "id": 5, "method": "nope"}))["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
