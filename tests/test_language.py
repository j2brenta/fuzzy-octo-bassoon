import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from engine import entities, extract, judge, language, triage
from engine.extract import extract_capture
from engine.triage import triage_capture
from store.raw import capture_text
from tests.test_extract import response
from tests.test_triage import FakeModel

TEXT = 'Автор советует начинать копить как можно раньше.'


class LanguageTests(unittest.TestCase):
    def test_the_setting_defaults_to_english_and_accepts_source(self):
        self.assertEqual(language.code({}), 'en')
        self.assertEqual(language.name({'REFRESHER_LANGUAGE': 'DE'}), 'German')
        self.assertEqual(language.name({'REFRESHER_LANGUAGE': 'source'}), 'the source language')
        self.assertEqual(language.code({'REFRESHER_LANGUAGE': 'klingon'}), 'en')

    def test_every_prompt_with_the_marker_is_filled_where_it_is_sent(self):
        for module in (extract, judge, entities, triage):
            source = Path(module.__file__).read_text(encoding='utf-8')
            if language.MARKER in source:
                self.assertIn('language.apply(', source, module.__name__)
        self.assertNotIn(language.MARKER, language.apply(judge.SYSTEM_PROMPT, {'REFRESHER_LANGUAGE': 'fr'}))
        self.assertIn('French', language.apply(judge.SYSTEM_PROMPT, {'REFRESHER_LANGUAGE': 'fr'}))


class OutputLanguageInStagesTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.raw = capture_text(TEXT, Path(self.directory.name), channel='file', source_id='fixture', source_name='Fixture')

    def test_claims_are_asked_for_in_the_configured_language_and_a_change_re_extracts(self):
        client = FakeModel([response(), response()])
        with patch.dict(os.environ, {'REFRESHER_LANGUAGE': 'en'}):
            extract_capture(self.raw.path, client)
            self.assertTrue(extract_capture(self.raw.path, client).reused)
        prompt = client.calls[0][0]
        self.assertIn('in English, whatever language the source is in', prompt)
        self.assertNotIn(language.MARKER, prompt)
        self.assertIn(TEXT, client.calls[0][1])  # the model reads the original, never a translation
        with patch.dict(os.environ, {'REFRESHER_LANGUAGE': 'de'}):
            again = extract_capture(self.raw.path, client)
        self.assertFalse(again.reused)
        self.assertIn('in German', client.calls[1][0])

    def test_triage_reason_follows_the_setting(self):
        client = FakeModel()
        with patch.dict(os.environ, {'REFRESHER_LANGUAGE': 'ru'}):
            triage_capture(self.raw.path, client)
        self.assertIn('one concise sentence in Russian', client.calls[0][0])


if __name__ == '__main__':
    unittest.main()
