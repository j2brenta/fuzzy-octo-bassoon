from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from aiogram.types import Message

from channels.telegram import handle_message
from engine.entities import EntityError, entity_client_from_environment, extract_entities, review_entities, validate_entities, validate_entity_review
from engine.llm import ModelError, ModelReply
from store.raw import capture_text
from tests.test_extract import TEXT as CLAIM_TEXT, response
from tests.test_judge import review_data
from tests.test_telegram import message
from tests.test_triage import FakeModel, LABELS

TEXT = '💡 Maya at Acme discusses retrieval.\nMaya says AI may help retrieval.\nDaniel uses GMAIL.'
ROWS = [
    {'name': 'Maya', 'kind': 'person', 'evidence_passage_ids': [1, 2]},
    {'name': 'Acme', 'kind': 'organization', 'evidence_passage_ids': [1]},
    {'name': 'retrieval', 'kind': 'concept', 'evidence_passage_ids': [1, 2]},
]


def entity_review(entities, **changes):
    return {'entities': [{'entity_id': e['entity_id'], 'verdict': 'supported', 'reason': 'Explicit source mention with the correct type.'} for e in entities],
            'omitted_entities': [], 'summary': 'Source entity mentions are supported.', **changes}


class EntityTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.vault = Path(folder.name)
        self.raw = capture_text(TEXT, self.vault, channel='file', source_id='fixture:entities', source_name='Synthetic entity fixture')

    def extract(self, client=None):
        return extract_entities(self.raw.path, client or FakeModel([ModelReply({'entities': ROWS})]))

    def test_mentions_have_exact_unicode_spans_without_identity_inference(self):
        before = self.raw.path.read_bytes()
        result = self.extract()
        self.assertEqual([e['kind'] for e in result.entities], ['person', 'organization', 'concept'])
        self.assertEqual(len(result.entities[0]['mentions']), 2)
        for entity in result.entities:
            self.assertEqual(entity['identity_status'], 'local_mention_not_resolved')
            for mention in entity['mentions']:
                self.assertEqual(TEXT[mention['start_char']:mention['end_char']], entity['name'])
                self.assertEqual(mention['item_id'], self.raw.item_id)
        self.assertEqual(self.raw.path.read_bytes(), before)

    def test_invented_names_partial_word_matches_and_wrong_passages_are_rejected(self):
        for row in [
            {'name': 'Dan', 'kind': 'person', 'evidence_passage_ids': [3]},
            {'name': 'AI', 'kind': 'concept', 'evidence_passage_ids': [3]},
            {'name': 'Maya Smith', 'kind': 'person', 'evidence_passage_ids': [1]},
            {'name': 'Maya', 'kind': 'person', 'evidence_passage_ids': [3]},
        ]:
            with self.subTest(row=row), self.assertRaises(EntityError):
                validate_entities({'entities': [row]}, TEXT, 'item', 'section')

    def test_invalid_fields_ids_kinds_and_duplicates_are_rejected(self):
        variants = [
            [{**ROWS[0], 'evidence_passage_ids': [True]}],
            [{**ROWS[0], 'evidence_passage_ids': [1, 1]}],
            [{**ROWS[0], 'evidence_passage_ids': [99]}],
            [{**ROWS[0], 'kind': 'author'}],
            [{**ROWS[0], 'name': ' Maya'}],
            [{**ROWS[0], 'affiliation': 'Acme'}],
            [ROWS[0], ROWS[0]], ROWS * 14,
        ]
        for rows in variants:
            with self.subTest(rows=rows), self.assertRaises(EntityError):
                validate_entities({'entities': rows}, TEXT, 'item', 'section')

    def test_reordered_passage_ids_have_stable_entities(self):
        first = validate_entities({'entities': ROWS}, TEXT, 'item', 'section')
        rows = deepcopy(ROWS)
        rows[0]['evidence_passage_ids'].reverse()
        self.assertEqual(first, validate_entities({'entities': rows}, TEXT, 'item', 'section'))

    def test_empty_source_entity_set_is_allowed(self):
        result = self.extract(FakeModel([ModelReply({'entities': []})]))
        self.assertEqual(result.entities, [])

    def test_successful_extraction_is_cached(self):
        client = FakeModel([ModelReply({'entities': ROWS})])
        first = self.extract(client)
        second = self.extract(client)
        self.assertTrue(second.reused)
        self.assertEqual(first.entities, second.entities)
        self.assertEqual(len(client.calls), 1)

    def test_corrupt_cached_mention_is_rebuilt(self):
        result = self.extract()
        data = json.loads(result.path.read_text())
        data['entities'][0]['mentions'][0]['start_char'] = 999
        result.path.write_text(json.dumps(data))
        rebuilt = self.extract()
        self.assertFalse(rebuilt.reused)
        self.assertEqual(result.entities, rebuilt.entities)

    def test_invalid_output_retries_once_and_can_be_corrected(self):
        client = FakeModel([ModelReply({'invalid': []}), ModelReply({'entities': ROWS})])
        result = self.extract(client)
        self.assertEqual(len(result.entities), 3)
        self.assertIn('Previous output was invalid', client.calls[1][0])

    def test_failed_extraction_saves_no_false_success(self):
        client = FakeModel([ModelReply({'invalid': []}), ModelReply({'invalid': []})])
        with self.assertRaises(EntityError):
            self.extract(client)
        self.assertFalse((self.vault / 'entities').exists())
        self.assertTrue(self.raw.path.exists())

    def test_service_failure_does_not_retry(self):
        client = FakeModel([ModelError('Service unavailable.')])
        with self.assertRaises(ModelError):
            self.extract(client)
        self.assertEqual(len(client.calls), 1)

    def test_entity_review_uses_entire_source_and_is_cached(self):
        result = self.extract()
        client = FakeModel([ModelReply(entity_review(result.entities))])
        first = review_entities(self.raw.path, client, result)
        second = review_entities(self.raw.path, client, result)
        self.assertEqual(first.review['status'], 'accepted')
        self.assertTrue(second.reused)
        self.assertEqual(len(client.calls), 1)
        payload = json.loads(client.calls[0][1])
        self.assertEqual(len(payload['passages']), 3)
        self.assertEqual(payload['entities'], [{**e, 'entity_id': f'E{n}'} for n, e in enumerate(result.entities, 1)])
        self.assertNotIn('review', payload)

    def test_review_answers_with_short_ids_are_mapped_back(self):
        result = self.extract()
        data = entity_review(result.entities)
        short = {row['entity_id']: f'E{n}' for n, row in enumerate(result.entities, 1)}
        data['entities'] = [{**row, 'entity_id': short[row['entity_id']]} for row in data['entities']]
        review = review_entities(self.raw.path, FakeModel([ModelReply(data)]), result).review
        self.assertEqual([r['entity_id'] for r in review['entities']], [e['entity_id'] for e in result.entities])

    def test_review_requires_every_entity_exactly_once(self):
        entities = self.extract().entities
        data = entity_review(entities)
        for rows in [data['entities'][:1], data['entities'] * 2,
                     [data['entities'][0]] * 3, [{**r, 'entity_id': 'unknown'} for r in data['entities']]]:
            with self.subTest(rows=rows), self.assertRaises(EntityError):
                validate_entity_review({**data, 'entities': rows}, entities, TEXT, 'item', 'section')

    def test_wrong_people_block_acceptance_but_omissions_are_advisory(self):
        entities = self.extract().entities
        data = entity_review(entities)
        data['entities'][0]['verdict'] = 'needs_revision'
        self.assertEqual(validate_entity_review(data, entities, TEXT, 'item', 'section')['status'], 'needs_revision')
        omitted = [{'name': 'AI', 'kind': 'concept', 'evidence_passage_ids': [2]}]
        data = entity_review(entities, omitted_entities=omitted)
        self.assertEqual(validate_entity_review(data, entities, TEXT, 'item', 'section')['status'], 'accepted')
        self.assertTrue(validate_entity_review(data, entities, TEXT, 'item', 'section')['omitted_entities'])
        with self.assertRaises(EntityError):
            validate_entity_review(entity_review(entities, omitted_entities=[ROWS[0]]), entities, TEXT, 'item', 'section')

    def test_omissions_cannot_invent_names_or_reference_missing_passages(self):
        entities = self.extract().entities
        for name, ids in [('Not named', [1]), ('AI', [99])]:
            data = entity_review(entities, omitted_entities=[{'name': name, 'kind': 'concept', 'evidence_passage_ids': ids}])
            with self.subTest(name=name), self.assertRaises(EntityError):
                validate_entity_review(data, entities, TEXT, 'item', 'section')

    def test_corrupt_cached_acceptance_cannot_hide_flagged_entity(self):
        result = self.extract()
        data = entity_review(result.entities)
        data['entities'][0]['verdict'] = 'needs_revision'
        first = review_entities(self.raw.path, FakeModel([ModelReply(data)]), result)
        cached = json.loads(first.path.read_text())
        cached['review']['status'] = 'accepted'
        first.path.write_text(json.dumps(cached))
        rebuilt = review_entities(self.raw.path, FakeModel([ModelReply(data)]), result)
        self.assertFalse(rebuilt.reused)
        self.assertEqual(rebuilt.review['status'], 'needs_revision')

    def test_changed_entity_artifact_invalidates_review(self):
        result = self.extract()
        client = FakeModel([ModelReply(entity_review(result.entities)), ModelReply(entity_review(result.entities))])
        review_entities(self.raw.path, client, result)
        record = json.loads(result.path.read_text())
        record['created_at'] = 'changed'
        result.path.write_text(json.dumps(record))
        self.assertFalse(review_entities(self.raw.path, client, result).reused)
        self.assertEqual(len(client.calls), 2)

    def test_failed_review_preserves_extraction_and_never_accepts(self):
        result = self.extract()
        client = FakeModel([ModelReply({}), ModelReply({})])
        with self.assertRaises(EntityError):
            review_entities(self.raw.path, client, result)
        self.assertTrue(result.path.exists())
        self.assertFalse((self.vault / 'entity-reviews').exists())

    def test_configuration_reuses_strong_model_and_can_be_disabled(self):
        with patch.dict(os.environ, {'ENTITY_EXTRACTION': 'off'}, clear=True):
            self.assertIsNone(entity_client_from_environment())
        with patch.dict(os.environ, {'ENTITY_EXTRACTION': 'on', 'JUDGE_PROVIDER': 'nebius', 'NEBIUS_API_KEY': 'fixture-secret'}, clear=True):
            self.assertEqual(entity_client_from_environment().model_name, 'nvidia/Nemotron-3-Ultra-550b-a55b')


class TelegramEntityTests(unittest.IsolatedAsyncioTestCase):
    async def test_entities_get_a_separate_review_and_reply(self):
        with TemporaryDirectory() as folder:
            extractor = FakeModel([ModelReply(dict(LABELS)), response()])
            class ClaimJudge(FakeModel):
                def complete_json(self, prompt, content):
                    return ModelReply(review_data(json.loads(content)['claims']))
            class EntityModel(FakeModel):
                def complete_json(self, prompt, content):
                    data = json.loads(content)
                    return ModelReply(entity_review(data['entities']) if 'entities' in data else {'entities': []})
            with patch('channels.telegram.client_from_environment', return_value=extractor), patch('channels.telegram.judge_client_from_environment', return_value=ClaimJudge()), patch('channels.telegram.entity_client_from_environment', return_value=EntityModel()), patch.object(Message, 'answer', new_callable=AsyncMock) as answer:
                await handle_message(message(text=CLAIM_TEXT), Path(folder), 100)
                self.assertEqual(answer.await_count, 7)
                self.assertIn('AI entity review: accepted', answer.await_args_list[-2].args[0])
                self.assertIn('Combined extraction saved', answer.await_args_list[-1].args[0])
                self.assertTrue(list((Path(folder) / 'processed').glob('*.json')))

    async def test_entity_failure_preserves_claims_and_reports_pending(self):
        with TemporaryDirectory() as folder:
            extractor = FakeModel([ModelReply(dict(LABELS)), response()])
            class ClaimJudge(FakeModel):
                def complete_json(self, prompt, content):
                    return ModelReply(review_data(json.loads(content)['claims']))
            with patch('channels.telegram.client_from_environment', return_value=extractor), patch('channels.telegram.judge_client_from_environment', return_value=ClaimJudge()), patch('channels.telegram.entity_client_from_environment', return_value=FakeModel([ModelError('Service unavailable.')])), patch.object(Message, 'answer', new_callable=AsyncMock) as answer:
                vault = Path(folder)
                await handle_message(message(text=CLAIM_TEXT), vault, 100)
                self.assertIn('entity extraction or review is pending', answer.await_args_list[-1].args[0])
                self.assertTrue(list((vault / 'extractions').glob('*.json')))


if __name__ == '__main__':
    unittest.main()
