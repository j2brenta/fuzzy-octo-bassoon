"""Regression cases from Kirill's integration trial, without paid model calls."""
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine.artifacts import atomic_json
from engine.entities import extract_entities, review_entities, validate_entities, validate_entity_review
from engine.judge import judge_capture, validate_review
from engine.llm import ModelError, ModelReply, client_from_environment, judge_client_from_environment
from engine.package import build_extraction
from engine.placement import apply_storage_stub
from engine.quality import quality_capture
from engine.triage import SYSTEM_PROMPT as TRIAGE_PROMPT, triage_capture
from refresher.schemas import AuthorHint
from store.raw import capture_text
from tests.test_extract import TEXT, response
from tests.test_judge import review_data
from tests.test_entities import entity_review
from tests import test_package as package_fixtures
from tests.test_triage import FakeModel, LABELS


class CorrectnessGateTests(unittest.TestCase):
    setUp = package_fixtures.PackageTests.setUp
    # Reuse source/artifact fixtures while adding policy-specific end-to-end checks.
    def test_all_correct_claims_with_coverage_notes_reach_storage_handoff(self):
        record = json.loads(self.claim_review.path.read_text())
        record['review']['omitted_concepts'] = [{'passage_id': 1, 'description': 'Another central source idea.'}]
        record['review']['status'] = 'accepted'
        atomic_json(self.claim_review.path, record)
        result = build_extraction(self.raw.path)
        self.assertEqual(result.record['quality_status'], 'accepted')
        self.assertTrue(result.record['quality_gate_passed'])
        self.assertTrue(result.record['coverage_notes']['claims'])
        self.assertEqual(apply_storage_stub(result.path)['status'], 'stored_only')

    def test_correctness_findings_still_block_the_complete_storage_handoff(self):
        original = json.loads(self.claim_review.path.read_text())
        cases = [('needs_revision', issue) for issue in (
            'wrong_evidence', 'wrong_attribution', 'lost_qualification',
            'not_atomic', 'wrong_kind', 'wrong_durability')]
        cases += [('unsupported', 'unsupported'), ('duplicate', 'duplicate')]
        for verdict, issue in cases:
            with self.subTest(issue=issue):
                record = deepcopy(original)
                record['review']['claims'][0].update(verdict=verdict, issues=[issue])
                record['review']['status'] = 'needs_revision'
                atomic_json(self.claim_review.path, record)
                result = build_extraction(self.raw.path)
                self.assertEqual(result.record['quality_status'], 'needs_revision')
                self.assertFalse(result.record['quality_gate_passed'])
                self.assertEqual(apply_storage_stub(result.path)['status'], 'pending_quality')

    def test_copied_header_metadata_refresh_preserves_paid_claim_caches(self):
        handoff = build_extraction(self.raw.path)
        apply_storage_stub(handoff.path)
        document_path = self.vault / 'documents' / self.raw.path.name
        document = json.loads(document_path.read_text())
        document['telegram_copy'] = {'messages': [{'copied_at': '2026-10-07T12:00:00+02:00'}]}
        before = self.claim_review.path.read_bytes()
        atomic_json(document_path, document)
        self.assertEqual(before, self.claim_review.path.read_bytes())
        self.assertTrue(self.original.path.exists())
        self.assertTrue(self.entities.path.exists())
        self.assertFalse(handoff.path.exists())
        self.assertFalse((self.vault / 'placements' / self.raw.path.name).exists())

    def test_changed_entities_invalidate_their_review_without_discarding_claim_review(self):
        handoff = build_extraction(self.raw.path)
        apply_storage_stub(handoff.path)
        record = json.loads(self.entities.path.read_text())
        record['created_at'] = 'regenerated'
        atomic_json(self.entities.path, record)
        self.assertTrue(self.original.path.exists())
        self.assertTrue(self.claim_review.path.exists())
        self.assertFalse(self.entity_review.path.exists())
        self.assertFalse(handoff.path.exists())
        result = build_extraction(self.raw.path)
        self.assertEqual(result.record['quality_status'], 'pending')
        self.assertEqual(apply_storage_stub(result.path)['status'], 'pending_quality')

    def test_old_omission_only_rejection_is_reassessed_without_forging_a_new_model_review(self):
        record = json.loads(self.claim_review.path.read_text())
        record.pop('quality_policy_version', None)
        record['review']['omitted_concepts'] = [{'passage_id': 1, 'description': 'Additional source idea.'}]
        record['review']['status'] = 'needs_revision'
        atomic_json(self.claim_review.path, record)
        before = self.claim_review.path.read_bytes()
        result = build_extraction(self.raw.path)
        self.assertEqual(result.record['reviews']['claims']['status'], 'accepted')
        self.assertTrue(result.record['quality_gate_passed'])
        self.assertEqual(self.claim_review.path.read_bytes(), before)

    def test_advisory_omissions_do_not_trigger_automatic_paid_expansion(self):
        record = json.loads(self.claim_review.path.read_text())
        record['review']['omitted_concepts'] = [{'passage_id': 1, 'description': 'More context could be extracted.'}]
        atomic_json(self.claim_review.path, record)
        client = FakeModel([])
        result = quality_capture(self.raw.path, client)
        self.assertFalse(result.expanded)
        self.assertEqual(client.calls, [])
        self.assertEqual(result.reviewed.review['status'], 'accepted')

    def test_changed_extraction_archives_stale_stages_but_preserves_raw_and_entities(self):
        result = build_extraction(self.raw.path)
        apply_storage_stub(result.path)
        vault = self.raw.path.parent.parent
        for folder in ['repairs', 'coverage', 'evidence']:
            path = vault / folder / self.raw.path.name
            path.parent.mkdir(exist_ok=True)
            path.write_text('{"stale": true}')
        before = self.raw.path.read_bytes(), self.entities.path.read_bytes()
        original = json.loads(self.original.path.read_text())
        original['created_at'] = 'regenerated'
        atomic_json(self.original.path, original)
        for folder in ['reviews', 'repairs', 'coverage', 'evidence', 'processed', 'placements']:
            self.assertFalse((vault / folder / self.raw.path.name).exists(), folder)
        self.assertEqual(before, (self.raw.path.read_bytes(), self.entities.path.read_bytes()))
        archived = list((vault / 'stale' / self.raw.item_id).rglob(self.raw.path.name))
        self.assertEqual(len(archived), 6)

    def test_concept_findings_remain_visible_without_blocking_processed_handoff(self):
        client = FakeModel([ModelReply({'entities': [
            {'name': 'AI', 'kind': 'concept', 'evidence_passage_ids': [1]}
        ]})])
        client.model_name = 'concept-extractor-fixture'
        entities = extract_entities(self.raw.path, client)
        data = entity_review(entities.entities)
        data['entities'][0].update(verdict='needs_revision', reason='Too broad to be useful as a concept.')
        review_entities(self.raw.path, FakeModel([ModelReply(data)]), entities)
        record = build_extraction(self.raw.path)
        self.assertTrue(record.record['quality_gate_passed'])
        self.assertTrue(record.record['concept_review_notes'])
        self.assertEqual(apply_storage_stub(record.path)['status'], 'stored_only')

    def test_identical_artifact_write_preserves_downstream_cache(self):
        result = build_extraction(self.raw.path)
        atomic_json(self.original.path, json.loads(self.original.path.read_text()))
        self.assertTrue(self.claim_review.path.exists())
        self.assertTrue(result.path.exists())

    def test_new_forwarder_matches_author_hint_contract(self):
        raw = capture_text('Forwarded content.', self.vault, channel='telegram', source_id='fixture:typed-forwarder', source_name='Forward', forwarded_by={'name': 'Olga', 'url': 'tg://user?id=100'})
        record = json.loads(raw.path.read_text())
        self.assertEqual(AuthorHint.model_validate(record['forwarded_by']).name, 'Olga')
        self.assertIsNone(record['author_hint'])


class EntityMechanicsTests(unittest.TestCase):
    def test_duplicate_alias_labels_are_removed_without_rejecting_the_entity(self):
        text = 'Carlos Victorica speaks.\nCarlos answers.'
        aliases = [{'name': 'Carlos Victorica', 'evidence_passage_ids': [1]}, {'name': 'Carlos', 'evidence_passage_ids': [2]}, {'name': 'Carlos', 'evidence_passage_ids': [2]}]
        result = validate_entities({'entities': [{'name': 'Carlos Victorica', 'kind': 'person', 'evidence_passage_ids': [1], 'aliases': aliases}]}, text, 'item', 'section')
        self.assertEqual(result[0]['aliases'], ['Carlos'])
        self.assertEqual(len(result[0]['mentions']), 2)

    def test_wrong_concepts_are_advisory_but_wrong_people_still_block(self):
        entities = validate_entities({'entities': [{'name': 'Carlos', 'kind': 'person', 'evidence_passage_ids': [1]}, {'name': 'retrieval', 'kind': 'concept', 'evidence_passage_ids': [1]}]}, 'Carlos studies retrieval.', 'item', 'section')
        rows = [{'entity_id': e['entity_id'], 'verdict': 'supported', 'reason': 'Supported.'} for e in entities]
        rows[1]['verdict'] = 'needs_revision'
        data = {'entities': rows, 'omitted_entities': [], 'summary': 'A concept needs refinement.'}
        result = validate_entity_review(data, entities, 'Carlos studies retrieval.', 'item', 'section')
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['entities'][1]['verdict'], 'needs_revision')
        rows[0]['verdict'] = 'needs_revision'
        self.assertEqual(validate_entity_review(data, entities, 'Carlos studies retrieval.', 'item', 'section')['status'], 'needs_revision')


class NebiusConfigurationTests(unittest.TestCase):
    def test_super_ultra_defaults_and_configurable_token_budgets(self):
        env = {'TRIAGE_PROVIDER': 'nebius', 'JUDGE_PROVIDER': 'nebius', 'NEBIUS_API_KEY': 'fixture-key', 'NEBIUS_MAX_TOKENS': '24000', 'NEBIUS_JUDGE_MAX_TOKENS': '48000'}
        with patch.dict(os.environ, env, clear=True):
            extraction, judge = client_from_environment(), judge_client_from_environment()
        self.assertEqual(extraction.model_name, 'nvidia/nemotron-3-super-120b-a12b')
        self.assertEqual(judge.model_name, 'nvidia/Nemotron-3-Ultra-550b-a55b')
        self.assertEqual((extraction.max_tokens, judge.max_tokens), (24000, 48000))
        self.assertNotIn('fixture-key', repr(judge))

    def test_invalid_token_budgets_fail_before_any_request(self):
        for value in ['0', '-3', 'abc', '1.5', '131073']:
            with self.subTest(value=value), patch.dict(os.environ, {'TRIAGE_PROVIDER': 'nebius', 'NEBIUS_API_KEY': 'fixture', 'NEBIUS_MAX_TOKENS': value}, clear=True), self.assertRaises(ModelError):
                client_from_environment()


if __name__ == '__main__':
    unittest.main()
