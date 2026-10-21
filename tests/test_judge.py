from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.types import Message

from channels.telegram import handle_message
from engine.extract import extract_capture, source_passages
from engine.judge import JudgeError, judge_capture, validate_review
from engine.llm import ModelError, ModelReply, judge_client_from_environment
from store.raw import capture_text
from tests.test_extract import TEXT, ROWS, response
from tests.test_telegram import message
from tests.test_triage import FakeModel, LABELS


def review_data(claims, **changes):
    data = {
        'claims': [{'claim_id': c['claim_id'], 'verdict': 'supported', 'issues': [], 'reason': 'Faithful to passage 1.'} for c in claims],
        'omitted_concepts': [], 'summary': 'The extracted ideas are faithful to the source.'}
    return {**data, **changes}


class JudgeTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.vault = Path(directory.name)
        self.raw = capture_text(TEXT, self.vault, channel='file', source_id='fixture:judge', source_name='Synthetic judge fixture')
        self.extraction = extract_capture(self.raw.path, FakeModel([response()]))
        self.claims = self.extraction.claims
        self.data = review_data(self.claims)
        self.passages = source_passages(TEXT)

    def client(self, data=None):
        client = FakeModel([ModelReply(self.data if data is None else data, actual_model='fixture-judge', usage={'total_tokens': 30})])
        client.model_name = 'fixture-judge'
        return client

    def test_acceptance_requires_claim_correctness_but_retains_coverage_notes(self):
        self.assertEqual(validate_review(self.data, self.claims, self.passages)['status'], 'accepted')
        changed = deepcopy(self.data)
        changed['claims'][0].update(verdict='needs_revision', issues=['lost_qualification'])
        self.assertEqual(validate_review(changed, self.claims, self.passages)['status'], 'needs_revision')
        changed = {**self.data, 'omitted_concepts': [{'passage_id': 1, 'description': 'A central omitted idea.'}]}
        self.assertEqual(validate_review(changed, self.claims, self.passages)['status'], 'accepted')
        self.assertTrue(validate_review(changed, self.claims, self.passages)['omitted_concepts'])

    def test_unknown_missing_and_repeated_ids_cannot_pass(self):
        for rows in [self.data['claims'][:1], self.data['claims'] * 2,
                     [self.data['claims'][0], self.data['claims'][0]],
                     [{**r, 'claim_id': 'invented'} for r in self.data['claims']]]:
            with self.subTest(rows=rows), self.assertRaises(JudgeError):
                validate_review({**self.data, 'claims': rows}, self.claims, self.passages)

    def test_inconsistent_verdicts_invalid_codes_and_omissions_are_rejected(self):
        variants = []
        for update in [{'verdict': 'approved'}, {'issues': ['unsupported']}, {'verdict': 'unsupported'},
                       {'verdict': 'unsupported', 'issues': ['invented']}, {'issues': [None]}, {'reason': ''}]:
            data = deepcopy(self.data)
            data['claims'][0].update(update)
            variants.append(data)
        for pid in [True, '1', 1.0, 999]:
            variants.append({**self.data, 'omitted_concepts': [{'passage_id': pid, 'description': 'Missing idea.'}]})
        variants.append({**self.data, 'status': 'accepted'})
        for data in variants:
            with self.subTest(data=data), self.assertRaises(JudgeError):
                validate_review(data, self.claims, self.passages)

    def test_review_does_not_rewrite_source_or_extraction_and_is_cached(self):
        before = self.raw.path.read_bytes(), self.extraction.path.read_bytes()
        client = self.client()
        first = judge_capture(self.raw.path, client)
        second = judge_capture(self.raw.path, client)
        self.assertEqual(first.review['status'], 'accepted')
        self.assertTrue(second.reused)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(before, (self.raw.path.read_bytes(), self.extraction.path.read_bytes()))
        payload = json.loads(client.calls[0][1])
        self.assertEqual(payload['passages'], [{'id': 1, 'text': TEXT}])
        self.assertEqual(len(payload['claims']), 2)
        stored = json.loads(first.path.read_text())
        self.assertEqual(stored['actual_model'], 'fixture-judge')
        self.assertEqual(stored['scope'], 'source_faithfulness_not_external_fact_checking')

    def test_changed_extraction_invalidates_cached_review(self):
        first = judge_capture(self.raw.path, self.client())
        old_hash = json.loads(first.path.read_text())['extraction_hash']
        record = json.loads(self.extraction.path.read_text())
        record['created_at'] = 'changed'
        self.extraction.path.write_text(json.dumps(record))
        second = judge_capture(self.raw.path, self.client())
        self.assertFalse(second.reused)
        self.assertNotEqual(json.loads(second.path.read_text())['extraction_hash'], old_hash)

    def test_changed_judge_invalidates_cache(self):
        judge_capture(self.raw.path, self.client())
        client = self.client()
        client.model_name = 'different-judge'
        self.assertFalse(judge_capture(self.raw.path, client).reused)
        self.assertEqual(len(client.calls), 1)

    def test_corrupt_cached_status_cannot_bypass_validation(self):
        data = deepcopy(self.data)
        data['claims'][0].update(verdict='unsupported', issues=['unsupported'])
        first = judge_capture(self.raw.path, self.client(data))
        record = json.loads(first.path.read_text())
        record['review']['status'] = 'accepted'
        first.path.write_text(json.dumps(record))
        result = judge_capture(self.raw.path, self.client(data))
        self.assertFalse(result.reused)
        self.assertEqual(result.review['status'], 'needs_revision')

    def test_invalid_reply_retries_once_with_feedback(self):
        client = FakeModel([ModelReply({'invalid': True}), ModelReply(self.data)])
        result = judge_capture(self.raw.path, client)
        self.assertEqual(json.loads(result.path.read_text())['attempts'], 2)
        self.assertIn('Previous output was invalid', client.calls[1][0])

    def test_failed_review_never_saves_acceptance(self):
        client = FakeModel([ModelReply({}), ModelReply({'claims': []})])
        with self.assertRaises(JudgeError) as failure:
            judge_capture(self.raw.path, client)
        self.assertFalse((self.vault / 'reviews').exists())
        self.assertTrue(self.extraction.path.exists())
        # Both reasons are reported, and both rejected answers are kept for inspection only.
        self.assertEqual(str(failure.exception).count(' | '), 1)
        rejected = sorted((self.vault / 'rejected' / 'reviews').glob('*.json'))
        self.assertEqual([p.name.rsplit('.', 2)[1] for p in rejected], ['1', '2'])
        second = json.loads(rejected[1].read_text())
        self.assertEqual(second['data'], {'claims': []})
        self.assertIn(second['error'], str(failure.exception))

    def test_service_errors_do_not_trigger_blind_retries(self):
        client = FakeModel([ModelError('Service unavailable.')])
        with self.assertRaises(ModelError):
            judge_capture(self.raw.path, client)
        self.assertEqual(len(client.calls), 1)
        self.assertFalse((self.vault / 'reviews').exists())

    def test_stale_extraction_does_not_make_a_model_call(self):
        data = json.loads(self.extraction.path.read_text())
        data['source_content_hash'] = 'stale'
        self.extraction.path.write_text(json.dumps(data))
        client = self.client()
        with self.assertRaises(JudgeError):
            judge_capture(self.raw.path, client)
        self.assertEqual(client.calls, [])

    def test_empty_extraction_still_requires_coverage_review(self):
        data = {'claims': [], 'omitted_concepts': [{'passage_id': 1, 'description': 'A central idea was missed.'}], 'summary': 'No ideas extracted.'}
        self.assertEqual(validate_review(data, [], self.passages)['status'], 'accepted')
        self.assertTrue(validate_review(data, [], self.passages)['omitted_concepts'])

    def test_configuration_is_separate_and_key_not_in_repr(self):
        with patch.dict(os.environ, {'JUDGE_PROVIDER': 'off'}, clear=True):
            self.assertIsNone(judge_client_from_environment())
        with patch.dict(os.environ, {'JUDGE_PROVIDER': 'nebius', 'NEBIUS_API_KEY': 'fixture-secret', 'NEBIUS_MODEL': 'small-extractor'}, clear=True):
            client = judge_client_from_environment()
            self.assertEqual(client.model_name, 'nvidia/Nemotron-3-Ultra-550b-a55b')
            self.assertEqual(client.model_provider, 'nebius')
            self.assertEqual(client.timeout_seconds, 180)
            self.assertNotIn('fixture-secret', repr(client))


class TelegramJudgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_pipeline_sends_automated_review(self):
        with TemporaryDirectory() as directory:
            vault = Path(directory)
            extractor = FakeModel([ModelReply(dict(LABELS)), response()])
            class AutomaticJudge(FakeModel):
                def complete_json(self, prompt, content):
                    return ModelReply(review_data(json.loads(content)['claims']))
            with patch('channels.telegram.client_from_environment', return_value=extractor), patch('channels.telegram.judge_client_from_environment', return_value=AutomaticJudge()), patch.object(Message, 'answer', new_callable=AsyncMock) as answer:
                await handle_message(message(text=TEXT), vault, 100)
                self.assertEqual(answer.await_count, 5)
                self.assertIn('AI quality review: accepted | 2/2', answer.await_args_list[-1].args[0])
            self.assertTrue(list((vault / 'reviews').glob('*.json')))

    async def test_judge_failure_reports_pending_and_preserves_claims(self):
        with TemporaryDirectory() as directory:
            vault = Path(directory)
            extractor = FakeModel([ModelReply(dict(LABELS)), response()])
            judge = FakeModel([ModelError('Service unavailable.')])
            with patch('channels.telegram.client_from_environment', return_value=extractor), patch('channels.telegram.judge_client_from_environment', return_value=judge), patch.object(Message, 'answer', new_callable=AsyncMock) as answer:
                await handle_message(message(text=TEXT), vault, 100)
                self.assertIn('quality review is pending', answer.await_args_list[-1].args[0])
            self.assertTrue(list((vault / 'extractions').glob('*.json')))
            self.assertFalse((vault / 'reviews').exists())


if __name__ == '__main__':
    unittest.main()
