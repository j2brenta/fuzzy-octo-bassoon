"""Telegram photos and image documents, retained before model interpretation."""

import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from engine.vision import MAX_BYTES, ImageError, image_info, read_image
from store.media import LimitedBuffer, publish_original
from store.raw import capture_text, CaptureResult

MIMES = {"image/jpeg", "image/png", "image/webp"}


def image_attachment(message):
    if message.photo:
        return max(message.photo, key=lambda photo: photo.width * photo.height)
    if message.document and message.document.mime_type in MIMES:
        return message.document
    return None


def checked_receipt(path: Path):
    path = Path(path)
    data = json.loads(path.read_text())
    digest = data.get("sha256")
    suffix = data.get("extension")
    if (
        path.parent.name != "images"
        or path.parent.parent.name != "raw"
        or not re.fullmatch(r"[0-9a-f]{64}", str(digest))
        or suffix not in {"jpg", "png", "webp"}
        or data.get("stage") != "telegram_picture"
        or data.get("item_id") != path.stem
    ):
        raise ImageError("Saved image identity is invalid; original preserved.")
    image = path.parent / f"{digest}.{suffix}"
    content = image.read_bytes()
    if hashlib.sha256(content).hexdigest() != digest:
        raise ImageError("Saved image failed its integrity check; original preserved.")
    image_info(content)
    return data, image


async def capture_picture(message, vault: Path, fields: dict) -> Path:
    attachment = image_attachment(message)
    if attachment is None:
        raise ImageError("Send a photo or a JPEG/PNG/WebP image file.")
    if (attachment.file_size or 0) > MAX_BYTES:
        raise ImageError("Send an image under 10 MB; this image was not downloaded.")
    iid = hashlib.sha256(
        json.dumps(
            ["telegram_picture", fields["source_id"], fields["message_id"]]
        ).encode()
    ).hexdigest()
    receipt = Path(vault) / "raw" / "images" / f"{iid}.json"
    if receipt.exists():
        data, _ = await asyncio.to_thread(checked_receipt, receipt)
        if data["file_unique_id"] != attachment.file_unique_id:
            raise ImageError("Message contains different media; original preserved.")
        return receipt
    try:
        remote = await message.bot.get_file(attachment.file_id)
        if not remote.file_path or (remote.file_size or 0) > MAX_BYTES:
            raise ImageError("Image cannot be downloaded within the 10 MB limit.")
        with LimitedBuffer(MAX_BYTES) as buffer:
            await message.bot.download_file(
                remote.file_path, destination=buffer, timeout=60
            )
            content = buffer.getvalue()
    except ImageError:
        raise
    except Exception:
        raise ImageError(
            "Could not download this image from Telegram. Resend it; no capture success was recorded."
        ) from None
    suffix, width, height = await asyncio.to_thread(image_info, content)
    digest = hashlib.sha256(content).hexdigest()
    data = {
        "schema_version": 1,
        "stage": "telegram_picture",
        "item_id": iid,
        "sha256": digest,
        "extension": suffix,
        "file_unique_id": attachment.file_unique_id,
        "width": width,
        "height": height,
        "source": fields,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    await asyncio.to_thread(
        publish_original, receipt.parent / f"{digest}.{suffix}", content
    )
    await asyncio.to_thread(
        publish_original,
        receipt,
        (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode(),
    )
    return receipt


def interpret_picture(receipt: Path, *, reader=None) -> CaptureResult:
    receipt = Path(receipt)
    data, image = checked_receipt(receipt)
    vault = receipt.parent.parent.parent
    fields = data["source"]
    iid = hashlib.sha256(
        json.dumps(
            ["telegram", fields["source_id"], fields["message_id"], None]
        ).encode()
    ).hexdigest()
    raw = vault / "raw" / f"{iid}.json"
    if raw.exists():
        from engine.parse import parse_capture

        parse_capture(raw)
        cached = json.loads(raw.read_text())
        if (
            cached.get("channel_metadata", {}).get("image_reading", {}).get("sha256")
            != data["sha256"]
        ):
            raise ImageError(
                "Saved text belongs to different media; originals preserved."
            )
        return CaptureResult(raw, iid, True)
    reading = (reader or read_image)(image)
    # Keep OCR, model description and user caption visibly distinct.
    parts = []
    if reading["text"].strip():
        parts.append("Visible text (automatic OCR):\n" + reading["text"].strip())
    if reading["description"].strip():
        parts.append(
            "Visual description (model-generated):\n" + reading["description"].strip()
        )
    caption = (fields["channel_metadata"].get("caption") or "").strip()
    if caption:
        parts.append("Telegram caption (sender supplied):\n" + caption)
    if not parts:
        raise ImageError("No image content available; original retained.")
    metadata = dict(fields["channel_metadata"])
    metadata["image_reading"] = {
        **{k: v for k, v in reading.items() if k not in {"text", "description"}},
        "sha256": data["sha256"],
        "image_path": str(image.relative_to(vault)),
        "receipt_path": str(receipt.relative_to(vault)),
        "receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
    }
    # Caption is preserved once in the derived body; the immutable receipt retains
    # Telegram formatting offsets, which do not apply to the model's OCR text.
    metadata.pop("caption", None)
    metadata.pop("caption_entities", None)
    return capture_text(
        "\n\n".join(parts),
        vault,
        channel="telegram",
        **{**fields, "channel_metadata": metadata},
    )


def validate_image_reference(raw_path: Path, record: dict):
    ref = (record.get("channel_metadata") or {}).get("image_reading")
    if ref is None:
        return None
    vault = raw_path.parent.parent.resolve()
    try:
        receipt = vault / ref["receipt_path"]
        if receipt.parent.resolve() != vault / "raw" / "images" or not re.fullmatch(
            r"[0-9a-f]{64}\.json", receipt.name
        ):
            raise ValueError
        if not receipt.resolve().is_relative_to(vault):
            raise ValueError
        data, image = checked_receipt(receipt)
        if (
            ref["sha256"] != data["sha256"]
            or ref["image_path"] != str(image.relative_to(vault))
            or not image.resolve().is_relative_to(vault)
            or hashlib.sha256(receipt.read_bytes()).hexdigest() != ref["receipt_sha256"]
        ):
            raise ValueError
        for key in [
            "source_id",
            "message_id",
            "author_hint",
            "forwarded_by",
            "published_at",
        ]:
            if data["source"].get(key) != record.get(key):
                raise ValueError
    except (OSError, ValueError, KeyError, TypeError, ImageError):
        raise ValueError(
            "Image provenance failed validation; original retained."
        ) from None
    return ref
