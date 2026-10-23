"""共享浏览器池：租用互斥、归还、接管前置条件、中转机脚本保存与恢复原服务。"""
import unittest
from unittest.mock import AsyncMock, patch

from src import browser_pool as pool


class BrowserPoolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        pool._leases.clear()
        pool._help.clear()
        self.patches = [patch.object(pool, "start_slot", AsyncMock()),
                        patch.object(pool, "_version", AsyncMock(return_value=None)),
                        patch.object(pool, "export_identity", AsyncMock()),
                        patch.object(pool, "_owner", AsyncMock(side_effect=lambda c: {"project": f"p{c}", "session_id": f"claude:%1:{c.replace('-', ':')}"})),
                        patch.object(pool, "RELEASE_GRACE", 0)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    async def test_each_client_gets_its_own_slot_and_third_waits(self):
        a = await pool.acquire("1-1")
        self.assertEqual(await pool.acquire("1-1"), a)   # 同一会话重连沿用原槽位
        b = await pool.acquire("2-2")
        self.assertNotEqual(a, b)
        with self.assertRaisesRegex(RuntimeError, "都在使用中"):
            await pool.acquire("3-3")

    async def test_disconnected_lease_is_released(self):
        slot = await pool.acquire("1-1")
        pool._schedule_release(slot, "1-1")
        await pool._leases[slot]["release_task"]
        self.assertNotIn(slot, pool._leases)
        pool.export_identity.assert_awaited_with(slot)

    async def test_lease_kept_while_user_is_operating(self):
        slot = await pool.acquire("1-1")
        pool._help["h"] = {"slot": slot, "status": "waiting"}
        pool._schedule_release(slot, "1-1")
        await pool._leases[slot]["release_task"]
        self.assertIn(slot, pool._leases)

    async def test_help_needs_a_lease(self):
        with patch.object(pool, "help_available", return_value=True):
            result = await pool.request_help("claude:%9:5:5", "请登录")
        self.assertEqual(result["status"], "not_leased")

    async def test_new_tab_refused_at_limit(self):
        create = '{"id": 7, "method": "Target.createTarget", "params": {"url": "about:blank"}}'
        with patch.object(pool, "_pages", AsyncMock(return_value=pool.MAX_TABS)):
            refusal = await pool._tab_limit_refusal(1, create)
            self.assertIn("最多", refusal)
            self.assertIsNone(await pool._tab_limit_refusal(1, '{"id": 8, "method": "Page.navigate"}'))
        with patch.object(pool, "_pages", AsyncMock(return_value=1)):
            self.assertIsNone(await pool._tab_limit_refusal(1, create))

    def test_slot_count_has_hard_cap(self):
        self.assertLessEqual(pool.SLOTS, pool.MAX_SLOTS)

    async def test_direct_mode_runs_novnc_locally_without_ssh(self):
        started = []
        class Proc:
            returncode = None
            def terminate(self): pass
        async def fake_exec(*args, **kwargs):
            started.append(args)
            return Proc()
        pool._leases[1] = {"client": "5-5", "session_id": "claude:%9:5:5", "cwd": "/w", "conns": 1}
        bot = AsyncMock()
        bot.send_message.return_value.message_id = 1
        with patch.object(pool, "RELAY_HOST", ""), patch.object(pool, "RELAY_LISTEN", "0.0.0.0:6801"), \
             patch.object(pool, "RELAY_PUBLIC_URL", "https://me.example:6801"), \
             patch.object(pool, "RELAY_WEBSOCKIFY", "websockify --web /usr/share/novnc"), \
             patch.object(pool.asyncio, "create_subprocess_exec", fake_exec), patch.object(pool.asyncio, "sleep", AsyncMock()), \
             patch.object(pool, "_relay", AsyncMock()) as relay, \
             patch("src.projects.get_topic", return_value=7), patch("src.api_server._bot", bot):
            result = await pool.request_help("claude:%9:5:5", "请登录")
        self.assertEqual(result["status"], "waiting")
        self.assertEqual(started[1], ("websockify", "--web", "/usr/share/novnc", "0.0.0.0:6801", "127.0.0.1:5921"))
        self.assertFalse(any(a[0] == "ssh" for a in started))
        relay.assert_not_awaited()
        pool.gate(1).set()

    def test_relay_script_saves_and_restores_existing_service(self):
        with patch.object(pool, "RELAY_LISTEN", "10.0.1.188:6801"), patch.object(pool, "RELAY_WEBSOCKIFY", "ws/run --ssl-only"), \
             patch.object(pool, "RELAY_RESTORE", "ws/run --ssl-only 10.0.1.188:6801 10.0.1.188:5900"):
            start, stop = pool._relay_script("start"), pool._relay_script("stop")
        self.assertIn("-iTCP@10.0.1.188:6801", start)
        self.assertIn("nc -z 127.0.0.1 16801", start)      # 隧道不通就报错，不起 noVNC
        self.assertIn("saved.flag", start)
        self.assertIn("ws/run --ssl-only 10.0.1.188:6801 127.0.0.1:16801", start)
        self.assertIn("nohup ws/run --ssl-only 10.0.1.188:6801 10.0.1.188:5900", stop)
        with patch.object(pool, "RELAY_RESTORE", ""):
            self.assertIn("stopped-original", pool._relay_script("stop"))


if __name__ == "__main__":
    unittest.main()
