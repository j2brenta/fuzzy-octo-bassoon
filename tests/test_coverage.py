from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.coverage import expand_coverage
from engine.extract import ExtractionError, _cached_claims, extract_capture, validate_claims
from engine.judge import JudgeError, judge_capture
from engine.llm import ModelError, ModelReply
from engine.quality import quality_capture
from store.raw import capture_text
from tests.test_judge import review_data
from tests.test_triage import FakeModel

TEXT = 'Keep explanations simple.\nAI may reduce paperwork.\nPreserve the original source.'
FIRST = {'statement': 'Keep explanations simple.', 'evidence_passage_id': 1, 'kind': 'principle', 'durability_class': 'foundation', 'confidence': 0.9}
ADDITIONS = [
    {'statement': 'AI may reduce paperwork.', 'evidence_passage_id': 2, 'kind': 'opinion', 'durability_class': 'news', 'confidence': 0.8},
    {'statement': 'Preserve the original source.', 'evidence_passage_id': 3, 'kind': 'principle', 'durability_class': 'foundation', 'confidence': 0.9},
]


class CoverageTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.vault = Path(folder.name)
        self.raw = capture_text(TEXT, self.vault, channel='file', source_id='fixture:coverage', source_name='Synthetic coverage fixture')
        self.original = extract_capture(self.raw.path, FakeModel([ModelReply({'claims': [FIRST]})]))
        self.section = self.original.claims[0]['evidence']['section_id']
        self.review = review_data(self.original.claims, omitted_concepts=[{'passage_id': 2, 'description': 'AI may reduce paperwork.'}, {'passage_id': 3, 'description': 'Preserve the original.'}])
        self.reviewed = judge_capture(self.raw.path, FakeModel([ModelReply(self.review)]))
        self.combined = self.original.claims + validate_claims({'claims': ADDITIONS}, TEXT, self.raw.item_id, self.section)
        self.final = review_data(self.combined)

    def client(self, final=None):
        return FakeModel([ModelReply({'claims': ADDITIONS}), ModelReply(final or self.final)])

    def expand(self, client):
        return expand_coverage(self.raw.path, client, self.original.path, self.reviewed)

    def test_supported_claims_are_preserved_and_all_additions_re_reviewed(self):
        original_bytes = self.original.path.read_bytes()
        review_bytes = self.reviewed.path.read_bytes()
        client = self.client()
        result = self.expand(client)
        self.assertEqual(result.claims, self.combined)
        self.assertEqual(result.claims[:1], self.original.claims)
        self.assertEqual(result.reviewed.review['status'], 'accepted')
        self.assertEqual(self.original.path.read_bytes(), original_bytes)
        self.assertEqual(self.reviewed.path.read_bytes(), review_bytes)
        self.assertEqual(result.extraction_path.parent.name, 'coverage')
        self.assertEqual(result.reviewed.path.parent.name, 'coverage-reviews')
        self.assertEqual(len(json.loads(client.calls[1][1])['claims']), 3)
        self.assertNotIn('omitted_concepts', json.loads(client.calls[1][1]))

    def test_initially_supported_claims_skip_rewrite_and_go_directly_to_coverage(self):
        client = self.client()
        result = quality_capture(self.raw.path, client, expand_omissions=True)
        self.assertTrue(result.expanded)
        self.assertFalse(result.corrected)
        self.assertFalse((self.vault / 'repairs').exists())
        self.assertEqual(len(client.calls), 2)

    def test_repeated_processing_reuses_both_caches(self):
        client = self.client()
        quality_capture(self.raw.path, client, expand_omissions=True)
        again = quality_capture(self.raw.path, client, expand_omissions=True)
        self.assertTrue(again.reviewed.reused)
        self.assertEqual(len(client.calls), 2)

    def test_remaining_omissions_stop_after_one_pass(self):
        final = review_data(self.combined, omitted_concepts=[{'passage_id': 3, 'description': 'Still missing a central idea.'}])
        client = self.client(final)
        result = quality_capture(self.raw.path, client, expand_omissions=True)
        self.assertEqual(result.reviewed.review['status'], 'accepted')
        self.assertTrue(result.reviewed.review['omitted_concepts'])
        again = quality_capture(self.raw.path, client, expand_omissions=True)
        self.assertEqual(again.reviewed.review['status'], 'accepted')
        self.assertEqual(len(client.calls), 2)

    def test_invalid_passages_and_exact_existing_duplicates_do_not_save(self):
        for additions in [[{**ADDITIONS[0], 'evidence_passage_id': 999}], [FIRST]]:
            client = FakeModel([ModelReply({'claims': additions}), ModelReply({'claims': additions})])
            with self.subTest(additions=additions), self.assertRaises(JudgeError):
                self.expand(client)
            self.assertFalse((self.vault / 'coverage').exists())
            self.assertTrue(self.original.path.exists())

    def test_semantic_duplicates_flagged_by_judge_are_not_accepted(self):
        final = deepcopy(self.final)
        final['claims'][1].update(verdict='duplicate', issues=['duplicate'], reason='Duplicates an existing idea.')
        result = self.expand(self.client(final))
        self.assertEqual(result.reviewed.review['status'], 'needs_revision')

    def test_service_failure_keeps_original(self):
        with self.assertRaises(ModelError):
            self.expand(FakeModel([ModelError('Service unavailable.')]))
        self.assertFalse((self.vault / 'coverage').exists())
        self.assertTrue(self.original.path.exists())

    def test_re_review_can_resume_without_regenerating_additions(self):
        client = FakeModel([ModelReply({'claims': ADDITIONS}), ModelError('Service unavailable.')])
        with self.assertRaises(ModelError):
            self.expand(client)
        client.replies.append(ModelReply(self.final))
        self.assertEqual(self.expand(client).reviewed.review['status'], 'accepted')
        self.assertEqual(len(client.calls), 3)

    def test_empty_additions_are_allowed_but_do_not_override_omissions(self):
        client = FakeModel([ModelReply({'claims': []}), ModelReply(self.review)])
        result = self.expand(client)
        self.assertEqual(result.claims, self.original.claims)
        self.assertEqual(result.reviewed.review['status'], 'accepted')
        self.assertTrue(result.reviewed.review['omitted_concepts'])

    def test_corrupt_cache_cannot_replace_existing_supported_claim(self):
        result = self.expand(self.client())
        data = json.loads(result.extraction_path.read_text())
        data['claims'] = data['claims'][1:]
        result.extraction_path.write_text(json.dumps(data))
        client = self.client()
        rebuilt = self.expand(client)
        self.assertEqual(rebuilt.claims, self.combined)
        self.assertEqual(len(client.calls), 2)

    def test_changed_review_invalidates_coverage_cache(self):
        result = self.expand(self.client())
        old_hash = json.loads(result.extraction_path.read_text())['parent_review_hash']
        self.reviewed.review['summary'] = 'Updated independent feedback.'
        self.expand(self.client())
        data = json.loads(result.extraction_path.read_text())
        self.assertNotEqual(data['parent_review_hash'], old_hash)

    def test_coverage_refuses_unsupported_base_claims(self):
        self.reviewed.review['claims'][0].update(verdict='unsupported', issues=['unsupported'])
        client = self.client()
        with self.assertRaises(JudgeError):
            self.expand(client)
        self.assertEqual(client.calls, [])

    def test_expanded_claim_limit_and_judge_cover_more_than_25_claims(self):
        text = '\n'.join(f'Principle {i}.' for i in range(26))
        raw = capture_text(text, self.vault, channel='file', source_id='fixture:many', source_name='Many synthetic ideas')
        rows = [{**FIRST, 'statement': f'Principle {i}.', 'evidence_passage_id': i+1} for i in range(26)]
        with self.assertRaises(ExtractionError):
            validate_claims({'claims': rows}, text, raw.item_id, 'section')
        initial = extract_capture(raw.path, FakeModel([ModelReply({'claims': rows[:25]})]))
        section = initial.claims[0]['evidence']['section_id']
        combined = validate_claims({'claims': rows}, text, raw.item_id, section, max_claims=50)
        self.assertEqual(len(_cached_claims(combined, text, raw.item_id, section, max_claims=50)), 26)
        review = review_data(initial.claims, omitted_concepts=[{'passage_id': 26, 'description': 'Principle 25.'}])
        reviewed = judge_capture(raw.path, FakeModel([ModelReply(review)]))
        client = FakeModel([ModelReply({'claims': rows[25:]}), ModelReply(review_data(combined))])
        result = expand_coverage(raw.path, client, initial.path, reviewed)
        self.assertEqual(len(result.claims), 26)
        self.assertEqual(result.reviewed.review['status'], 'accepted')
        self.assertIn('up to 50', client.calls[1][0])
        with self.assertRaises(ExtractionError):
            validate_claims({'claims': rows * 2}, text, raw.item_id, section, max_claims=50)


if __name__ == '__main__':
    unittest.main()
