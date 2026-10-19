import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from engine.parse import parse_capture
from store.raw import capture_text


class TextParseTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.vault = Path(self.directory.name) / 'vault'

    def capture(self, text, metadata=None):
        return capture_text(text, self.vault, channel='telegram', source_id='fixture', source_name='Fixture', channel_metadata=metadata)

    def parse(self, text, metadata=None):
        raw = self.capture(text, metadata)
        original = raw.path.read_bytes()
        result = parse_capture(raw.path)
        self.assertEqual(raw.path.read_bytes(), original)
        return raw, result, json.loads(result.path.read_text())

    def test_text_is_referenced_once_without_copying_it_into_document(self):
        text = '  My original text.\r\nПривет!\n'
        raw, result, document = self.parse(text)
        self.assertEqual(result.section_count, 1)
        section = document['sections'][0]
        ref = section['provenance']
        self.assertEqual(ref['item_id'], raw.item_id)
        self.assertEqual(ref['field'], 'text')
        self.assertEqual(text[ref['start_char']:ref['end_char']], text)
        self.assertNotIn('text', section)
        self.assertNotIn('text', document)
        self.assertEqual(document['raw_path'], f'raw/{raw.item_id}.json')

    def test_two_links_in_russian_text_are_identified(self):
        text = 'Отчёт об AI.\nhttps://example.com/report.pdf\nhttps://youtu.be/demo'
        _, result, document = self.parse(text)
        self.assertEqual(result.link_count, 2)
        self.assertEqual([link['url'] for link in document['links']], ['https://example.com/report.pdf', 'https://youtu.be/demo'])
        for link in document['links']:
            self.assertEqual(text[link['start_char']:link['end_char']], link['url'])

    def test_markdown_and_sentence_punctuation_are_excluded(self):
        _, _, document = self.parse('[Read](https://example.com/report.pdf). And https://example.com/next!')
        self.assertEqual([link['url'] for link in document['links']], ['https://example.com/report.pdf', 'https://example.com/next'])

    def test_balanced_parentheses_inside_url_are_preserved(self):
        url = 'https://example.com/Function_(mathematics)'
        _, _, document = self.parse(f'Read ({url}).')
        self.assertEqual(document['links'][0]['url'], url)

    def test_hidden_telegram_link_after_emoji_has_correct_span(self):
        text = '💡 Read the report'
        label = 'report'
        start = text.index(label)
        offset = len(text[:start].encode('utf-16-le')) // 2
        metadata = {'entities': [{'type': 'text_link', 'offset': offset, 'length': len(label), 'url': 'https://example.com/report.pdf'}]}
        _, _, document = self.parse(text, metadata)
        link = document['links'][0]
        self.assertEqual(text[link['start_char']:link['end_char']], label)
        self.assertEqual(link['url'], 'https://example.com/report.pdf')
        self.assertEqual(link['kind'], 'text_link')

    def test_url_detected_by_entity_and_regex_is_not_duplicated(self):
        text = '💡 https://example.com'
        metadata = {'entities': [{'type': 'url', 'offset': 3, 'length': len('https://example.com')}]}
        _, result, _ = self.parse(text, metadata)
        self.assertEqual(result.link_count, 1)

    def test_repeat_preparation_leaves_unchanged_document_untouched(self):
        raw, first, _ = self.parse('A note')
        timestamp = first.path.stat().st_mtime_ns
        second = parse_capture(raw.path)
        self.assertTrue(second.reused)
        self.assertEqual(second.path.stat().st_mtime_ns, timestamp)

    def test_derived_document_can_be_rebuilt(self):
        raw, result, document = self.parse('A note https://example.com')
        result.path.write_text('broken derived content')
        rebuilt = parse_capture(raw.path)
        self.assertFalse(rebuilt.reused)
        self.assertEqual(json.loads(rebuilt.path.read_text()), document)

    def test_changed_source_text_is_rejected_without_writing_document(self):
        raw = self.capture('Original text')
        record = json.loads(raw.path.read_text())
        record['text'] = 'Changed text'
        raw.path.write_text(json.dumps(record))
        before = raw.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'content hash'):
            parse_capture(raw.path)
        self.assertEqual(raw.path.read_bytes(), before)
        self.assertFalse((self.vault / 'documents').exists())

    def test_invalid_entity_span_is_rejected_without_modifying_raw(self):
        raw = self.capture('A note', {'entities': [{'type': 'text_link', 'offset': 100, 'length': 1, 'url': 'https://example.com'}]})
        before = raw.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'outside'):
            parse_capture(raw.path)
        self.assertEqual(raw.path.read_bytes(), before)
        self.assertFalse((self.vault / 'documents').exists())

    def test_entity_cannot_split_an_emoji(self):
        raw = self.capture('💡 report', {'entities': [{'type': 'text_link', 'offset': 1, 'length': 2, 'url': 'https://example.com'}]})
        with self.assertRaisesRegex(ValueError, 'Unicode'):
            parse_capture(raw.path)

    def test_cli_processes_saved_captures_and_can_be_repeated(self):
        self.capture('A note https://example.com')
        command = [sys.executable, '-m', 'engine.parse', str(self.vault)]
        first = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn('1 section(s), 1 link(s)', first.stdout)
        second = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn('Already prepared', second.stdout)


if __name__ == '__main__':
    unittest.main()
