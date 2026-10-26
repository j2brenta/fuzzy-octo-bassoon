import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from PIL import Image
from channels.pictures import capture_picture, interpret_picture
from channels.telegram import handle_message, message_fields
from engine.parse import parse_capture
from engine.results import write_item_note
from engine.vision import ImageError, read_image, validate_reading
from engine.llm import ModelReply, ModelError
from tests.test_telegram import message


def picture(**changes):
    return message(
        text=None,
        photo=[
            {
                "file_id": "small",
                "file_unique_id": "small-id",
                "width": 20,
                "height": 20,
            },
            {
                "file_id": "large",
                "file_unique_id": "large-id",
                "width": 200,
                "height": 100,
            },
        ],
        **changes,
    )


def png():
    out = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(out, format="PNG")
    return out.getvalue()


def bot(content=None):
    async def download(path, destination, **kwargs):
        destination.write(content if content is not None else png())
        destination.seek(0)

    return SimpleNamespace(
        get_file=AsyncMock(
            return_value=SimpleNamespace(file_path="images/test.png", file_size=100)
        ),
        download_file=AsyncMock(side_effect=download),
    )


def reading(path):
    return {
        "text": "Alice: Workshop starts at 16:00 UTC on 25 October 2026.",
        "description": "",
        "uncertainties": [],
        "prompt_version": "image-read-v1",
        "model": "fixture-vision",
        "usage": {},
        "request_id": "fixture",
    }


class PictureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.vault = Path(self.directory.name)

    async def capture(self, incoming=None, client=None):
        incoming = (incoming or picture()).as_(client or bot())
        return await capture_picture(incoming, self.vault, message_fields(incoming))

    async def test_highest_resolution_image_saved_before_vision_and_cache_reused(self):
        client = bot()
        receipt = await self.capture(client=client)
        original = receipt.read_bytes()
        client.get_file.assert_awaited_once_with("large")
        self.assertEqual(receipt, await self.capture(client=client))
        client.download_file.assert_awaited_once()
        result = interpret_picture(receipt, reader=reading)
        before = result.path.read_bytes()
        with patch(
            "channels.pictures.read_image", side_effect=AssertionError("no paid call")
        ):
            self.assertTrue(interpret_picture(receipt).duplicate)
        self.assertEqual(before, result.path.read_bytes())
        self.assertEqual(original, receipt.read_bytes())
        document = json.loads(parse_capture(result.path).path.read_text())
        self.assertIn("image_source", document)
        self.assertIn("![Saved original]", write_item_note(result.path).read_text())

    async def test_png_document_and_caption_are_supported_without_guessing_authorship(
        self,
    ):
        incoming = message(
            text=None,
            document={
                "file_id": "doc",
                "file_unique_id": "doc-id",
                "mime_type": "image/png",
                "file_name": "note.png",
            },
            caption="My context",
        )
        result = interpret_picture(await self.capture(incoming), reader=reading)
        raw = json.loads(result.path.read_text())
        self.assertIn("Telegram caption (sender supplied):\nMy context", raw["text"])
        self.assertNotIn("caption", raw["channel_metadata"])
        self.assertIsNone(raw["author_hint"])
        self.assertEqual(raw["forwarded_by"]["name"], "Olga")

    async def test_forwarded_source_is_separate_from_current_sender(self):
        incoming = picture(
            forward_origin={
                "type": "hidden_user",
                "date": 1792317600,
                "sender_user_name": "Original Sender",
            }
        )
        raw = json.loads(
            interpret_picture(
                await self.capture(incoming), reader=reading
            ).path.read_text()
        )
        self.assertEqual(raw["author_hint"]["name"], "Original Sender")
        self.assertEqual(raw["forwarded_by"]["name"], "Olga")

    async def test_model_failure_preserves_picture_and_does_not_save_fake_text(self):
        receipt = await self.capture()
        with patch(
            "channels.pictures.read_image",
            side_effect=ModelError("Service unavailable"),
        ):
            with self.assertRaises(ModelError):
                interpret_picture(receipt)
        self.assertTrue(receipt.exists())
        self.assertFalse(list((self.vault / "raw").glob("*.json")))
        self.assertTrue(interpret_picture(receipt, reader=reading).path.exists())

    async def test_tampered_image_blocks_cached_interpretation(self):
        receipt = await self.capture()
        result = interpret_picture(receipt, reader=reading)
        data = json.loads(receipt.read_text())
        (receipt.parent / (data["sha256"] + ".png")).write_bytes(b"changed")
        with self.assertRaises(ImageError):
            interpret_picture(receipt, reader=reading)
        with self.assertRaisesRegex(ValueError, "provenance"):
            parse_capture(result.path)

    async def test_limits_and_bad_payloads_do_not_acknowledge_capture(self):
        client = bot()
        incoming = picture()
        incoming = incoming.model_copy(
            update={
                "photo": [
                    incoming.photo[1].model_copy(update={"file_size": 10_000_001})
                ]
            }
        )
        with self.assertRaises(ImageError):
            await self.capture(incoming, client)
        client.get_file.assert_not_awaited()
        with self.assertRaises(ImageError):
            await self.capture(client=bot(b"not an image"))
        self.assertFalse(list(self.vault.rglob("*.json")))

    async def test_unauthorized_sender_never_downloads_or_sends_image_to_vlm(self):
        for owner in [None, 999]:
            with (
                patch("aiogram.types.Message.answer", new_callable=AsyncMock),
                patch(
                    "channels.telegram.capture_picture", new_callable=AsyncMock
                ) as capture,
            ):
                await handle_message(picture(), self.vault, owner)
                capture.assert_not_awaited()

    async def test_photo_enters_shared_pipeline_with_visible_automatic_reading(self):
        with (
            patch("aiogram.types.Message.answer", new_callable=AsyncMock) as answer,
            patch("channels.pictures.read_image", side_effect=reading),
            patch(
                "channels.telegram.process_capture", new_callable=AsyncMock
            ) as process,
        ):
            await handle_message(picture().as_(bot()), self.vault, 100)
        process.assert_awaited_once()
        self.assertTrue(process.await_args.args[1].path.exists())
        self.assertIn("Image reading (automatic", answer.await_args_list[1].args[0])

    async def test_voice_is_explicitly_rejected(self):
        incoming = message(
            text=None,
            voice={"file_id": "voice", "file_unique_id": "v-id", "duration": 1},
        )
        with patch("aiogram.types.Message.answer", new_callable=AsyncMock) as answer:
            await handle_message(incoming, self.vault, 100)
        self.assertIn("Voice", answer.await_args.args[0])
        self.assertFalse(list(self.vault.rglob("*.json")))

    def test_vlm_receives_bounded_inline_image_and_strict_json(self):
        path = self.vault / "image.png"
        path.write_bytes(png())
        client = SimpleNamespace(
            model_name="fixture-vlm",
            complete_json=lambda system, payload: self.reply(payload),
        )
        result = read_image(path, client)
        self.assertEqual(result["text"], "Readable text")
        self.assertEqual(result["attempts"], 1)

    def reply(self, payload):
        self.assertEqual(payload[1]["type"], "image_url")
        self.assertTrue(
            payload[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        )
        return ModelReply(
            {"text": "Readable text", "description": "", "uncertainties": []}
        )

    def test_blank_invalid_or_oversized_vision_output_is_not_accepted(self):
        for value in [
            {},
            {"text": "", "description": "", "uncertainties": ["Unreadable"]},
            {"text": "x", "description": "", "uncertainties": [123]},
        ]:
            with self.assertRaises(ImageError):
                validate_reading(value)
