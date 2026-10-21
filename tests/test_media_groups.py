import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src import bot


def update(mid, caption=None, group='album', topic=206):
    message = SimpleNamespace(
        message_id=mid, media_group_id=group, message_thread_id=topic,
        chat_id=34, chat=SimpleNamespace(type='supergroup'),
        text=None, caption=caption, photo=[object()], document=None,
        video=None, audio=None, voice=None, sticker=None, reply_to_message=None,
        reply_text=AsyncMock())
    return SimpleNamespace(message=message, effective_message=message,
                           effective_user=SimpleNamespace(id=77))


class AlbumTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tasks = []
        def create_task(coro, **kwargs):
            task = asyncio.create_task(coro); self.tasks.append(task); return task
        self.context = SimpleNamespace(bot=object(), application=SimpleNamespace(create_task=create_task))
        self.inject = AsyncMock()
        self.download = AsyncMock(side_effect=lambda message, context, cwd:
                                  (f'.attachments/{message.message_id}.jpg', '.jpg', ''))
        patches = [patch.object(bot, '_media_groups', {}),
                   patch.object(bot, '_project_locks', {}),
                   patch.object(bot, 'MEDIA_GROUP_QUIET_SECONDS', .04),
                   patch.object(bot, 'auth_check', return_value=True),
                   patch.object(bot, '_update_cwd', return_value='/project'),
                   patch.object(bot.projects, 'get_path', return_value='/project'),
                   patch.object(bot.config, 'CHAT_ID', 34),
                   patch.object(bot.backends, 'get_backend', return_value='claude'),
                   patch.object(bot, '_download_attachment', self.download),
                   patch.object(bot, '_find_pane_for_project', AsyncMock(return_value='%1')),
                   patch.object(bot, '_inject_tmux', self.inject),
                   patch.object(bot, 'start_typing'),
                   patch('src.api_server.mark_tg_input'),
                   patch('src.api_server.mark_tg_reply_target'),
                   patch('src.api_server.register_pane_watcher', AsyncMock(return_value=True))]
        for p in patches:
            p.start(); self.addCleanup(p.stop)

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def test_album_one_prompt_order_caption_and_duplicate_update(self):
        first, second = update(1, '这是手机端看到的'), update(2)
        await bot.handle_message(second, self.context)
        await asyncio.sleep(.01)
        await bot.handle_message(first, self.context)
        await bot.handle_message(second, self.context)
        await asyncio.gather(*self.tasks)
        self.inject.assert_awaited_once_with('%1', '@.attachments/1.jpg\n@.attachments/2.jpg\n这是手机端看到的')
        self.assertEqual(self.download.await_count, 2)
        first.message.reply_text.assert_awaited_once()
        second.message.reply_text.assert_not_awaited()

    async def test_same_album_id_different_topics_stay_separate(self):
        await bot.handle_message(update(1, 'A', topic=202), self.context)
        await bot.handle_message(update(2, 'B', topic=206), self.context)
        await asyncio.gather(*self.tasks)
        self.assertEqual(self.inject.await_count, 2)
        self.assertEqual({c.args[1] for c in self.inject.call_args_list},
                         {'@.attachments/1.jpg\nA', '@.attachments/2.jpg\nB'})

    async def test_download_failure_does_not_inject_partial_album(self):
        self.download.side_effect = [('.attachments/1.jpg', '.jpg', ''), (None, None, '下载失败')]
        first = update(1, '说明')
        await bot.handle_message(first, self.context)
        await bot.handle_message(update(2), self.context)
        await asyncio.gather(*self.tasks)
        self.inject.assert_not_awaited()
        self.assertIn('相册未投递', first.message.reply_text.call_args.args[0])

    async def test_single_photo_unchanged_no_wait(self):
        await bot.handle_message(update(1, '单图', group=None), self.context)
        self.inject.assert_awaited_once_with('%1', '@.attachments/1.jpg\n单图')
        self.assertEqual(self.tasks, [])

    async def test_unauthorized_album_not_buffered_or_downloaded(self):
        first = update(1)
        with patch.object(bot, 'auth_check', return_value=False):
            await bot.handle_message(first, self.context)
        self.assertEqual(self.tasks, [])
        self.download.assert_not_awaited()
        self.inject.assert_not_awaited()
