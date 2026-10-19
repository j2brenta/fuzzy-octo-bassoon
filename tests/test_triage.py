import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

from aiogram.types import Message

from channels.telegram import handle_message
from engine.llm import BackboardModel, ModelError, ModelOutputError, ModelReply, client_from_environment
from engine.triage import TriageError, triage_capture, validate_labels
from store.raw import capture_text
from tests.test_telegram import message

LABELS = {'keep': True, 'kind': 'post', 'durability_class': 'news', 'split_digest': False, 'reason': 'A substantive AI development is worth keeping.'}


class FakeModel:
    provider = 'backboard'
    model_provider = 'fixture'
    model_name = 'fixture-model'

    def __init__(self, replies=None):
        self.replies = list(replies or [ModelReply(dict(LABELS), usage={'total_tokens': 12})])
        self.calls = []

    def complete_json(self, system_prompt, content):
        self.calls.append((system_prompt, content))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeResponse:
    def __init__(self, data):
        self.body = json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size):
        return self.body[:size]


class BackboardTests(unittest.TestCase):
    def test_request_uses_json_mode_without_memory_search_or_prior_thread(self):
        response = FakeResponse({'status': 'COMPLETED', 'content': json.dumps(LABELS), 'model_provider': 'openai', 'model_name': 'gpt-4o-mini', 'message_id': 'fixture-request', 'input_tokens': 10, 'output_tokens': 20})
        client = BackboardModel('fixture-secret')
        with patch('engine.llm.urlopen', return_value=response) as request:
            reply = client.complete_json('system instructions', 'source text')
        sent = request.call_args.args[0]
        payload = json.loads(sent.data)
        self.assertEqual(sent.full_url, 'https://app.backboard.io/api/threads/messages')
        self.assertEqual(sent.get_header('X-api-key'), 'fixture-secret')
        self.assertTrue(payload['json_output'])
        self.assertEqual(payload['memory'], 'off')
        self.assertEqual(payload['web_search'], 'off')
        self.assertNotIn('thread_id', payload)
        self.assertNotIn('fixture-secret', repr(client))
        self.assertNotIn('fixture-secret', sent.data.decode())
        self.assertEqual(reply.data, LABELS)
        self.assertEqual(reply.usage, {'input_tokens': 10, 'output_tokens': 20})

    def test_authentication_error_does_not_expose_response_or_key(self):
        error = HTTPError('https://example.com', 401, 'private diagnostic', {}, io.BytesIO(b'private response'))
        with patch('engine.llm.urlopen', side_effect=error):
            with self.assertRaises(ModelError) as failure:
                BackboardModel('fixture-secret').complete_json('system', 'source')
        self.assertIn('rejected', str(failure.exception))
        self.assertNotIn('private', str(failure.exception))
        self.assertNotIn('fixture-secret', str(failure.exception))

    def test_non_json_model_output_has_distinct_retryable_error(self):
        with patch('engine.llm.urlopen', return_value=FakeResponse({'content': 'not JSON'})):
            with self.assertRaises(ModelOutputError):
                BackboardModel('fixture').complete_json('system', 'source')

    def test_configuration_requires_explicit_enable_and_key(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(client_from_environment())
        with patch.dict(os.environ, {'TRIAGE_PROVIDER': 'backboard'}, clear=True):
            with self.assertRaisesRegex(ModelError, 'BACKBOARD_API_KEY'):
                client_from_environment()
        with patch.dict(os.environ, {'TRIAGE_PROVIDER': 'backboard', 'BACKBOARD_API_KEY': 'fixture', 'BACKBOARD_MODEL': 'chosen-model'}, clear=True):
            self.assertEqual(client_from_environment().model_name, 'chosen-model')


class TriageTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.vault = Path(self.directory.name)
        self.raw = capture_text('Отчёт об AI https://example.com', self.vault, channel='file', source_id='fixture', source_name='Fixture')

    def test_model_judgment_is_saved_without_duplicating_source_text(self):
        before = self.raw.path.read_bytes()
        client = FakeModel()
        result = triage_capture(self.raw.path, client)
        saved = json.loads(result.path.read_text())
        self.assertEqual(saved['labels'], LABELS)
        self.assertEqual(saved['provider'], 'backboard')
        self.assertEqual(saved['model_name'], 'fixture-model')
        self.assertEqual(saved['prompt_version'], 'triage-v2')
        self.assertNotIn('text', saved)
        self.assertEqual(self.raw.path.read_bytes(), before)
        self.assertEqual(client.calls[0][1], json.loads(before)['text'])

    def test_successful_retry_uses_cache_without_new_model_calls(self):
        client = FakeModel()
        first = triage_capture(self.raw.path, client)
        original = first.path.read_bytes()
        second = triage_capture(self.raw.path, client)
        self.assertTrue(second.reused)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(second.path.read_bytes(), original)

    def test_model_change_invalidates_cache(self):
        triage_capture(self.raw.path, FakeModel())
        client = FakeModel()
        client.model_name = 'different-model'
        result = triage_capture(self.raw.path, client)
        self.assertFalse(result.reused)
        self.assertEqual(len(client.calls), 1)

    def test_invalid_labels_get_only_one_retry(self):
        client = FakeModel([ModelReply({'keep': 'yes'}), ModelReply(dict(LABELS))])
        result = triage_capture(self.raw.path, client)
        self.assertEqual(result.labels, LABELS)
        self.assertEqual(len(client.calls), 2)

    def test_invalid_json_can_retry_once(self):
        client = FakeModel([ModelOutputError('invalid output'), ModelReply(dict(LABELS))])
        self.assertTrue(triage_capture(self.raw.path, client).labels['keep'])
        self.assertEqual(len(client.calls), 2)

    def test_two_invalid_outputs_leave_raw_and_no_success_record(self):
        before = self.raw.path.read_bytes()
        client = FakeModel([ModelReply({}), ModelReply({})])
        with self.assertRaisesRegex(TriageError, 'twice'):
            triage_capture(self.raw.path, client)
        self.assertEqual(self.raw.path.read_bytes(), before)
        self.assertFalse((self.vault / 'triage').exists())
        self.assertEqual(len(client.calls), 2)

    def test_service_error_does_not_trigger_blind_paid_retries(self):
        client = FakeModel([ModelError('service unavailable')])
        with self.assertRaises(ModelError):
            triage_capture(self.raw.path, client)
        self.assertTrue(self.raw.path.exists())
        self.assertEqual(len(client.calls), 1)

    def test_skip_decision_does_not_delete_source(self):
        labels = {**LABELS, 'keep': False, 'kind': 'other', 'durability_class': 'ephemeral', 'reason': 'Only a greeting.'}
        triage_capture(self.raw.path, FakeModel([ModelReply(labels)]))
        self.assertTrue(self.raw.path.exists())

    def test_substantive_conversation_is_a_supported_content_type(self):
        labels = {**LABELS, 'kind': 'conversation', 'durability_class': 'reference'}
        self.assertEqual(validate_labels(labels)['kind'], 'conversation')

    def test_labels_require_real_booleans_and_consistent_digest_flag(self):
        for changes in [{'keep': 'true'}, {'kind': []}, {'reason': ''}, {'split_digest': True}]:
            with self.subTest(changes=changes):
                with self.assertRaises(TriageError):
                    validate_labels({**LABELS, **changes})


class TelegramTriageTests(unittest.IsolatedAsyncioTestCase):
    async def test_enabled_triage_replies_after_capture_and_parse_then_reuses_result(self):
        with TemporaryDirectory() as folder:
            extraction = {"claims": [{"statement": "A note from Telegram", "evidence_passage_id": 1, "kind": "opinion", "durability_class": "reference", "confidence": 0.8}]}
            client = FakeModel([ModelReply(dict(LABELS)), ModelReply(extraction)])
            with patch('channels.telegram.client_from_environment', return_value=client), patch.object(Message, 'answer', new_callable=AsyncMock) as answer:
                await handle_message(message(), Path(folder), 100)
                self.assertEqual(answer.await_count, 4)
                self.assertTrue(answer.await_args_list[0].args[0].startswith('Captured.'))
                self.assertTrue(answer.await_args_list[1].args[0].startswith('Prepared:'))
                self.assertIn('AI triage: keep | post | news', answer.await_args_list[2].args[0])
                answer.reset_mock()
                await handle_message(message(), Path(folder), 100)
                self.assertIn('Cached triage', answer.await_args_list[2].args[0])
                self.assertEqual(len(client.calls), 2)

    async def test_provider_failure_keeps_capture_and_prepared_document(self):
        with TemporaryDirectory() as folder:
            vault = Path(folder)
            client = FakeModel([ModelError('Backboard is unavailable.')])
            with patch('channels.telegram.client_from_environment', return_value=client), patch.object(Message, 'answer', new_callable=AsyncMock) as answer:
                await handle_message(message(), vault, 100)
                self.assertIn('AI triage is pending', answer.await_args_list[2].args[0])
            self.assertTrue(list((vault / 'raw').glob('*.json')))
            self.assertTrue(list((vault / 'documents').glob('*.json')))
            self.assertFalse((vault / 'triage').exists())


if __name__ == '__main__':
    unittest.main()
