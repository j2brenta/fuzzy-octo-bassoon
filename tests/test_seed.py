import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

import yaml

from channels.seed import import_seed, load_manifest

CORPUS = Path(__file__).resolve().parents[1] / 'eval' / 'seed_corpus'


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.corpus = self.root / 'corpus'
        shutil.copytree(CORPUS, self.corpus)
        self.manifest = self.corpus / 'manifest.yaml'
        self.vault = self.root / 'vault'

    def change(self, change):
        data = yaml.safe_load(self.manifest.read_text())
        change(data)
        self.manifest.write_text(yaml.safe_dump(data, sort_keys=False))

    def test_metadata_is_preserved_without_answer_key_leakage(self):
        items = load_manifest(self.manifest)
        results = import_seed(items, self.vault)
        self.assertEqual(len(results), 9)
        correction = json.loads(results[0][1].path.read_text())
        self.assertEqual(correction['text'], items[0].text)
        self.assertEqual(correction['captured_at'], '2026-10-19T10:00:00+00:00')
        self.assertEqual(correction['published_at'], '2026-10-16T09:00:00+00:00')
        self.assertEqual(correction['author_hint'], {'name': 'Demo Organizer'})
        self.assertEqual(correction['channel'], 'telegram')
        self.assertNotIn('expected', correction)
        self.assertNotIn('relations', correction)
        people = json.loads(results[3][1].path.read_text())
        self.assertIsNone(people['author_hint'])
        self.assertIsNone(people['published_at'])
        self.assertEqual(people['forwarded_by'], 'Kirill R')

    def test_retry_after_corpus_move_keeps_original_records(self):
        first = import_seed(load_manifest(self.manifest), self.vault)
        original = {result.path.name: result.path.read_bytes() for _, result in first}
        relocated = self.root / 'moved'
        shutil.copytree(self.corpus, relocated)
        second = import_seed(load_manifest(relocated / 'manifest.yaml'), self.vault)
        self.assertTrue(all(result.duplicate for _, result in second))
        self.assertEqual(len(list((self.vault / 'raw').iterdir())), 9)
        for _, result in second:
            self.assertEqual(result.path.read_bytes(), original[result.path.name])

    def test_missing_last_original_fails_before_any_capture(self):
        self.change(lambda d: d['items'][-1].update(file='originals/missing.txt'))
        with self.assertRaises(OSError):
            import_seed(load_manifest(self.manifest), self.vault)
        self.assertFalse(self.vault.exists())

    def test_empty_original_is_rejected(self):
        (self.corpus / 'originals/demo_original.txt').write_text('  \n')
        with self.assertRaisesRegex(ValueError, 'empty'):
            load_manifest(self.manifest)

    def test_duplicate_seed_ids_are_rejected(self):
        self.change(lambda d: d['items'][1].update(id=d['items'][0]['id']))
        with self.assertRaisesRegex(ValueError, 'Duplicate seed ID'):
            load_manifest(self.manifest)

    def test_duplicate_delivery_identities_are_rejected(self):
        def repeat(data):
            for key in ('channel', 'source_id', 'message_id'):
                data['items'][1][key] = data['items'][0][key]
        self.change(repeat)
        with self.assertRaisesRegex(ValueError, 'delivery identity'):
            load_manifest(self.manifest)

    def test_unknown_relation_target_is_rejected(self):
        self.change(lambda d: d['items'][0]['expected']['relations'][0].update(target='missing'))
        with self.assertRaisesRegex(ValueError, 'relation must target'):
            load_manifest(self.manifest)

    def test_file_outside_originals_is_rejected(self):
        (self.root / 'outside.txt').write_text('Outside corpus')
        self.change(lambda d: d['items'][0].update(file='../outside.txt'))
        with self.assertRaisesRegex(ValueError, 'inside originals'):
            load_manifest(self.manifest)

    def test_naive_timestamp_is_rejected(self):
        self.change(lambda d: d['items'][0].update(published_at='2026-10-16T09:00:00'))
        with self.assertRaisesRegex(ValueError, 'timezone'):
            load_manifest(self.manifest)

    def test_cli_validation_and_repeat_import(self):
        command = [sys.executable, '-m', 'channels.importer', str(self.corpus), '--vault', str(self.vault)]
        validate = subprocess.run(command + ['--validate-only'], capture_output=True, text=True)
        self.assertEqual(validate.returncode, 0, validate.stderr)
        self.assertIn('9 items; no records written', validate.stdout)
        self.assertFalse(self.vault.exists())
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn('9 new, 0 already captured', first.stdout)
        second = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn('0 new, 9 already captured', second.stdout)

    def test_invalid_yaml_reports_readable_error(self):
        self.manifest.write_text('items: [broken')
        result = subprocess.run([sys.executable, '-m', 'channels.importer', str(self.manifest)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Invalid YAML', result.stderr)
        self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
