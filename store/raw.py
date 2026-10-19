"""Preserve incoming text before processing it."""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4


def save_text(text: str, vault_path: Path) -> Path:
    """Save a nonblank text item unchanged and return its file path."""
    if not text.strip():
        raise ValueError("Cannot save an empty text item.")

    raw_folder = vault_path / "raw"
    raw_folder.mkdir(parents=True, exist_ok=True)

    item_id = uuid4().hex
    file_path = raw_folder / f"{item_id}.txt"

    # Exclusive creation prevents accidentally overwriting an existing item.
    with file_path.open("x", encoding="utf-8", newline="") as file:
        file.write(text)

    return file_path


@dataclass(frozen=True)
class CaptureResult:
    """The acknowledgment returned after a durable capture."""

    path: Path
    item_id: str
    duplicate: bool


def capture_text(
    text: str,
    vault_path: Path,
    *,
    channel: str,
    source_id: str,
    source_name: str,
    message_id: str | None = None,
) -> CaptureResult:
    """Capture text with provenance; repeat deliveries reuse the original record.

    This is a provisional text-only capture envelope, not the team's final
    RawItem contract. Identity is scoped to a channel and source: independent
    sources must remain available as separate evidence for later graph work.
    """
    if not text.strip():
        raise ValueError("Cannot capture an empty text item.")
    if not all(value.strip() for value in (channel, source_id, source_name)):
        raise ValueError("Channel, source ID and source name must not be blank.")
    if message_id is not None and not message_id.strip():
        raise ValueError("Message ID must not be blank when provided.")

    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    identity = [channel, source_id, message_id, None if message_id else content_hash]
    item_id = hashlib.sha256(json.dumps(identity).encode("utf-8")).hexdigest()
    record = {
        "schema_version": 1,
        "item_id": item_id,
        "channel": channel,
        "source_id": source_id,
        "source_name": source_name,
        "message_id": message_id,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "published_at": None,
        "author_hint": None,
        "url": None,
        "kind": "text",
        "text": text,
        "attachments": [],
        "content_hash": content_hash,
    }

    raw_folder = vault_path / "raw"
    raw_folder.mkdir(parents=True, exist_ok=True)
    file_path = raw_folder / f"{item_id}.json"

    # Publish only a complete record. A hard link creates the final filename
    # exclusively, so concurrent retries cannot overwrite the first capture.
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=raw_folder, delete=False, suffix=".tmp"
    ) as temporary:
        temporary_path = Path(temporary.name)
        try:
            json.dump(record, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise

    duplicate = False
    try:
        try:
            os.link(temporary_path, file_path)
        except FileExistsError:
            existing = json.loads(file_path.read_text(encoding="utf-8"))
            if existing["content_hash"] != content_hash:
                raise ValueError(
                    "This message ID already contains different text; "
                    "the original was preserved."
                ) from None
            duplicate = True
    finally:
        temporary_path.unlink(missing_ok=True)

    return CaptureResult(file_path, item_id, duplicate)
