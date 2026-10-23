import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.types import Message

from channels.telegram import capture_message, handle_message


def message(**changes):
    data = {
        'message_id': 10,
        'date': 1792404000,
        'chat': {'id': 100, 'type': 'private', 'first_name': 'Olga'},
        'from': {'id': 100, 'is_bot': False, 'first_name': 'Olga', 'username': 'demo_owner'},
        'text': 'A note from Telegram',
    }
    data.update(changes)
    return Message.model_validate(data)


def channel_forward(receipt_id=10):
    return message(message_id=receipt_id, forward_origin={
        'type': 'channel', 'date': 1792317600,
        'chat': {'id': -100123, 'type': 'channel', 'title': 'Demo Channel'},
        'message_id': 77,
    })


class TelegramCaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.vault = Path(self.directory.name) / 'vault'

    def record(self, incoming):
        result = capture_message(incoming, self.vault)
        return result, json.loads(result.path.read_text())

    def test_direct_message_preserves_text_author_and_original_message(self):
        incoming = message(text='  Café\r\nПривет\n')
        _, record = self.record(incoming)
        self.assertEqual(record['text'], incoming.text)
        self.assertEqual(record['author_hint']['id'], '100')
        self.assertEqual(record['author_hint']['handle'], 'demo_owner')
        self.assertIsNone(record['forwarded_by'])
        self.assertEqual(record['published_at'], incoming.date.isoformat())
        self.assertEqual(record['channel_metadata']['message_id'], 10)
        self.assertNotIn('token', record['channel_metadata'])
        self.assertNotIn('text', record['channel_metadata'])

    def test_channel_forward_keeps_source_separate_from_forwarder(self):
        _, record = self.record(channel_forward())
        self.assertEqual(record['source_id'], 'telegram:channel:-100123')
        self.assertEqual(record['source_name'], 'Demo Channel')
        self.assertEqual(record['message_id'], '77')
        self.assertEqual(record['forwarded_by'], {'name': 'Olga', 'handle': 'demo_owner', 'url': 'tg://user?id=100'})
        self.assertIsNone(record['author_hint'])
        self.assertEqual(record['channel_metadata']['forward_origin']['type'], 'channel')
        self.assertNotIn('text', record['channel_metadata'])
        self.assertEqual(record['published_at'], channel_forward().forward_origin.date.isoformat())

    def test_same_channel_post_reuses_record_across_forward_deliveries(self):
        first, _ = self.record(channel_forward(receipt_id=10))
        second, _ = self.record(channel_forward(receipt_id=11))
        self.assertTrue(second.duplicate)
        self.assertEqual(first.path, second.path)

    def test_same_words_as_new_direct_message_are_not_a_retry(self):
        first, _ = self.record(message(message_id=10))
        second, _ = self.record(message(message_id=11))
        self.assertFalse(second.duplicate)
        self.assertNotEqual(first.path, second.path)

    def test_forwarded_user_is_author_and_sender_is_forwarder(self):
        incoming = message(forward_origin={
            'type': 'user', 'date': 1792317600,
            'sender_user': {'id': 200, 'is_bot': False, 'first_name': 'Original Author'},
        })
        _, record = self.record(incoming)
        self.assertEqual(record['author_hint']['id'], '200')
        self.assertEqual(record['forwarded_by'], {'name': 'Olga', 'handle': 'demo_owner', 'url': 'tg://user?id=100'})
        self.assertEqual(record['message_id'], 'delivery:100:10')

    def test_hidden_sender_name_is_a_hint_without_invented_id(self):
        incoming = message(forward_origin={
            'type': 'hidden_user', 'date': 1792317600, 'sender_user_name': 'Hidden Author',
        })
        _, record = self.record(incoming)
        self.assertEqual(record['author_hint'], {'name': 'Hidden Author'})
        self.assertEqual(record['message_id'], 'delivery:100:10')

    def test_link_preview_preserves_text_and_serializable_metadata(self):
        text = "Отчёт об AI за год.\nhttps://example.com/report.pdf\nhttps://youtu.be/demo"
        previews = [
            {"url": "https://example.com/report.pdf"},
            {"is_disabled": False, "url": "https://example.com/report.pdf", "prefer_large_media": True},
        ]
        for index, preview in enumerate(previews):
            with self.subTest(preview=preview):
                incoming = message(message_id=50 + index, text=text, link_preview_options=preview)
                _, record = self.record(incoming)
                self.assertEqual(record["text"], text)
                self.assertNotIn("text", record["channel_metadata"])
                self.assertEqual(record["channel_metadata"]["link_preview_options"], preview)

    def test_forwarded_post_with_link_preview_keeps_provenance(self):
        payload = channel_forward().model_dump(mode="json", exclude_none=True)
        payload["text"] = "Отчёт об AI: https://example.com/report.pdf"
        payload["link_preview_options"] = {"url": "https://example.com/report.pdf"}
        _, record = self.record(Message.model_validate(payload))
        self.assertEqual(record["source_name"], "Demo Channel")
        self.assertEqual(record["forwarded_by"]["url"], "tg://user?id=100")
        self.assertEqual(record["channel_metadata"]["link_preview_options"]["url"], "https://example.com/report.pdf")

    def test_media_with_caption_is_not_silently_saved_as_text(self):
        incoming = message(text=None, caption='A photo caption', photo=[{
            'file_id': 'demo-file', 'file_unique_id': 'demo-unique', 'width': 100, 'height': 100,
        }])
        with self.assertRaisesRegex(ValueError, 'text messages only'):
            capture_message(incoming, self.vault)
        self.assertFalse(self.vault.exists())


class TelegramHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        web_patch = patch.dict(os.environ, {"WEB_FETCH": "off"})
        web_patch.start()
        self.addCleanup(web_patch.stop)
        model_patch = patch("channels.telegram.client_from_environment", return_value=None)
        model_patch.start()
        self.addCleanup(model_patch.stop)
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.vault = Path(self.directory.name) / 'vault'

    async def reply(self, incoming, owner_id):
        with patch.object(Message, 'answer', new_callable=AsyncMock) as answer:
            await handle_message(incoming, self.vault, owner_id)
            return answer

    async def test_setup_start_returns_user_id_without_capturing(self):
        answer = await self.reply(message(text='/start'), None)
        self.assertIn('user ID is 100', answer.call_args.args[0])
        self.assertFalse(self.vault.exists())

    async def test_setup_mode_does_not_capture_notes(self):
        answer = await self.reply(message(), None)
        self.assertIn('disabled', answer.call_args.args[0])
        self.assertFalse(self.vault.exists())

    async def test_non_owner_cannot_capture(self):
        answer = await self.reply(message(), 999)
        self.assertIn('configured owner', answer.call_args.args[0])
        self.assertFalse(self.vault.exists())

    async def test_group_messages_are_ignored(self):
        answer = await self.reply(message(chat={'id': -100, 'type': 'group', 'title': 'Demo Group'}), 100)
        answer.assert_not_awaited()
        self.assertFalse(self.vault.exists())

    async def test_owner_receives_ack_only_after_record_exists(self):
        answer = await self.reply(message(), 100)
        self.assertTrue(list((self.vault / 'raw').glob('*.json')))
        self.assertTrue(answer.await_args_list[0].args[0].startswith('Captured.'))
        answer = await self.reply(message(), 100)
        self.assertTrue(answer.await_args_list[0].args[0].startswith('Already captured.'))

    async def test_link_preview_receives_successful_ack(self):
        incoming = message(text="Отчёт: https://example.com", link_preview_options={"url": "https://example.com"})
        answer = await self.reply(incoming, 100)
        self.assertTrue(answer.await_args_list[0].args[0].startswith("Captured."))
        self.assertEqual(len(list((self.vault / "raw").glob("*.json"))), 1)

    async def test_preparation_reports_links_after_durable_capture(self):
        incoming = message(text="Отчёт https://example.com/report.pdf")
        answer = await self.reply(incoming, 100)
        self.assertEqual(answer.await_count, 2)
        self.assertTrue(answer.await_args_list[0].args[0].startswith("Captured."))
        self.assertIn("1 link(s)", answer.await_args_list[1].args[0])
        self.assertTrue(list((self.vault / "documents").glob("*.json")))

    async def test_parse_failure_preserves_raw_and_reports_partial_success(self):
        with (
            patch("channels.telegram.parse_capture", side_effect=ValueError("private details")),
            self.assertLogs("channels.telegram", level="ERROR"),
        ):
            answer = await self.reply(message(), 100)
        self.assertTrue(answer.await_args_list[0].args[0].startswith("Captured."))
        self.assertIn("original is saved", answer.await_args_list[1].args[0])
        self.assertNotIn("private details", answer.await_args_list[1].args[0])
        self.assertTrue(list((self.vault / "raw").glob("*.json")))

    async def test_whitespace_note_gets_a_capture_error_instead_of_crashing(self):
        with self.assertLogs("channels.telegram", level="ERROR"):
            answer = await self.reply(message(text="   "), 100)
        self.assertIn("Could not capture", answer.call_args.args[0])
        self.assertFalse(self.vault.exists())

    async def test_storage_error_does_not_claim_success(self):
        with (
            patch('channels.telegram.capture_message', side_effect=OSError('private diagnostic details')),
            self.assertLogs('channels.telegram', level='ERROR') as logs,
        ):
            answer = await self.reply(message(), 100)
        self.assertIn('Could not capture', answer.call_args.args[0])
        self.assertIn('Error category: OSError', answer.call_args.args[0])
        self.assertNotIn('private diagnostic details', '\n'.join(logs.output))
        self.assertFalse(self.vault.exists())

    async def test_unknown_command_is_not_saved(self):
        answer = await self.reply(message(text='/refresh memory'), 100)
        self.assertIn('not implemented', answer.call_args.args[0])
        self.assertFalse(self.vault.exists())

    async def test_forwarded_command_text_is_saved_as_content(self):
        incoming = channel_forward().model_copy(update={'text': '/start'})
        answer = await self.reply(incoming, 100)
        self.assertTrue(answer.await_args_list[0].args[0].startswith('Captured.'))


if __name__ == '__main__':
    unittest.main()
