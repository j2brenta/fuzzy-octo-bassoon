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
    captured_at: str | None = None,
    published_at: str | None = None,
    author_hint: dict[str, str] | None = None,
    forwarded_by: str | None = None,
    channel_metadata: dict | None = None,
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

    captured_at = normalize_timestamp(captured_at) if captured_at is not None else datetime.now(timezone.utc).isoformat()
    published_at = normalize_timestamp(published_at) if published_at is not None else None
    if author_hint is not None and (
        not isinstance(author_hint, dict)
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in author_hint.items())
    ):
        raise ValueError("Author hint must contain text keys and values.")
    if forwarded_by is not None and (not isinstance(forwarded_by, str) or not forwarded_by.strip()):
        raise ValueError("Forwarder must be nonblank text when provided.")

    if channel_metadata is not None:
        if not isinstance(channel_metadata, dict):
            raise ValueError("Channel metadata must be a mapping.")
        # Validate before creating any files; channel adapters must pass JSON data.
        json.dumps(channel_metadata, ensure_ascii=False, allow_nan=False)

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
        "captured_at": captured_at,
        "published_at": published_at,
        "author_hint": author_hint,
        "forwarded_by": forwarded_by,
        "channel_metadata": channel_metadata,
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


def normalize_timestamp(value: str) -> str:
    """Require an explicit timezone and store equivalent UTC time."""
    if not isinstance(value, str):
        raise ValueError("Timestamps must be ISO 8601 text with a timezone.")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("Timestamps must be ISO 8601 text with a timezone.") from None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Timestamps must include a timezone, such as +00:00.")
    return timestamp.astimezone(timezone.utc).isoformat()
