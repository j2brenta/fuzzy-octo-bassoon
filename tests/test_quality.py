from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.types import Message

from channels.telegram import handle_message
from engine.extract import extract_capture, validate_claims
from engine.judge import JudgeError
from engine.llm import ModelError, ModelReply
from engine.quality import QualityError, quality_capture
from store.raw import capture_text
from tests.test_extract import TEXT, ROWS, response
from tests.test_judge import review_data
from tests.test_telegram import message
from tests.test_triage import FakeModel, LABELS


class QualityTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.vault = Path(folder.name)
        self.raw = capture_text(TEXT, self.vault, channel='file', source_id='fixture:quality', source_name='Synthetic quality fixture')
        self.original = extract_capture(self.raw.path, FakeModel([response()]))
        self.initial_review = review_data(self.original.claims)
        self.initial_review['claims'][0].update(verdict='needs_revision', issues=['wrong_kind'])
        self.changed_rows = deepcopy(ROWS)
        self.changed_rows[0]['statement'] = 'Explanations should remain simple.'
        self.changed_claims = validate_claims({'claims': self.changed_rows}, TEXT, self.raw.item_id, self.original.claims[0]['evidence']['section_id'])
        self.final_review = review_data(self.changed_claims)

    def client(self, final=None):
        client = FakeModel([ModelReply(self.initial_review), response(self.changed_rows), ModelReply(final or self.final_review)])
        client.model_name = 'fixture-strong-model'
        return client

    def test_one_correction_is_judged_and_preserves_initial_files(self):
        raw_before = self.raw.path.read_bytes()
        extraction_before = self.original.path.read_bytes()
        client = self.client()
        result = quality_capture(self.raw.path, client)
        self.assertTrue(result.corrected)
        self.assertEqual(result.reviewed.review['status'], 'accepted')
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(result.claims, self.changed_claims)
        self.assertEqual(self.raw.path.read_bytes(), raw_before)
        self.assertEqual(self.original.path.read_bytes(), extraction_before)
        self.assertEqual(result.extraction_path.parent.name, 'repairs')
        self.assertEqual(result.reviewed.path.parent.name, 'repair-reviews')
        self.assertEqual(json.loads((self.vault / 'reviews' / self.raw.path.name).read_text())['review']['status'], 'needs_revision')
        self.assertIn('review', json.loads(client.calls[1][1]))
        self.assertIn('previous_claims', json.loads(client.calls[1][1]))
        payload = json.loads(client.calls[1][1])
        self.assertEqual(payload['previous_claims'][0]['claim_id'], 'C1')
        self.assertEqual(payload['previous_claims'][0]['evidence_passage_ids'], [1])
        self.assertNotIn('evidence', payload['previous_claims'][0])
        self.assertEqual(payload['review']['claims'][0]['claim_id'], 'C1')
        self.assertNotIn('previous_claims', json.loads(client.calls[2][1]))

    def test_accepted_initial_review_needs_no_correction(self):
        client = FakeModel([ModelReply(review_data(self.original.claims))])
        result = quality_capture(self.raw.path, client)
        self.assertFalse(result.corrected)
        self.assertEqual(result.extraction_path, self.original.path)
        self.assertEqual(len(client.calls), 1)
        self.assertFalse((self.vault / 'repairs').exists())

    def test_cached_results_do_not_spend_more_calls(self):
        client = self.client()
        first = quality_capture(self.raw.path, client)
        second = quality_capture(self.raw.path, client)
        self.assertTrue(second.reviewed.reused)
        self.assertEqual(first.claims, second.claims)
        self.assertEqual(len(client.calls), 3)

    def test_failed_second_review_stops_without_another_correction(self):
        failed = deepcopy(self.final_review)
        failed['claims'][0].update(verdict='needs_revision', issues=['wrong_kind'])
        client = self.client(failed)
        result = quality_capture(self.raw.path, client)
        self.assertEqual(result.reviewed.review['status'], 'needs_revision')
        self.assertEqual(len(client.calls), 3)
        again = quality_capture(self.raw.path, client)
        self.assertEqual(again.reviewed.review['status'], 'needs_revision')
        self.assertEqual(len(client.calls), 3)

    def test_invalid_correction_never_saves_a_false_success(self):
        client = FakeModel([ModelReply(self.initial_review), ModelReply({'invalid': []}), ModelReply({'invalid': []})])
        with self.assertRaises(QualityError):
            quality_capture(self.raw.path, client)
        self.assertEqual(len(client.calls), 3)
        self.assertFalse((self.vault / 'repairs').exists())
        self.assertTrue(self.original.path.exists())

    def test_invalid_correction_can_retry_format_once(self):
        client = FakeModel([ModelReply(self.initial_review), ModelReply({}), response(self.changed_rows), ModelReply(self.final_review)])
        result = quality_capture(self.raw.path, client)
        self.assertEqual(result.reviewed.review['status'], 'accepted')
        self.assertEqual(len(client.calls), 4)
        self.assertIn('Previous correction was invalid', client.calls[2][0])
        self.assertEqual(json.loads(client.calls[2][1])['previous_output'], {})

    def test_service_failure_preserves_review_and_original(self):
        client = FakeModel([ModelReply(self.initial_review), ModelError('Service unavailable.')])
        with self.assertRaises(ModelError):
            quality_capture(self.raw.path, client)
        self.assertEqual(len(client.calls), 2)
        self.assertTrue((self.vault / 'reviews' / self.raw.path.name).exists())
        self.assertFalse((self.vault / 'repairs').exists())

    def test_second_review_unavailable_can_resume_saved_candidate(self):
        client = FakeModel([ModelReply(self.initial_review), response(self.changed_rows), ModelError('Service unavailable.')])
        with self.assertRaises(ModelError):
            quality_capture(self.raw.path, client)
        client.replies.append(ModelReply(self.final_review))
        result = quality_capture(self.raw.path, client)
        self.assertEqual(result.reviewed.review['status'], 'accepted')
        self.assertEqual(len(client.calls), 4)

    def test_corrupt_cached_correction_is_rebuilt(self):
        client = self.client()
        first = quality_capture(self.raw.path, client)
        data = json.loads(first.extraction_path.read_text())
        data['claims'][0]['evidence']['start_char'] = 999
        first.extraction_path.write_text(json.dumps(data))
        client.replies.extend([response(self.changed_rows), ModelReply(self.final_review)])
        second = quality_capture(self.raw.path, client)
        self.assertEqual(second.claims, self.changed_claims)
        self.assertEqual(len(client.calls), 5)

    def test_changed_original_invalidates_correction_and_final_review(self):
        client = self.client()
        first = quality_capture(self.raw.path, client)
        previous_hash = json.loads(first.extraction_path.read_text())['parent_extraction_hash']
        data = json.loads(self.original.path.read_text())
        data['created_at'] = 'changed'
        self.original.path.write_text(json.dumps(data))
        client.replies.extend([ModelReply(self.initial_review), response(self.changed_rows), ModelReply(self.final_review)])
        result = quality_capture(self.raw.path, client)
        self.assertNotEqual(json.loads(result.extraction_path.read_text())['parent_extraction_hash'], previous_hash)
        self.assertEqual(len(client.calls), 6)


class TelegramQualityTests(unittest.IsolatedAsyncioTestCase):
    async def test_corrected_claim_count_and_status_are_reported(self):
        with TemporaryDirectory() as folder:
            vault = Path(folder)
            extractor = FakeModel([ModelReply(dict(LABELS)), response()])
            class RevisingModel(FakeModel):
                def __init__(self):
                    super().__init__()
                    self.stage = 0
                def complete_json(self, prompt, content):
                    data = json.loads(content)
                    self.stage += 1
                    if 'previous_claims' in data:
                        return response(ROWS[:1])
                    result = review_data(data['claims'])
                    if self.stage == 1:
                        result['claims'][0].update(verdict='needs_revision', issues=['wrong_kind'])
                    return ModelReply(result)
            with patch('channels.telegram.client_from_environment', return_value=extractor), patch('channels.telegram.judge_client_from_environment', return_value=RevisingModel()), patch.object(Message, 'answer', new_callable=AsyncMock) as answer:
                await handle_message(message(text=TEXT), vault, 100)
                self.assertIn('accepted | 1/1', answer.await_args_list[-1].args[0])
                self.assertIn('automatic correction', answer.await_args_list[-1].args[0])


if __name__ == '__main__':
    unittest.main()
