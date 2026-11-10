"""/model 按钮菜单：分组、✅ 当前、（默认）标记、effort 跟随模型、按钮 → 请求、回调落到同一套切换逻辑。"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src import bot, model_menu, model_prefs

CATALOG = [{"slug": "gpt-6-sol", "name": "GPT-6-Sol", "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"], "default": "medium"},
           {"slug": "gpt-6-luna", "name": "GPT-6-Luna", "efforts": ["low", "medium", "high"], "default": "medium"}]


def labels(markup):
    return [[b.text for b in row] for row in markup.inline_keyboard]


def cur(**kw):
    base = dict(engine="claude", model="claude-opus-5-5[1m]", effort="high", effort_is_default=False, permission="full")
    return model_menu.Current(**{**base, **kw})


class RenderTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(model_prefs, "default_model",
                         side_effect=lambda e: "claude-sonnet-5-5" if e == "claude" else "gpt-6-luna")
        p.start()
        self.addCleanup(p.stop)

    def test_layout_groups_checks_and_defaults(self):
        text, markup = model_menu.render("demo", cur(), CATALOG)
        rows = labels(markup)
        self.assertIn("当前：Opus 5.5 · 1M（Claude） · effort high · 权限 Full", text)
        self.assertEqual(rows[0], ["── Claude ──"])
        self.assertEqual(rows[1], ["Fable 5.2", "Fable 5.1"])
        self.assertEqual(rows[2], ["✅ Opus 5.5 · 1M", "Opus 5.5"])
        self.assertEqual(rows[3], ["Sonnet 5.5（默认）", "Haiku 5.5"])
        self.assertEqual(rows[4], ["── Codex (GPT) ──"])
        self.assertEqual(rows[5], ["GPT-6-Sol", "GPT-6-Luna（默认）"])
        self.assertEqual(rows[6], ["── 思考强度 effort ──"])
        self.assertEqual(rows[7], ["low", "medium", "✅ high", "xhigh", "max"])
        self.assertEqual(rows[-1], ["✅ Full", "逐项审批"])
        for row in markup.inline_keyboard:
            for b in row:
                self.assertLessEqual(len(b.callback_data.encode()), 64)

    def test_codex_efforts_follow_model_and_wrap(self):
        _, markup = model_menu.render("demo", cur(engine="codex", model="gpt-6-sol", effort="medium",
                                                  effort_is_default=True, permission="workspace"), CATALOG)
        rows = labels(markup)
        i = rows.index(["── 思考强度 effort ──"])
        self.assertEqual(rows[i + 1:i + 3], [["low", "✅ medium", "high"], ["xhigh", "max", "ultra"]])
        self.assertEqual(rows[-1], ["Full", "✅ 工作区", "只读"])
        self.assertIn(["✅ GPT-6-Sol", "GPT-6-Luna（默认）"], rows)


class CallbackParseTests(unittest.TestCase):
    def test_model_effort_permission_buttons(self):
        req = model_menu.parse_callback("md:m:codex:gpt-6-sol", cur(), CATALOG)
        self.assertEqual((req.engine, req.model, req.effort), ("codex", "gpt-6-sol", None))
        req = model_menu.parse_callback("md:m:claude:sonnet", cur(), CATALOG)
        self.assertEqual((req.engine, req.model), ("claude", "claude-sonnet-5-5"))
        self.assertEqual(model_menu.parse_callback("md:e:max", cur(), CATALOG).effort, "max")
        self.assertEqual(model_menu.parse_callback("md:p:default", cur(), CATALOG).permission, "default")

    def test_noop_for_headers_and_current_values(self):
        for data in ("md:n", "md:m:claude:opus", "md:e:high", "md:p:full"):
            with self.subTest(data=data):
                self.assertIsNone(model_menu.parse_callback(data, cur(), CATALOG))

    def test_unsupported_effort_is_cleared_when_switching_codex_model(self):
        now = cur(engine="codex", model="gpt-6-sol", effort="ultra", permission="full")
        self.assertEqual(model_menu.parse_callback("md:m:codex:gpt-6-luna", now, CATALOG).effort, "")
        now = cur(engine="codex", model="gpt-6-sol", effort="high", permission="full")
        self.assertIsNone(model_menu.parse_callback("md:m:codex:gpt-6-luna", now, CATALOG).effort)

    def test_stale_or_mismatched_buttons_raise(self):
        cases = ["md:m:codex:gpt-gone", "md:m:claude:opus-4", "md:e:ultra", "md:p:workspace"]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                model_menu.parse_callback(data, cur(), CATALOG)


class CallbackFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_button_applies_and_refreshes_menu_in_place(self):
        query = SimpleNamespace(data="md:e:max", answer=AsyncMock(), edit_message_text=AsyncMock(),
                                message=SimpleNamespace(message_thread_id=7, chat_id=1, reply_text=AsyncMock()))
        update = SimpleNamespace(callback_query=query)
        apply = AsyncMock(return_value="已重启终端并接回原会话。")
        with patch.object(bot, "_update_cwd", return_value="/w/demo"), \
             patch.object(model_prefs, "codex_models", return_value=CATALOG), \
             patch.object(bot, "_model_current", return_value=cur()), \
             patch.object(bot, "_apply_model", apply):
            await bot._model_callback(query, update, query.data)
        req = apply.await_args.args[2]
        self.assertEqual((req.engine, req.model, req.effort), (None, None, "max"))
        query.answer.assert_awaited_with("正在切换…")
        text = query.edit_message_text.await_args.args[0]
        self.assertIn("已重启终端并接回原会话。", text)
        self.assertIsNotNone(query.edit_message_text.await_args.kwargs["reply_markup"])

    async def test_current_value_button_only_answers(self):
        query = SimpleNamespace(data="md:p:full", answer=AsyncMock(), edit_message_text=AsyncMock(),
                                message=SimpleNamespace(message_thread_id=7, chat_id=1))
        apply = AsyncMock()
        with patch.object(bot, "_update_cwd", return_value="/w/demo"), \
             patch.object(model_prefs, "codex_models", return_value=CATALOG), \
             patch.object(bot, "_model_current", return_value=cur()), \
             patch.object(bot, "_apply_model", apply):
            await bot._model_callback(query, SimpleNamespace(callback_query=query), query.data)
        apply.assert_not_awaited()
        query.answer.assert_awaited_with("已是当前设置")


if __name__ == "__main__":
    unittest.main()
