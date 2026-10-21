"""视频附件只落盘、不交给模型（移植自 nas），含相册混发场景。"""
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from src import bot, recon, api_server


class VideoAttachmentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = self.tmp.name
        async def download(path, **kwargs): Path(path).write_bytes(b'video-test')
        self.tg_file = SimpleNamespace(file_size=10, download_to_drive=AsyncMock(side_effect=download))
        self.video = SimpleNamespace(file_size=10, file_name='clip.mp4', file_unique_id='unique',
                                     mime_type='video/mp4', get_file=AsyncMock(return_value=self.tg_file))
        self.msg = SimpleNamespace(photo=None, document=None, video=self.video, video_note=None,
            audio=None, voice=None, sticker=None, text=None, caption='请看看', reply_to_message=None,
            message_thread_id=12, chat_id=34, chat=SimpleNamespace(type='supergroup'), reply_text=AsyncMock())

    async def test_video_is_saved_and_reported_without_touching_model(self):
        update = SimpleNamespace(effective_user=SimpleNamespace(id=77), message=self.msg)
        with patch.object(bot, 'auth_check', return_value=True), \
             patch.object(bot.projects, 'get_path', return_value=self.cwd), \
             patch.object(bot.config, 'CHAT_ID', 34), \
             patch.object(bot, '_find_pane_for_project', AsyncMock()) as find, \
             patch.object(bot, '_inject_tmux', AsyncMock()) as inject:
            await bot._handle_message(update, SimpleNamespace())
        files = list((Path(self.cwd)/'.attachments/videos').iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].read_bytes(), b'video-test')
        self.assertIn(str(files[0]), self.msg.reply_text.call_args.args[0])
        self.assertIn('未提交模型', self.msg.reply_text.call_args.args[0])
        find.assert_not_awaited(); inject.assert_not_awaited()
        recon.cleanup_attachments(self.cwd, keep=0)
        self.assertTrue(files[0].exists())

    async def test_document_video_and_video_note_classification(self):
        self.msg.video = None; self.msg.document = self.video
        path, ext, err = await bot._download_attachment(self.msg, None, self.cwd)
        self.assertFalse(err); self.assertTrue(path.startswith('.attachments/videos/'))
        self.msg.document = None; self.msg.video_note = self.video
        self.assertTrue(bot._is_video_attachment(self.msg))
        self.msg.video_note = None
        self.msg.document = SimpleNamespace(file_name='source.ts', mime_type='text/plain')
        self.assertFalse(bot._is_video_attachment(self.msg))

    async def test_oversized_video_reports_not_saved_without_download(self):
        self.video.file_size = bot.MAX_VIDEO_SIZE + 1
        path, ext, err = await bot._download_attachment(self.msg, None, self.cwd)
        self.assertIsNone(path); self.assertIn('未保存', err)
        self.video.get_file.assert_not_awaited()

    async def test_partial_download_is_removed(self):
        async def fail(path, **kwargs):
            Path(path).write_bytes(b'partial'); raise OSError('download interrupted')
        self.tg_file.download_to_drive.side_effect=fail
        path, ext, err = await bot._download_attachment(self.msg, None, self.cwd)
        self.assertIsNone(path); self.assertIn('下载文件失败', err)
        self.assertEqual(list((Path(self.cwd)/'.attachments/videos').iterdir()), [])

    async def test_nonvideo_documents_still_reach_model(self):
        self.msg.video = None
        self.msg.document = self.video
        self.video.file_name = 'notes.txt'
        self.video.mime_type = 'text/plain'
        self.msg.message_id = 9
        update = SimpleNamespace(effective_user=SimpleNamespace(id=77), message=self.msg)
        with patch.object(bot, 'auth_check', return_value=True), \
             patch.object(bot.projects, 'get_path', return_value=self.cwd), \
             patch.object(bot.config, 'CHAT_ID', 34), \
             patch.object(bot.backends, 'get_backend', return_value='claude'), \
             patch.object(bot, '_find_pane_for_project', AsyncMock(return_value='%1')), \
             patch.object(api_server, 'register_pane_watcher', AsyncMock(return_value=True)), \
             patch.object(bot, 'start_typing'), \
             patch.object(bot, '_inject_tmux', AsyncMock()) as inject:
            await bot._handle_message(update, SimpleNamespace(bot=None))
        inject.assert_awaited_once()
        self.assertIn('notes.txt', inject.call_args.args[1])
        self.assertIn('请看看', inject.call_args.args[1])
        self.assertIn('正在读取分析', self.msg.reply_text.call_args.args[0])

    def _photo_part(self, mid):
        async def download(path, **kwargs): Path(path).write_bytes(b'img')
        tg = SimpleNamespace(file_size=3, download_to_drive=AsyncMock(side_effect=download))
        photo = SimpleNamespace(file_size=3, file_unique_id=f'p{mid}', get_file=AsyncMock(return_value=tg))
        return SimpleNamespace(**{**vars(self.msg), 'video': None, 'photo': [photo], 'message_id': mid, 'caption': None})

    async def _deliver_album(self, parts):
        self.msg.message_id = 1
        self.msg.photo = None
        update = SimpleNamespace(effective_user=SimpleNamespace(id=77), message=self.msg)
        with patch.object(bot, 'auth_check', return_value=True), \
             patch.object(bot.projects, 'get_path', return_value=self.cwd), \
             patch.object(bot.config, 'CHAT_ID', 34), \
             patch.object(bot.backends, 'get_backend', return_value='claude'), \
             patch.object(bot, '_find_pane_for_project', AsyncMock(return_value='%1')), \
             patch.object(api_server, 'register_pane_watcher', AsyncMock(return_value=True)), \
             patch.object(bot, 'start_typing'), \
             patch.object(bot, '_inject_tmux', AsyncMock()) as inject:
            await bot._handle_message(update, SimpleNamespace(bot=None), parts)
        return inject

    async def test_album_mixed_photo_and_video_sends_only_photo(self):
        inject = await self._deliver_album([self.msg, self._photo_part(2)])
        inject.assert_awaited_once()
        self.assertIn('photo_p2.jpg', inject.call_args.args[1])
        self.assertNotIn('clip.mp4', inject.call_args.args[1])
        replies = [c.args[0] for c in self.msg.reply_text.await_args_list]
        self.assertTrue(any('视频已保存' in r and 'clip.mp4' in r for r in replies))

    async def test_album_of_only_videos_does_not_reach_model(self):
        inject = await self._deliver_album([self.msg])
        inject.assert_not_awaited()
        self.assertIn('视频已保存', self.msg.reply_text.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
