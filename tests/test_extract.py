from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.types import Message

from channels.telegram import handle_message
from engine.extract import ExtractionError, extract_capture, main, source_passages, validate_claims
from engine.llm import ModelError, ModelReply
from store.raw import capture_text
from tests.test_telegram import message
from tests.test_triage import FakeModel, LABELS

TEXT = '💡 Keep explanations simple. AI may reduce paperwork.'
ROWS = [
    {'statement': 'Keep explanations simple.', 'evidence_passage_id': 1, 'kind': 'principle', 'durability_class': 'foundation', 'confidence': 0.9},
    {'statement': 'AI may reduce paperwork.', 'evidence_passage_id': 1, 'kind': 'opinion', 'durability_class': 'news', 'confidence': 0.8},
]


def response(rows=None):
    return ModelReply({'claims': ROWS if rows is None else rows}, actual_model='fixture-model', usage={'total_tokens': 20})


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.vault = Path(self.directory.name)
        self.raw = capture_text(TEXT, self.vault, channel='file', source_id='fixture', source_name='Fixture')

    def test_mixed_claims_have_distinct_half_lives_and_exact_source_spans(self):
        before = self.raw.path.read_bytes()
        result = extract_capture(self.raw.path, FakeModel([response()]))
        self.assertEqual([c['half_life_days'] for c in result.claims], [720, 30])
        self.assertEqual(result.claims[1]['statement'], 'AI may reduce paperwork.')
        for row, claim in zip(ROWS, result.claims):
            evidence = claim['evidence']
            self.assertEqual(TEXT[evidence['start_char']:evidence['end_char']], TEXT)
            self.assertEqual(evidence['item_id'], self.raw.item_id)
            self.assertTrue(evidence['section_id'].startswith(self.raw.item_id))
            self.assertEqual(claim['support_status'], 'quoted_not_fact_checked')
            self.assertNotIn('evidence_quote', claim)
        self.assertEqual(len({c['claim_id'] for c in result.claims}), 2)
        self.assertEqual(self.raw.path.read_bytes(), before)

    def test_successful_extraction_is_cached_without_another_call(self):
        client = FakeModel([response()])
        first = extract_capture(self.raw.path, client)
        original = first.path.read_bytes()
        second = extract_capture(self.raw.path, client)
        self.assertTrue(second.reused)
        self.assertEqual(first.claims, second.claims)
        self.assertEqual(second.path.read_bytes(), original)
        self.assertEqual(len(client.calls), 1)

    def test_missing_quote_retries_once_and_does_not_save_false_success(self):
        bad = [{**ROWS[0], 'evidence_passage_id': 999}]
        client = FakeModel([response(bad), response(bad)])
        before = self.raw.path.read_bytes()
        with self.assertRaisesRegex(ExtractionError, 'twice'):
            extract_capture(self.raw.path, client)
        self.assertEqual(len(client.calls), 2)
        self.assertFalse((self.vault / 'extractions').exists())
        self.assertEqual(self.raw.path.read_bytes(), before)

    def test_repair_can_succeed_on_second_attempt(self):
        client = FakeModel([ModelReply({'wrong': []}), response()])
        result = extract_capture(self.raw.path, client)
        self.assertEqual(len(result.claims), 2)
        self.assertEqual(json.loads(result.path.read_text())['attempts'], 2)

    def test_retry_receives_specific_validation_feedback(self):
        bad = [{**ROWS[0], 'evidence_passage_id': 999}]
        client = FakeModel([response(bad), response()])
        extract_capture(self.raw.path, client)
        self.assertIn('Evidence passage ID must be an integer from the supplied passages.', client.calls[1][0])

    def test_final_error_explains_failed_rule_without_source_text(self):
        bad = [{**ROWS[0], 'evidence_passage_id': 'Private invented quote.'}]
        with self.assertRaisesRegex(ExtractionError, 'Evidence passage ID') as caught:
            extract_capture(self.raw.path, FakeModel([response(bad), response(bad)]))
        self.assertNotIn('Private invented quote', str(caught.exception))

    def test_compatible_v5_cache_is_reused_without_rewriting_original_metadata(self):
        client = FakeModel([response()])
        original = extract_capture(self.raw.path, client)
        data = json.loads(original.path.read_text())
        data['prompt_version'] = 'extract-v5'
        original.path.write_text(json.dumps(data))
        before = original.path.read_bytes()
        repeated = extract_capture(self.raw.path, client)
        self.assertTrue(repeated.reused)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(original.path.read_bytes(), before)

    def test_repeated_passages_keep_distinct_original_locations(self):
        text = '💡 Same idea.\r\n\n💡 Same idea.'
        row = {**ROWS[0], 'evidence_passage_id': 2}
        claim = validate_claims({'claims': [row]}, text, self.raw.item_id, 'section')[0]
        span = claim['evidence']
        self.assertEqual(span['start_char'], text.rindex('💡'))
        self.assertEqual(text[span['start_char']:span['end_char']], '💡 Same idea.')
        self.assertEqual(len(source_passages(text)), 2)

    def test_model_receives_numbered_passages_and_invalid_ids_are_rejected(self):
        client = FakeModel([response()])
        extract_capture(self.raw.path, client)
        self.assertEqual(json.loads(client.calls[0][1]), {'passages': [{'id': 1, 'text': TEXT}]})
        for bad_id in [True, 1.0, '1', 0, -1, 2, None]:
            with self.subTest(passage_id=bad_id), self.assertRaises(ExtractionError):
                validate_claims({'claims': [{**ROWS[0], 'evidence_passage_id': bad_id}]}, TEXT, self.raw.item_id, 'section')

    def test_wrong_fields_confidence_and_duplicate_claims_are_rejected(self):
        for rows in [
            [{**ROWS[0], 'confidence': True}],
            [{**ROWS[0], 'confidence': float('nan')}],
            [{**ROWS[0], 'kind': 'verified-fact'}],
            [{**ROWS[0], 'durability_class': 'forever'}],
            [ROWS[0], ROWS[0]],
            [ROWS[0]] * 26,
        ]:
            with self.subTest(rows=rows):
                with self.assertRaises(ExtractionError):
                    validate_claims({'claims': rows}, TEXT, self.raw.item_id, 'section')

    def test_empty_extraction_is_allowed_without_inventing_claims(self):
        self.assertEqual(extract_capture(self.raw.path, FakeModel([response([])])).claims, [])

    def test_model_failure_preserves_source_without_blind_retry(self):
        client = FakeModel([ModelError('Service unavailable.')])
        with self.assertRaises(ModelError):
            extract_capture(self.raw.path, client)
        self.assertEqual(len(client.calls), 1)
        self.assertTrue(self.raw.path.exists())
        self.assertFalse((self.vault / 'extractions').exists())

    def test_model_change_rebuilds_derived_extraction(self):
        extract_capture(self.raw.path, FakeModel([response()]))
        client = FakeModel([response()])
        client.model_name = 'another-model'
        self.assertFalse(extract_capture(self.raw.path, client).reused)
        self.assertEqual(len(client.calls), 1)

    def test_corrupted_cached_evidence_is_rebuilt(self):
        first = extract_capture(self.raw.path, FakeModel([response()]))
        cached = json.loads(first.path.read_text())
        cached['claims'][0]['evidence']['start_char'] = 999
        first.path.write_text(json.dumps(cached))
        result = extract_capture(self.raw.path, FakeModel([response()]))
        self.assertFalse(result.reused)
        self.assertEqual(result.claims, first.claims)

    def test_cli_runs_triage_gate_then_extraction(self):
        client = FakeModel([ModelReply(dict(LABELS)), response()])
        with (
            patch('engine.extract.load_dotenv'),
            patch('engine.extract.client_from_environment', return_value=client),
            patch('sys.argv', ['extract', str(self.vault), '--item-id', self.raw.item_id]),
            redirect_stdout(StringIO()) as output,
        ):
            self.assertEqual(main(), 0)
        self.assertIn('2 claims', output.getvalue())
        self.assertEqual(len(client.calls), 2)


class TelegramExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_skip_stops_extraction_and_retains_original(self):
        skip = {**LABELS, 'keep': False, 'kind': 'other', 'durability_class': 'ephemeral', 'reason': 'Only a greeting.'}
        client = FakeModel([ModelReply(skip)])
        with TemporaryDirectory() as folder:
            vault = Path(folder)
            with patch('channels.telegram.client_from_environment', return_value=client), patch.object(Message, 'answer', new_callable=AsyncMock) as answer:
                await handle_message(message(text='hello'), vault, 100)
                self.assertIn('extraction skipped by triage', answer.await_args_list[3].args[0])
            self.assertEqual(len(client.calls), 1)
            self.assertTrue(list((vault / 'raw').glob('*.json')))
            self.assertFalse((vault / 'extractions').exists())

    async def test_extraction_failure_retains_earlier_stages(self):
        client = FakeModel([ModelReply(dict(LABELS)), ModelError('Service unavailable.')])
        with TemporaryDirectory() as folder:
            vault = Path(folder)
            with patch('channels.telegram.client_from_environment', return_value=client), patch.object(Message, 'answer', new_callable=AsyncMock) as answer:
                await handle_message(message(text=TEXT), vault, 100)
                self.assertIn('claim extraction is pending', answer.await_args_list[3].args[0])
            for stage in ['raw', 'documents', 'triage']:
                self.assertTrue(list((vault / stage).glob('*.json')))
            self.assertFalse((vault / 'extractions').exists())


if __name__ == '__main__':
    unittest.main()
