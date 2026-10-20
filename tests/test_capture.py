import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from store.raw import capture_text


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.vault = Path(self.directory.name)

    def capture(self, text="A note", **overrides):
        arguments = dict(channel="file", source_id="source-1", source_name="Sample")
        arguments.update(overrides)
        return capture_text(text, self.vault, **arguments)

    def test_preserves_original_and_does_not_invent_authorship(self):
        text = "  Café — Привет\r\nOriginal spacing.\n"
        result = self.capture(text)
        record = json.loads(result.path.read_text(encoding="utf-8"))
        self.assertEqual(record["text"], text)
        self.assertEqual(record["source_id"], "source-1")
        self.assertIsNone(record["author_hint"])
        self.assertIsNone(record["published_at"])
        self.assertTrue(record["captured_at"].endswith("+00:00"))

    def test_retry_keeps_original_record_and_capture_time(self):
        first = self.capture()
        original_bytes = first.path.read_bytes()
        retry = self.capture()
        self.assertTrue(retry.duplicate)
        self.assertEqual(first.path, retry.path)
        self.assertEqual(first.path.read_bytes(), original_bytes)
        self.assertEqual(len(list((self.vault / "raw").iterdir())), 1)

    def test_identical_text_from_independent_sources_is_retained(self):
        first = self.capture()
        second = self.capture(source_id="source-2")
        self.assertNotEqual(first.path, second.path)
        self.assertFalse(second.duplicate)

    def test_distinct_messages_are_kept_even_when_text_matches(self):
        first = self.capture(message_id="message-1")
        second = self.capture(message_id="message-2")
        self.assertNotEqual(first.path, second.path)

    def test_changed_message_does_not_overwrite_original(self):
        first = self.capture(message_id="message-1")
        original_bytes = first.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "original was preserved"):
            self.capture("Changed text", message_id="message-1")
        self.assertEqual(first.path.read_bytes(), original_bytes)
        self.assertFalse(list((self.vault / "raw").glob("*.tmp")))

    def test_blank_input_creates_no_record(self):
        with self.assertRaises(ValueError):
            self.capture(" \n ")
        self.assertFalse((self.vault / "raw").exists())


if __name__ == "__main__":
    unittest.main()
