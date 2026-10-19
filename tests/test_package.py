from copy import deepcopy
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.artifacts import atomic_json, fingerprint
from engine.entities import extract_entities, review_entities
from engine.extract import extract_capture
from engine.judge import judge_capture
from engine.package import PackageError, build_extraction, main
from engine.placement import PlacementError, apply_storage_stub
from engine.quality import quality_capture
from engine.triage import triage_capture
from engine.llm import ModelReply
from store.raw import capture_text
from tests.test_entities import entity_review
from tests.test_extract import ROWS, TEXT, response
from tests.test_judge import review_data
from tests.test_triage import FakeModel, LABELS


class PackageTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.vault = Path(folder.name)
        self.raw = capture_text(TEXT, self.vault, channel='file', source_id='fixture:handoff', source_name='Synthetic handoff', author_hint={'name': 'Source author'})
        triage_capture(self.raw.path, FakeModel([ModelReply(dict(LABELS))]))
        self.original = extract_capture(self.raw.path, FakeModel([response()]))
        self.claim_review = judge_capture(self.raw.path, FakeModel([ModelReply(review_data(self.original.claims))]))
        self.entities = extract_entities(self.raw.path, FakeModel([ModelReply({'entities': []})]))
        self.entity_review = review_entities(self.raw.path, FakeModel([ModelReply(entity_review([]))]), self.entities)

    def test_one_record_preserves_source_claims_entities_and_reviews(self):
        before = self.raw.path.read_bytes()
        result = build_extraction(self.raw.path)
        record = result.record
        self.assertEqual(record['claims'], self.original.claims)
        self.assertEqual(record['source']['author_hint'], {'name': 'Source author'})
        self.assertEqual(record['quality_status'], 'accepted')
        self.assertTrue(record['quality_gate_passed'])
        self.assertEqual(record['graph_status'], 'not_applied')
        self.assertEqual(record['contract_status'], 'provisional_awaiting_shared_schema')
        self.assertNotIn('text', record)
        self.assertEqual(self.raw.path.read_bytes(), before)
        self.assertIn('raw/' + self.raw.path.name, record['inputs'])

    def test_repeated_build_reuses_identical_record_and_timestamp(self):
        first = build_extraction(self.raw.path)
        data = first.path.read_bytes()
        second = build_extraction(self.raw.path)
        self.assertTrue(second.reused)
        self.assertEqual(data, second.path.read_bytes())

    def test_changed_review_rebuilds_record_and_does_not_hide_rejection(self):
        first = build_extraction(self.raw.path)
        data = json.loads(self.claim_review.path.read_text())
        data['review']['claims'][0].update(verdict='needs_revision', issues=['wrong_kind'])
        data['review']['status'] = 'needs_revision'
        atomic_json(self.claim_review.path, data)
        second = build_extraction(self.raw.path)
        self.assertFalse(second.reused)
        self.assertNotEqual(first.record['bundle_id'], second.record['bundle_id'])
        self.assertEqual(second.record['quality_status'], 'needs_revision')
        self.assertFalse(second.record['quality_gate_passed'])

    def test_missing_or_stale_review_stays_pending(self):
        self.claim_review.path.unlink()
        result = build_extraction(self.raw.path)
        self.assertEqual(result.record['quality_status'], 'pending')
        self.assertFalse(result.record['quality_gate_passed'])
        self.assertEqual(apply_storage_stub(result.path)['status'], 'pending_quality')

    def test_missing_entities_do_not_imply_accepted_empty_entities(self):
        self.entities.path.unlink()
        result = build_extraction(self.raw.path)
        self.assertEqual(result.record['entity_status'], 'pending')
        self.assertEqual(result.record['quality_status'], 'pending')

    def test_missing_triage_and_skipped_items_cannot_pass_the_gate(self):
        path = self.vault / 'triage' / self.raw.path.name
        saved = json.loads(path.read_text())
        path.unlink()
        self.assertEqual(build_extraction(self.raw.path).record['quality_status'], 'pending')
        saved['labels']['keep'] = False
        atomic_json(path, saved)
        result = build_extraction(self.raw.path)
        self.assertEqual(result.record['quality_status'], 'skipped')
        self.assertFalse(result.record['quality_gate_passed'])

    def test_corrupted_review_status_is_rejected(self):
        data = json.loads(self.claim_review.path.read_text())
        data['review']['claims'][0].update(verdict='needs_revision', issues=['wrong_kind'])
        atomic_json(self.claim_review.path, data)
        with self.assertRaises(PackageError):
            build_extraction(self.raw.path)

    def test_corrupted_cached_bundle_cannot_set_accepted_or_apply_graph(self):
        first = build_extraction(self.raw.path)
        data = json.loads(first.path.read_text())
        data['graph_status'] = 'applied'
        atomic_json(first.path, data)
        result = build_extraction(self.raw.path)
        self.assertFalse(result.reused)
        self.assertEqual(result.record['graph_status'], 'not_applied')

    def test_repaired_candidate_and_matching_review_are_selected(self):
        # Make initial review reject, then save an accepted correction.
        data = json.loads(self.claim_review.path.read_text())
        data['review']['claims'][0].update(verdict='needs_revision', issues=['wrong_kind'])
        data['review']['status'] = 'needs_revision'
        atomic_json(self.claim_review.path, data)
        quality = quality_capture(self.raw.path, FakeModel([response(), ModelReply(review_data(self.original.claims))]))
        result = build_extraction(self.raw.path)
        self.assertEqual(result.record['claim_candidate'], 'repairs')
        self.assertEqual(result.record['reviews']['claims'], quality.reviewed.review)
        self.assertTrue(result.record['quality_gate_passed'])
        # A higher valid candidate with a missing review must not fall back to approval.
        quality.reviewed.path.unlink()
        result = build_extraction(self.raw.path)
        self.assertEqual(result.record['claim_candidate'], 'repairs')
        self.assertFalse(result.record['quality_gate_passed'])

    def test_stale_derived_candidate_is_ignored_in_auto_but_explicit_selection_fails(self):
        record = json.loads(self.original.path.read_text())
        record.update(parent_extraction_hash='stale', parent_review_hash='stale')
        atomic_json(self.vault / 'repairs' / self.raw.path.name, record)
        result = build_extraction(self.raw.path)
        self.assertEqual(result.record['claim_candidate'], 'extractions')
        self.assertEqual(result.record['ignored_candidates'], [{'stage': 'repairs', 'reason': 'stale_parent'}])
        with self.assertRaises(PackageError):
            build_extraction(self.raw.path, claims_from='repairs')

    def test_inputs_from_another_source_are_not_packaged(self):
        record = json.loads(self.entities.path.read_text())
        record['item_id'] = 'wrong-source'
        atomic_json(self.entities.path, record)
        with self.assertRaises(PackageError):
            build_extraction(self.raw.path)

    def test_metadata_change_invalidates_bundle_even_if_text_unchanged(self):
        first = build_extraction(self.raw.path)
        raw = json.loads(self.raw.path.read_text())
        raw['author_hint'] = {'name': 'Corrected source author'}
        atomic_json(self.raw.path, raw)
        second = build_extraction(self.raw.path)
        self.assertFalse(second.reused)
        self.assertNotEqual(first.record['bundle_id'], second.record['bundle_id'])

    def test_storage_stub_is_idempotent_and_never_returns_graph_ids(self):
        result = build_extraction(self.raw.path)
        receipt = apply_storage_stub(result.path)
        path = self.vault / 'placements' / self.raw.path.name
        before = path.stat().st_mtime_ns
        again = apply_storage_stub(result.path)
        self.assertEqual(receipt, again)
        self.assertEqual(path.stat().st_mtime_ns, before)
        self.assertEqual(receipt['status'], 'stored_only')
        self.assertFalse(receipt['graph_applied'])
        self.assertEqual(receipt['node_ids'], [])
        self.assertEqual(receipt['edge_ids'], [])

    def test_stub_rejects_corrupt_identity_and_forged_gate(self):
        result = build_extraction(self.raw.path)
        data = deepcopy(result.record)
        data['bundle_id'] = 'invalid'
        atomic_json(result.path, data)
        with self.assertRaises(PlacementError):
            apply_storage_stub(result.path)
        data = deepcopy(result.record)
        data['reviews']['claims']['claims'][0].update(verdict='needs_revision', issues=['wrong_kind'])
        data['bundle_id'] = fingerprint({k: v for k, v in data.items() if k not in {'bundle_id', 'created_at'}})
        atomic_json(result.path, data)
        with self.assertRaises(PlacementError):
            apply_storage_stub(result.path)

    def test_cli_packages_without_configuring_or_calling_a_model(self):
        with patch('sys.argv', ['package', str(self.vault), '--item-id', self.raw.item_id]), redirect_stdout(StringIO()) as output:
            self.assertEqual(main(), 0)
        self.assertIn('graph_applied=False', output.getvalue())
        self.assertTrue((self.vault / 'processed' / self.raw.path.name).exists())


if __name__ == '__main__':
    unittest.main()
