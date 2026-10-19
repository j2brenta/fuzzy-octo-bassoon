from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.evidence import apply_citations, citation_targets, repair_evidence
from engine.extract import ExtractionError, _cached_claims, evidence_spans, extract_capture, validate_claims
from engine.judge import JudgeError, judge_capture
from engine.llm import ModelError, ModelReply
from store.raw import capture_text
from tests.test_judge import review_data
from tests.test_triage import FakeModel

TEXT = 'Dan proposes a model.\r\nAI handles preparation.\nHumans handle conversation. 💬'
ROW = {'statement': "Dan proposes AI handling preparation and humans handling conversation.", 'kind': 'opinion', 'durability_class': 'reference', 'confidence': 0.9}


class MultiEvidenceTests(unittest.TestCase):
    def test_multiple_spans_retain_exact_boundaries_and_canonical_identity(self):
        first = validate_claims({'claims': [{**ROW, 'evidence_passage_ids': [3, 1, 2]}]}, TEXT, 'item', 'section')[0]
        second = validate_claims({'claims': [{**ROW, 'evidence_passage_ids': [1, 2, 3]}]}, TEXT, 'item', 'section')[0]
        self.assertEqual(first, second)
        self.assertEqual([TEXT[s['start_char']:s['end_char']] for s in evidence_spans(first)], TEXT.splitlines())
        self.assertEqual(_cached_claims([first], TEXT, 'item', 'section'), [first])

    def test_legacy_single_passage_identity_is_preserved(self):
        legacy = validate_claims({'claims': [{**ROW, 'evidence_passage_id': 2}]}, TEXT, 'item', 'section')[0]
        current = validate_claims({'claims': [{**ROW, 'evidence_passage_ids': [2]}]}, TEXT, 'item', 'section')[0]
        self.assertEqual(legacy, current)
        self.assertIsInstance(current['evidence'], dict)

    def test_invalid_reference_lists_cannot_pass(self):
        for ids in [[], [True], [1.0], ['1'], [999], [1, 1], [1, 2, 3, 4, 5, 6], '1', None]:
            with self.subTest(ids=ids), self.assertRaises(ExtractionError):
                validate_claims({'claims': [{**ROW, 'evidence_passage_ids': ids}]}, TEXT, 'item', 'section')
        with self.assertRaises(ExtractionError):
            validate_claims({'claims': [{**ROW, 'evidence_passage_ids': [1], 'evidence_passage_id': 1}]}, TEXT, 'item', 'section')

    def test_a_corrupted_secondary_span_invalidates_the_cache(self):
        claim = validate_claims({'claims': [{**ROW, 'evidence_passage_ids': [1, 2, 3]}]}, TEXT, 'item', 'section')[0]
        for update in [{'start_char': 999}, {'item_id': 'other'}, {'end_char': 2}]:
            bad = deepcopy(claim)
            bad['evidence'][1].update(update)
            with self.subTest(update=update), self.assertRaises(ExtractionError):
                _cached_claims([bad], TEXT, 'item', 'section')


class CitationRepairTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.vault = Path(folder.name)
        self.raw = capture_text(TEXT, self.vault, channel='file', source_id='fixture:citations', source_name='Synthetic citation fixture')
        self.initial_row = {**ROW, 'evidence_passage_id': 2}
        self.original = extract_capture(self.raw.path, FakeModel([ModelReply({'claims': [self.initial_row]})]))
        self.review = review_data(self.original.claims)
        self.review['claims'][0].update(verdict='needs_revision', issues=['wrong_evidence'])
        self.reviewed = judge_capture(self.raw.path, FakeModel([ModelReply(self.review)]))
        self.cid = self.original.claims[0]['claim_id']
        self.section = self.original.claims[0]['evidence']['section_id']
        self.edits = {'citations': [{'claim_id': self.cid, 'evidence_passage_ids': [1, 2, 3]}]}
        self.corrected = apply_citations(self.edits, self.original.claims, {self.cid}, TEXT, self.raw.item_id, self.section)
        self.final = review_data(self.corrected)

    def client(self, final=None):
        return FakeModel([ModelReply(self.edits), ModelReply(final or self.final)])

    def repair(self, client):
        return repair_evidence(self.raw.path, client, self.original.path, self.reviewed)

    def test_citation_repair_preserves_wording_and_initial_artifacts(self):
        before = self.original.path.read_bytes(), self.reviewed.path.read_bytes()
        client = self.client()
        result = self.repair(client)
        self.assertEqual(result.reviewed.review['status'], 'accepted')
        self.assertEqual(result.claims[0]['statement'], self.original.claims[0]['statement'])
        self.assertNotEqual(result.claims[0]['claim_id'], self.cid)
        self.assertEqual(len(evidence_spans(result.claims[0])), 3)
        self.assertEqual(before, (self.original.path.read_bytes(), self.reviewed.path.read_bytes()))
        self.assertEqual(json.loads(client.calls[1][1])['claims'][0]['evidence_passage_ids'], [1, 2, 3])
        self.assertNotIn('feedback', json.loads(client.calls[1][1]))

    def test_unknown_missing_duplicate_ids_and_wording_edits_are_rejected(self):
        variants = [{'citations': []}, {'citations': self.edits['citations'] * 2},
                    {'citations': [{'claim_id': 'unknown', 'evidence_passage_ids': [1]}]},
                    {'citations': [{**self.edits['citations'][0], 'statement': 'Invented correction.'}]}]
        for data in variants:
            with self.subTest(data=data), self.assertRaises(JudgeError):
                apply_citations(data, self.original.claims, {self.cid}, TEXT, self.raw.item_id, self.section)

    def test_other_semantic_errors_are_not_hidden_by_citation_repair(self):
        changed = deepcopy(self.reviewed.review)
        changed['claims'][0]['issues'].append('lost_qualification')
        self.assertFalse(citation_targets(changed))
        self.assertEqual(citation_targets(self.reviewed.review), {self.cid})

    def test_supported_claims_cannot_be_modified(self):
        unchanged = deepcopy(self.original.claims[0])
        unchanged['claim_id'] = 'not-targeted'
        output = apply_citations(self.edits, self.original.claims + [unchanged], {self.cid}, TEXT, self.raw.item_id, self.section)
        self.assertEqual(output[1], unchanged)

    def test_repeat_calls_use_caches(self):
        client = self.client()
        self.repair(client)
        repeated = self.repair(client)
        self.assertTrue(repeated.reviewed.reused)
        self.assertEqual(len(client.calls), 2)

    def test_invalid_output_is_not_saved_as_success(self):
        client = FakeModel([ModelReply({'citations': []}), ModelReply({'citations': []})])
        with self.assertRaises(JudgeError):
            self.repair(client)
        self.assertFalse((self.vault / 'evidence').exists())

    def test_final_rejection_does_not_start_another_loop(self):
        failed = deepcopy(self.final)
        failed['claims'][0].update(verdict='needs_revision', issues=['wrong_evidence'])
        client = self.client(failed)
        result = self.repair(client)
        self.assertEqual(result.reviewed.review['status'], 'needs_revision')
        self.repair(client)
        self.assertEqual(len(client.calls), 2)

    def test_interrupted_final_review_resumes_saved_citations(self):
        client = FakeModel([ModelReply(self.edits), ModelError('Service unavailable.')])
        with self.assertRaises(ModelError):
            self.repair(client)
        client.replies.append(ModelReply(self.final))
        self.assertEqual(self.repair(client).reviewed.review['status'], 'accepted')
        self.assertEqual(len(client.calls), 3)

    def test_citation_only_errors_after_correction_trigger_one_repair(self):
        from engine.quality import quality_capture
        client = FakeModel([
            ModelReply({'claims': [self.initial_row]}), ModelReply(self.review),
            ModelReply(self.edits), ModelReply(self.final),
        ])
        result = quality_capture(self.raw.path, client)
        self.assertTrue(result.citations_repaired)
        self.assertEqual(result.reviewed.review['status'], 'accepted')
        self.assertEqual(len(client.calls), 4)
        again = quality_capture(self.raw.path, client)
        self.assertEqual(again.reviewed.review['status'], 'accepted')
        self.assertEqual(len(client.calls), 4)

    def test_stale_source_candidate_is_rejected_before_model_calls(self):
        data = json.loads(self.original.path.read_text())
        data['source_content_hash'] = 'stale'
        self.original.path.write_text(json.dumps(data))
        client = self.client()
        with self.assertRaises(JudgeError):
            self.repair(client)
        self.assertEqual(client.calls, [])

    def test_new_prompts_and_full_quality_pipeline_support_multiple_passages(self):
        from engine.quality import quality_capture
        # Initial semantic repair returns a claim already carrying all required citations.
        client = FakeModel([ModelReply({'claims': [{**ROW, 'evidence_passage_ids': [1, 2, 3]}]}), ModelReply(self.final)])
        result = quality_capture(self.raw.path, client)
        self.assertEqual(result.reviewed.review['status'], 'accepted')
        self.assertEqual(len(evidence_spans(result.claims[0])), 3)


if __name__ == '__main__':
    unittest.main()
