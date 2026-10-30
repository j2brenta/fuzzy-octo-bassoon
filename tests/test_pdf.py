import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from channels.pdf import capture_pdf, interpret_pdf, save_pdf
from channels.telegram import handle_message, message_fields
from channels.web import fetch_linked_articles
from engine.parse import parse_capture
from engine.parse.pdf import PdfError, read_pdf
from engine.parse.web import HtmlPage, WebError, fetch_document
from engine.results import write_item_note
from store.raw import capture_text
from tests.test_telegram import message
from tests import test_web as web_fixtures

FIXTURES = Path(__file__).parent / "fixtures" / "pdf"


def incoming(**changes):
    return message(
        text=None,
        document={
            "file_id": "pdf-file",
            "file_unique_id": "pdf-unique",
            "file_name": "notes.pdf",
            "mime_type": "application/pdf",
        },
        **changes,
    )


def bot(content=None):
    async def download(path, destination, **kwargs):
        destination.write(
            content if content is not None else (FIXTURES / "native.pdf").read_bytes()
        )
        destination.seek(0)

    return SimpleNamespace(
        get_file=AsyncMock(
            return_value=SimpleNamespace(file_path="documents/test.pdf", file_size=1000)
        ),
        download_file=AsyncMock(side_effect=download),
    )


def vision(path):
    return {
        "text": "Alice: Workshop starts at 16:00 UTC on 25 October 2026.",
        "description": "",
        "uncertainties": [],
        "model": "fixture-vision",
        "prompt_version": "image-read-v1",
    }


class PdfTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.vault = Path(self.directory.name)

    async def capture(self, *, content=None, caption=None):
        msg = incoming(caption=caption).as_(bot(content))
        return await capture_pdf(msg, self.vault, message_fields(msg))

    async def test_native_pages_and_original_preserved_without_vision_calls(self):
        receipt = await self.capture()
        before = receipt.read_bytes()
        with patch(
            "engine.parse.pdf.read_image",
            side_effect=AssertionError("native text needs no VLM"),
        ):
            result = interpret_pdf(receipt)
        raw = json.loads(result.path.read_text())
        ref = raw["channel_metadata"]["pdf_reading"]
        self.assertEqual(ref["page_count"], 2)
        self.assertEqual([p["method"] for p in ref["pages"]], ["native", "native"])
        self.assertIn("15:00", raw["text"])
        self.assertIn("16:00", raw["text"])
        self.assertIsNone(raw["author_hint"])
        self.assertEqual(raw["forwarded_by"]["name"], "Olga")
        document = json.loads(parse_capture(result.path).path.read_text())
        self.assertIn("pdf_source", document)
        note = write_item_note(result.path).read_text()
        self.assertIn("Open original PDF", note)
        self.assertEqual(receipt.read_bytes(), before)
        self.assertTrue(interpret_pdf(receipt).duplicate)
        self.assertTrue(parse_capture(result.path).reused)

    async def test_scanned_page_uses_vision_and_page_cache_avoids_repeated_calls(self):
        receipt = await self.capture(content=(FIXTURES / "scanned.pdf").read_bytes())
        reader = MagicMock(side_effect=vision)
        result = interpret_pdf(receipt, reader=reader)
        ref = json.loads(result.path.read_text())["channel_metadata"]["pdf_reading"]
        self.assertEqual(ref["pages"][0]["method"], "vision")
        reader.assert_called_once()
        # A second source with the same PDF reuses the page reading too.
        second = save_pdf(
            (FIXTURES / "scanned.pdf").read_bytes(),
            self.vault,
            {**message_fields(incoming()), "message_id": "different-message"},
        )
        interpret_pdf(second, reader=reader)
        reader.assert_called_once()

    async def test_caption_retained_separately_from_page_text(self):
        result = interpret_pdf(await self.capture(caption="My context"))
        text = json.loads(result.path.read_text())["text"]
        self.assertIn("Telegram caption (sender supplied):\nMy context", text)

    async def test_model_failure_preserves_original_for_retry(self):
        from engine.llm import ModelError

        receipt = await self.capture(content=(FIXTURES / "scanned.pdf").read_bytes())
        with patch(
            "engine.parse.pdf.read_image", side_effect=ModelError("Service unavailable")
        ):
            with self.assertRaises(ModelError):
                interpret_pdf(receipt)
        self.assertTrue(receipt.exists())
        self.assertTrue(interpret_pdf(receipt, reader=vision).path.exists())

    async def test_corrupt_pdf_or_blank_document_is_not_reported_as_successful_extraction(
        self,
    ):
        for content in [b"%PDF-1.7\ninvalid", (FIXTURES / "blank.pdf").read_bytes()]:
            receipt = save_pdf(
                content,
                self.vault,
                {**message_fields(incoming()), "message_id": str(len(content))},
            )
            with self.assertRaises(PdfError):
                interpret_pdf(receipt, reader=vision)
            self.assertTrue(receipt.exists())

    async def test_pdf_tampering_blocks_cached_text_and_parser(self):
        receipt = await self.capture()
        result = interpret_pdf(receipt)
        ref = json.loads(result.path.read_text())["channel_metadata"]["pdf_reading"]
        (self.vault / ref["pdf_path"]).write_bytes(b"changed")
        with self.assertRaises(PdfError):
            interpret_pdf(receipt)
        with self.assertRaisesRegex(ValueError, "PDF provenance"):
            parse_capture(result.path)

    async def test_a_long_pdf_is_read_up_to_the_limits_and_says_so(self):
        receipt = await self.capture()
        for limit, value, pages, notice in (("MAX_PAGES", 1, 1, "pages 1–1 of 2 were analyzed"),
                                            ("MAX_TEXT_CHARACTERS", 30, 1, "the start of page 1 of 2 was analyzed")):
            with self.subTest(limit=limit), patch(f"engine.parse.pdf.{limit}", value):
                result = interpret_pdf(receipt)
                raw = json.loads(result.path.read_text())
                ref = raw["channel_metadata"]["pdf_reading"]
                self.assertEqual((ref["page_count"], ref["pdf_pages"]), (pages, 2))
                self.assertIn(notice, ref["cut"])
                self.assertNotIn("PDF page 2:", raw["text"])
                parse_capture(result.path)  # page provenance still holds for what was read
                result.path.unlink()
        reading = json.loads(interpret_pdf(receipt).path.read_text())["channel_metadata"]["pdf_reading"]
        self.assertEqual(reading["page_count"], 2)
        self.assertNotIn("cut", reading)

    async def test_unauthorized_sender_never_downloads_pdf(self):
        with (
            patch("aiogram.types.Message.answer", new_callable=AsyncMock),
            patch("channels.telegram.capture_pdf", new_callable=AsyncMock) as capture,
        ):
            await handle_message(incoming(), self.vault, 999)
            capture.assert_not_awaited()

    async def test_pdf_enters_shared_pipeline_and_repeat_delivery_skips_download(self):
        client = bot()
        msg = incoming().as_(client)
        with (
            patch("aiogram.types.Message.answer", new_callable=AsyncMock) as answer,
            patch(
                "channels.telegram.process_capture", new_callable=AsyncMock
            ) as process,
        ):
            await handle_message(msg, self.vault, 100)
            await handle_message(msg, self.vault, 100)
        self.assertEqual(process.await_count, 2)
        client.download_file.assert_awaited_once()
        self.assertTrue(
            any("PDF prepared: 2" in call.args[0] for call in answer.await_args_list)
        )

    async def test_telegram_oversized_pdf_is_not_downloaded(self):
        client = bot()
        msg = incoming()
        msg = msg.model_copy(
            update={
                "document": msg.document.model_copy(update={"file_size": 20_000_001})
            }
        ).as_(client)
        with self.assertRaises(PdfError):
            await capture_pdf(msg, self.vault, message_fields(msg))
        client.get_file.assert_not_awaited()

    def test_linked_pdf_by_mime_type_preserves_url_parent_and_cache(self):
        url = "https://example.org/download?id=123"
        parent = capture_text(
            url,
            self.vault,
            channel="telegram",
            source_id="fixture-link",
            source_name="Sender",
        )
        fetcher = MagicMock(
            return_value=HtmlPage(
                url,
                url,
                (FIXTURES / "native.pdf").read_bytes(),
                "2026-10-23T12:00:00+00:00",
                "application/pdf",
            )
        )
        [row] = fetch_linked_articles(parent.path, fetcher=fetcher)
        self.assertEqual(row["kind"], "pdf")
        self.assertEqual(row["status"], "captured")
        child = json.loads((self.vault / row["raw_path"]).read_text())
        self.assertEqual(child["url"], url)
        self.assertEqual(child["channel_metadata"]["parent"]["item_id"], parent.item_id)
        [cached] = fetch_linked_articles(
            parent.path,
            fetcher=MagicMock(side_effect=AssertionError("No second download")),
        )
        self.assertTrue(cached["reused"])
        self.assertEqual(cached["item_id"], row["item_id"])

    async def test_handoff_and_readable_claim_carry_pdf_page_reference(self):
        from engine.llm import ModelReply
        from engine.triage import triage_capture
        from engine.extract import extract_capture
        from engine.judge import judge_capture
        from engine.entities import extract_entities, review_entities
        from engine.package import build_extraction
        from tests.test_triage import FakeModel, LABELS
        from tests.test_judge import review_data
        from tests.test_entities import entity_review

        raw = interpret_pdf(await self.capture())
        triage_capture(raw.path, FakeModel([ModelReply(dict(LABELS))]))
        candidate = extract_capture(
            raw.path,
            FakeModel(
                [
                    ModelReply(
                        {
                            "claims": [
                                {
                                    "statement": "The workshop start time was corrected from 15:00 to 16:00 UTC on 25 October 2026.",
                                    "evidence_passage_ids": [5, 6],
                                    "kind": "announcement",
                                    "durability_class": "ephemeral",
                                    "confidence": 0.9,
                                }
                            ]
                        }
                    )
                ]
            ),
        )
        judge_capture(raw.path, FakeModel([ModelReply(review_data(candidate.claims))]))
        entities = extract_entities(raw.path, FakeModel([ModelReply({"entities": []})]))
        review_entities(raw.path, FakeModel([ModelReply(entity_review([]))]), entities)
        record = build_extraction(raw.path).record
        self.assertEqual(record["pdf_source"]["page_count"], 2)
        self.assertIn("PDF pages: 2.", write_item_note(raw.path).read_text())

    def test_local_pdf_import_uses_the_same_page_preserving_adapter(self):
        from channels.importer import main

        with patch("dotenv.load_dotenv"):
            self.assertEqual(
                main([str(FIXTURES / "native.pdf"), "--vault", str(self.vault)]), 0
            )
        [raw_path] = list((self.vault / "raw").glob("*.json"))
        raw = json.loads(raw_path.read_text())
        self.assertEqual(raw["channel"], "import")
        self.assertEqual(raw["channel_metadata"]["pdf_reading"]["page_count"], 2)
        self.assertTrue(
            (self.vault / raw["channel_metadata"]["pdf_reading"]["pdf_path"]).exists()
        )

    def test_corrupted_page_cache_is_rebuilt_from_original(self):
        cache = self.vault / "page-cache"
        reader = MagicMock(side_effect=vision)
        read_pdf(FIXTURES / "scanned.pdf", cache, reader=reader)
        path = next(cache.rglob("page-1.json"))
        value = json.loads(path.read_text())
        value["text"] = "Unexpected cache corruption"
        path.write_text(json.dumps(value))
        result = read_pdf(FIXTURES / "scanned.pdf", cache, reader=reader)
        self.assertEqual(reader.call_count, 2)
        self.assertIn("Alice", result["text"])

    def test_pdf_http_download_uses_mime_and_checks_signature_and_size(self):
        connection = web_fixtures.FetchTests().connection(
            content=(FIXTURES / "native.pdf").read_bytes(),
            headers={"Content-Type": "application/pdf"},
        )
        with patch("engine.parse.web._connection", return_value=connection):
            result = fetch_document("https://example.org/download")
        self.assertEqual(result.content_type, "application/pdf")
        connection = web_fixtures.FetchTests().connection(
            content=b"not a PDF", headers={"Content-Type": "application/pdf"}
        )
        with patch("engine.parse.web._connection", return_value=connection):
            with self.assertRaises(WebError):
                fetch_document("https://example.org/download")

    def test_pdf_download_rejects_claimed_oversize_before_reading(self):
        connection = web_fixtures.FetchTests().connection(
            headers={"Content-Type": "application/pdf", "Content-Length": "20000001"}
        )
        with patch("engine.parse.web._connection", return_value=connection):
            with self.assertRaises(WebError):
                fetch_document("https://example.org/a.pdf")
        connection.getresponse.return_value.read.assert_not_called()
