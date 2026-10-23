from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.entities import EntityError, _cached_entities, validate_entities, validate_entity_review, extract_entities
from tests.test_entities import entity_review
from tests.test_triage import FakeModel
from engine.llm import ModelReply
from store.raw import capture_text

TEXT = 'Carlos Victorica leads the session.\nCarlos explains sales. Carlos answers questions.\nCarlos Diaz attends another session.'
FULL = {'name': 'Carlos Victorica', 'kind': 'person', 'evidence_passage_ids': [1]}
MERGED = {**FULL, 'aliases': [{'name': 'Carlos', 'evidence_passage_ids': [2]}]}


class EntityAliasTests(unittest.TestCase):
    def validate(self, rows, text=TEXT):
        return validate_entities({'entities': rows}, text, 'item', 'section')

    def test_full_name_and_alias_become_one_entity_with_all_exact_mentions(self):
        entity = self.validate([MERGED])[0]
        self.assertEqual(entity['name'], 'Carlos Victorica')
        self.assertEqual(entity['aliases'], ['Carlos'])
        self.assertEqual(entity['identity_status'], 'resolved_within_item')
        self.assertEqual(len(entity['mentions']), 3)
        for mention in entity['mentions']:
            self.assertEqual(TEXT[mention['start_char']:mention['end_char']], mention['surface_name'])
        self.assertEqual([m['surface_name'] for m in entity['mentions']], ['Carlos Victorica', 'Carlos', 'Carlos'])

    def test_canonical_full_name_id_remains_stable_after_merge(self):
        old = self.validate([FULL])[0]
        merged = self.validate([MERGED])[0]
        self.assertEqual(old['entity_id'], merged['entity_id'])

    def test_merged_entities_survive_cache_validation_without_losing_mentions(self):
        entities = self.validate([MERGED])
        self.assertEqual(_cached_entities(entities, TEXT, 'item', 'section'), entities)
        bad = deepcopy(entities)
        bad[0]['mentions'][1]['start_char'] += 1
        with self.assertRaises(EntityError):
            _cached_entities(bad, TEXT, 'item', 'section')

    def test_alias_inside_canonical_full_name_is_not_a_second_mention(self):
        row = {**FULL, 'aliases': [{'name': 'Carlos', 'evidence_passage_ids': [1, 2]}]}
        entity = self.validate([row])[0]
        self.assertEqual(len(entity['mentions']), 3)
        self.assertEqual(entity['mentions'][0]['surface_name'], 'Carlos Victorica')
        self.assertEqual(sum(m['passage_id'] == 1 for m in entity['mentions']), 1)

    def test_alias_without_a_standalone_mention_is_rejected(self):
        row = {**FULL, 'aliases': [{'name': 'Carlos', 'evidence_passage_ids': [1]}]}
        with self.assertRaises(EntityError):
            self.validate([row])

    def test_invented_alias_or_invalid_alias_evidence_is_rejected(self):
        for aliases in [
            [{'name': 'Carl', 'evidence_passage_ids': [2]}],
            [{'name': 'Carlos', 'evidence_passage_ids': [True]}],
            [{'name': 'Carlos', 'evidence_passage_ids': [2], 'guess': True}],
            'Carlos',
        ]:
            with self.subTest(aliases=aliases), self.assertRaises(EntityError):
                self.validate([{**FULL, 'aliases': aliases}])

    def test_similar_names_are_not_automatically_merged_by_python(self):
        # The source contains two Carloses. Exact-name matching alone must not merge them.
        entities = self.validate([FULL, {'name': 'Carlos Diaz', 'kind': 'person', 'evidence_passage_ids': [3]}])
        self.assertEqual(len(entities), 2)
        self.assertNotEqual(entities[0]['entity_id'], entities[1]['entity_id'])
        self.assertTrue(all(e['identity_status'] == 'local_mention_not_resolved' for e in entities))

    def test_alias_choice_does_not_change_canonical_id_or_source_order(self):
        text = 'Carlos Victorica leads.\nCarlos speaks.\nVictorica replies.'
        first = {**FULL, 'aliases': [{'name': 'Carlos', 'evidence_passage_ids': [2]}, {'name': 'Victorica', 'evidence_passage_ids': [3]}]}
        second = {**first, 'aliases': list(reversed(first['aliases']))}
        self.assertEqual(self.validate([first], text), self.validate([second], text))

    def test_review_cannot_call_an_existing_alias_a_missing_entity(self):
        entities = self.validate([MERGED])
        data = entity_review(entities, omitted_entities=[{'name': 'Carlos', 'kind': 'person', 'evidence_passage_ids': [2]}])
        with self.assertRaises(EntityError):
            validate_entity_review(data, entities, TEXT, 'item', 'section')

    def test_suspected_unsafe_merge_stays_needs_revision(self):
        entities = self.validate([MERGED])
        data = entity_review(entities)
        data['entities'][0].update(verdict='needs_revision', reason='Context does not establish which Carlos this alias refers to.')
        reviewed = validate_entity_review(data, entities, TEXT, 'item', 'section')
        self.assertEqual(reviewed['status'], 'needs_revision')

    def test_retry_receives_failed_output_and_entry_specific_error(self):
        bad = {**MERGED, 'evidence_passage_ids': [3]}
        client = FakeModel([ModelReply({'entities': [bad]}), ModelReply({'entities': [MERGED]})])
        with TemporaryDirectory() as folder:
            raw = capture_text(TEXT, Path(folder), channel='file', source_id='fixture:alias-retry', source_name='Synthetic alias retry')
            result = extract_entities(raw.path, client)
            self.assertEqual(len(result.entities), 1)
            self.assertIn('Entity entry 1', client.calls[1][0])
            self.assertEqual(json.loads(client.calls[1][1])['previous_output'], {'entities': [bad]})

    def test_cross_article_entities_are_never_merged(self):
        first = self.validate([MERGED])[0]
        second = validate_entities({'entities': [MERGED]}, TEXT, 'other-item', 'section')[0]
        self.assertNotEqual(first['entity_id'], second['entity_id'])


if __name__ == '__main__':
    unittest.main()
