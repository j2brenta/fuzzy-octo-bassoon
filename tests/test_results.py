import json
from pathlib import Path
import unittest

from engine.artifacts import atomic_json
from engine.results import entity_summary, write_item_note, _render_entities
from tests import test_package as fixtures


class ReadableResultTests(unittest.TestCase):
    setUp = fixtures.PackageTests.setUp

    def test_one_current_note_and_index_preserve_original_and_source_support(self):
        before = self.raw.path.read_bytes()
        note = write_item_note(self.raw.path)
        content = note.read_text()
        self.assertIn('**Status:** Accepted', content)
        self.assertIn(self.original.claims[0]['statement'], content)
        self.assertIn('Source passages: 1', content)
        self.assertIn('Your saved results', (note.parent / 'index.md').read_text())
        self.assertEqual(self.raw.path.read_bytes(), before)
        write_item_note(self.raw.path)
        self.assertEqual(note.read_text(), content)
        self.assertEqual(len(list(note.parent.glob(self.raw.item_id + '*.md'))), 1)

    def test_personal_annotations_survive_regeneration(self):
        note = write_item_note(self.raw.path)
        note.write_text(note.read_text() + '\nMy own follow-up question.\n')
        write_item_note(self.raw.path)
        self.assertEqual(note.read_text().count('My own follow-up question.'), 1)

    def test_incomplete_entity_stage_does_not_claim_that_no_people_exist(self):
        self.entities.path.unlink()
        note = write_item_note(self.raw.path)
        self.assertIn('Entity extraction has not finished yet.', note.read_text())
        self.assertIn('Processing incomplete', note.read_text())
        self.assertIn('| — | — |', (note.parent / 'index.md').read_text())

    def test_rejected_claim_is_visible_in_the_readable_result(self):
        review = json.loads(self.claim_review.path.read_text())
        review['review']['claims'][0].update(verdict='needs_revision', issues=['wrong_attribution'], reason='Speaker attribution needs correction.')
        review['review']['status'] = 'needs_revision'
        atomic_json(self.claim_review.path, review)
        text = write_item_note(self.raw.path).read_text()
        self.assertIn('**Status:** Needs revision', text)
        self.assertIn('Speaker attribution needs correction.', text)

    def test_names_aliases_and_review_are_readable_without_internal_ids(self):
        entities = [{'entity_id': 'a' * 64, 'name': 'Carlos Victorica', 'kind': 'person', 'aliases': ['Carlos'], 'mentions': [{}, {}]},
                    {'entity_id': 'b' * 64, 'name': 'Acme', 'kind': 'organization', 'mentions': [{}]}]
        review = {'status': 'accepted', 'summary': 'Names are grounded in the source.',
                  'entities': [{'entity_id': 'a' * 64, 'verdict': 'supported', 'reason': 'Supported.'},
                               {'entity_id': 'b' * 64, 'verdict': 'supported', 'reason': 'Supported.'}]}
        summary = entity_summary(entities, review)
        self.assertIn('Carlos Victorica (also: Carlos)', summary)
        self.assertIn('Organizations: 1', summary)
        self.assertNotIn('a' * 64, summary)
        table = '\n'.join(_render_entities(entities, review, True))
        self.assertIn('| Carlos Victorica | Carlos | 2 | Supported |', table)
        self.assertNotIn('a' * 64, table)

    def test_table_cells_escape_markup_and_review_findings_follow_the_table(self):
        rows = [{'entity_id': 'x', 'name': '<script>|Name', 'kind': 'organization', 'mentions': []}]
        review = {'entities': [{'entity_id': 'x', 'verdict': 'needs_revision', 'reason': 'Needs a type check.'}]}
        text = '\n'.join(_render_entities(rows, review, True))
        self.assertIn('&lt;script&gt;\\|Name', text)
        self.assertNotIn('<script>', text)
        self.assertIn('Needs a type check.', text)
