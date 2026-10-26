import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src import api_server, backends
from src.image_delivery import linked_images, send_linked_images, send_viewed_attachment


class ImageDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        temporary_roots = patch('src.image_delivery._temporary_roots', ())
        temporary_roots.start()
        self.addCleanup(temporary_roots.stop)
        # macOS 的临时目录 /var 是 /private/var 的软链接；代码按真实路径判断范围
        self.root = Path(self.temp.name).resolve()
        self.picture = self.root / 'screen shot.png'
        self.picture.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        self.text = f'![screen](<{self.picture}>) [again](screen%20shot.png)'

    def test_links_spaces_reference_links_and_dedup(self):
        text = self.text + '\n\n[reference][pic]\n\n[pic]: <screen shot.png>\n'
        self.assertEqual(linked_images(text, str(self.root)), [self.picture])

    def test_excludes_code_remote_missing_and_outside_symlinks(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / 'outside.png'
            outside.write_bytes(b'private')
            (self.root / 'escape.png').symlink_to(outside)
            text = ('`![code](screen%20shot.png)`\n\n'
                    '```md\n![code](screen%20shot.png)\n```\n'
                    '[remote](https://example.com/a.png) [missing](missing.png) '
                    f'[outside]({outside}) [symlink](escape.png)')
            self.assertEqual(linked_images(text, str(self.root)), [])

    async def test_public_code_path_uploads_and_deduplicates_link(self):
        fake = SimpleNamespace(send_document=AsyncMock(return_value=SimpleNamespace(message_id=61)),
                               send_message=AsyncMock())
        text = f'截图路径：`{self.picture}`。再次确认：`{self.picture}`\n' + self.text
        receipts = await send_linked_images(fake, text, str(self.root), 34, 202)
        self.assertEqual(receipts, [(str(self.picture), 61)])
        fake.send_document.assert_awaited_once()
        self.assertEqual(linked_images('`./screen shot.png`', str(self.root)), [self.picture])

    def test_code_path_temp_uri_and_command_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            picture = Path(directory).resolve() / 'claude-passkey-display.png'
            picture.write_bytes(b'image')
            with patch('src.image_delivery._temporary_roots', (Path(directory).resolve(),)):
                self.assertEqual(linked_images(f'截图：`{picture}`', str(self.root)), [picture])
                self.assertEqual(linked_images(f'`{picture.as_uri()}`', str(self.root)), [picture])
                text = f'`cat {picture}`\n\n```sh\n{picture}\n```\n`https://example.com/a.png`'
                self.assertEqual(linked_images(text, str(self.root)), [])
                with patch('src.image_delivery.os.getuid', return_value=-1):
                    self.assertEqual(linked_images(f'`{picture}`', str(self.root)), [])

    async def test_single_path_text_fence_uploads_once(self):
        fake = SimpleNamespace(send_document=AsyncMock(return_value=SimpleNamespace(message_id=62)),
                               send_message=AsyncMock())
        for language in ('', 'text', 'plaintext', 'txt'):
            text = f'二维码文件路径：\n\n```{language}\n{self.picture}\n```'
            self.assertEqual(linked_images(text, str(self.root)), [self.picture])
        receipts = await send_linked_images(fake, text + '\n' + self.text, str(self.root), 34, 202)
        self.assertEqual(receipts, [(str(self.picture), 62)])
        fake.send_document.assert_awaited_once()

    def test_fenced_commands_examples_and_multiple_lines_not_published(self):
        for text in (f'```sh\n{self.picture}\n```',
                     f'```text\ncat {self.picture}\n```',
                     f'```text\n{self.picture}\n{self.picture}\n```',
                     f'```md\n![example]({self.picture})\n```'):
            self.assertEqual(linked_images(text, str(self.root)), [])

    async def test_missing_code_path_visible(self):
        fake = SimpleNamespace(send_document=AsyncMock(), send_message=AsyncMock())
        with self.assertLogs('code-anywhere.images', level='WARNING'):
            await send_linked_images(fake, '`./missing.png`', str(self.root), 34, 202)
        fake.send_document.assert_not_awaited()
        self.assertIn('文件不存在', fake.send_message.call_args.kwargs['text'])

    async def test_upload_original_bytes_and_topic(self):
        seen = []
        async def upload(**kwargs):
            seen.append((kwargs['document'].read(), kwargs['message_thread_id']))
            return SimpleNamespace(message_id=55)
        bot = SimpleNamespace(send_document=AsyncMock(side_effect=upload))
        receipts = await send_linked_images(bot, self.text, str(self.root), 34, 206)
        self.assertEqual(receipts, [(str(self.picture), 55)])
        self.assertEqual(seen, [(self.picture.read_bytes(), 206)])

    async def test_explicit_temp_qr_link_uploads_original(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'seatalk-live-sync-profile-qr.png'
            path.write_bytes(self.picture.read_bytes())
            fake = SimpleNamespace(send_document=AsyncMock(return_value=SimpleNamespace(message_id=60)),
                                   send_message=AsyncMock())
            with patch('src.image_delivery._temporary_roots', (Path(directory).resolve(),)):
                receipts = await send_linked_images(fake, f'[扫码二维码]({path})', str(self.root), 34, 206)
                self.assertEqual(linked_images(f'[QR]({path.as_uri()})', str(self.root)), [path])
                # Internal viewing outside the delivery directory still does not publish.
                self.assertEqual(await send_viewed_attachment(fake, path.as_uri(), str(self.root), 34, 206), [])
            self.assertEqual(receipts, [(str(path), 60)])
            fake.send_document.assert_awaited_once()
            fake.send_message.assert_not_awaited()

    async def test_missing_and_out_of_scope_links_report_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory).resolve() / 'outside.png'; outside.write_bytes(b'image')
            fake = SimpleNamespace(send_document=AsyncMock(), send_message=AsyncMock())
            with self.assertLogs('code-anywhere.images', level='WARNING'):
                receipts = await send_linked_images(
                    fake, f'[missing](missing.png) [outside]({outside})', str(self.root), 34, 206)
            self.assertEqual(receipts, [])
            fake.send_document.assert_not_awaited()
            self.assertEqual(fake.send_message.await_count, 2)
            self.assertIn('文件不存在', fake.send_message.call_args_list[0].kwargs['text'])
            self.assertIn('路径不在', fake.send_message.call_args_list[1].kwargs['text'])

    async def test_temp_wrong_owner_and_project_symlink_cannot_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'other.png'; path.write_bytes(b'image')
            (self.root / 'escape.png').symlink_to(path)
            with patch('src.image_delivery._temporary_roots', (Path(directory).resolve(),)), \
                 patch('src.image_delivery.os.getuid', return_value=-1):
                self.assertEqual(linked_images(f'[QR]({path})', str(self.root)), [])
            with patch('src.image_delivery._temporary_roots', (Path(directory).resolve(),)):
                self.assertEqual(linked_images('[escape](escape.png)', str(self.root)), [])

    async def test_oversize_link_reports_failure(self):
        fake = SimpleNamespace(send_document=AsyncMock(), send_message=AsyncMock())
        with patch('src.image_delivery._max_bytes', 1), \
             self.assertLogs('code-anywhere.images', level='WARNING'):
            await send_linked_images(fake, self.text, str(self.root), 34, 206)
        fake.send_document.assert_not_awaited()
        self.assertIn('超过', fake.send_message.call_args.kwargs['text'])

    async def test_failure_visible_and_second_image_still_sent(self):
        other = self.root / 'second.png'; other.write_bytes(b'image')
        bot = SimpleNamespace(
            send_document=AsyncMock(side_effect=[RuntimeError('offline'), SimpleNamespace(message_id=56)]),
            send_message=AsyncMock())
        with self.assertLogs('code-anywhere.images', level='ERROR'):
            receipts = await send_linked_images(bot, self.text + ' [second](second.png)', str(self.root), 34, 206)
        self.assertEqual(receipts, [(str(other), 56)])
        self.assertIn('图片发送失败', bot.send_message.call_args.kwargs['text'])

    async def test_watcher_uploads_public_reply_once_ignores_internal_image(self):
        def event(kind, uid, **kwargs):
            return {'type': 'event_msg', 'payload': {'type': 'item_completed',
                    'item': {'type': kind, 'id': uid, **kwargs}}}
        reply = event('AgentMessage', 'reply', content=[{'type': 'Text', 'text': self.text}])
        rows = [event('ImageView', 'internal', path=str(self.picture)), reply, reply,
                {'type': 'response_item', 'payload': {'type': 'message', 'role': 'assistant'}}]
        rollout = self.root / 'rollout.jsonl'
        rollout.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        fake = SimpleNamespace(send_document=AsyncMock(return_value=SimpleNamespace(message_id=57)),
                               send_message=AsyncMock())
        sessions = {'image-test': {'cwd': str(self.root), 'backend': 'codex'}}
        with patch.object(api_server, '_bot', fake), \
             patch.object(api_server, 'terminal_sessions', sessions), \
             patch.object(backends, 'get_backend', return_value='codex'), \
             patch.object(api_server, 'broadcast_event'):
            task = asyncio.create_task(api_server._watch_transcript(
                'image-test', str(rollout), 206, 34, start_pos=0))
            try:
                for _ in range(30):
                    if fake.send_message.await_count:
                        break
                    await asyncio.sleep(.05)
                self.assertEqual(fake.send_document.await_count, 1)
                self.assertEqual(fake.send_message.await_count, 1)
            finally:
                task.cancel()
                await task

    async def test_attachment_scope_and_file_uri(self):
        directory = self.root / '.attachments'; directory.mkdir()
        picture = directory / 'current qr.png'; picture.write_bytes(self.picture.read_bytes())
        fake = SimpleNamespace(send_document=AsyncMock(return_value=SimpleNamespace(message_id=58)))
        receipts = await send_viewed_attachment(fake, picture.as_uri(), str(self.root), 34, 206)
        self.assertEqual(receipts, [(str(picture), 58)])
        for path in [self.picture.as_uri(), 'https://example.com/qr.png',
                     'file://remote/qr.png', str(directory / '../screen shot.png')]:
            self.assertEqual(await send_viewed_attachment(fake, path, str(self.root), 34, 206), [])
        (directory / 'escape.png').symlink_to(self.picture)
        self.assertEqual(await send_viewed_attachment(fake, str(directory / 'escape.png'), str(self.root), 34, 206), [])
        self.assertEqual(fake.send_document.await_count, 1)

    async def test_imageview_watcher_sends_attachment_once(self):
        directory = self.root / '.attachments'; directory.mkdir()
        picture = directory / 'qr.png'; picture.write_bytes(self.picture.read_bytes())
        event = {'type': 'event_msg', 'payload': {'type': 'item_completed', 'item': {
            'type': 'ImageView', 'id': 'view-qr', 'path': picture.as_uri()}}}
        rollout = self.root / 'rollout.jsonl'
        rollout.write_text((json.dumps(event) + '\n') * 2)
        fake = SimpleNamespace(send_document=AsyncMock(return_value=SimpleNamespace(message_id=59)))
        with patch.object(api_server, '_bot', fake), \
             patch.object(api_server, 'terminal_sessions', {'image-test': {'cwd': str(self.root), 'backend': 'codex'}}), \
             patch.object(backends, 'get_backend', return_value='codex'), \
             patch.object(api_server, 'broadcast_event'):
            task = asyncio.create_task(api_server._watch_transcript('image-test', str(rollout), 206, 34, start_pos=0))
            try:
                for _ in range(30):
                    if fake.send_document.await_count:
                        break
                    await asyncio.sleep(.05)
                self.assertEqual(fake.send_document.await_count, 1)
                self.assertEqual(fake.send_document.call_args.kwargs['filename'], 'qr.png')
                self.assertEqual(fake.send_document.call_args.kwargs['message_thread_id'], 206)
            finally:
                task.cancel()
                await task
