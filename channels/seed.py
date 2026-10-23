"""Validate seed originals and labels before sending any items to capture."""

from dataclasses import dataclass
from pathlib import Path

import yaml

from store.raw import CaptureResult, capture_text, normalize_timestamp, forwarder_hint


@dataclass(frozen=True)
class SeedItem:
    seed_id: str
    text: str
    capture: dict
    expected: dict


def _required_text(data: dict, key: str, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}: {key} must be nonblank text.")
    return value


def load_manifest(path: Path) -> list[SeedItem]:
    """Load all originals, checking metadata and relation targets before writes.

    Expected labels stay in the manifest. They are never given to the capture
    function or used to invent graph results.
    """
    with path.open(encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("Manifest must be a mapping with schema_version: 1.")
    _required_text(data, "corpus_id", "Manifest")
    entries = data.get("items")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Manifest items must be a nonempty list.")

    originals = (path.parent / "originals").resolve(strict=True)
    items = []
    seen = set()
    deliveries = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each manifest item must be a mapping.")
        seed_id = _required_text(entry, "id", "Item")
        if seed_id in seen:
            raise ValueError(f"Duplicate seed ID: {seed_id}")
        seen.add(seed_id)
        filename = _required_text(entry, "file", seed_id)
        source = (path.parent / filename).resolve(strict=True)
        if not source.is_relative_to(originals) or not source.is_file():
            raise ValueError(f"{seed_id}: file must be inside originals/.")
        with source.open(encoding="utf-8", newline="") as file:
            text = file.read()
        if not text.strip():
            raise ValueError(f"{seed_id}: original file is empty.")

        capture = {key: _required_text(entry, key, seed_id) for key in ("channel", "source_id", "source_name", "message_id")}
        delivery = (capture["channel"], capture["source_id"], capture["message_id"])
        if delivery in deliveries:
            raise ValueError(f"{seed_id}: delivery identity is already used by another seed item.")
        deliveries.add(delivery)
        for key in ("captured_at", "published_at"):
            value = entry.get(key)
            if value is not None:
                capture[key] = normalize_timestamp(value)
        author = entry.get("author_hint")
        if author is not None:
            if not isinstance(author, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in author.items()):
                raise ValueError(f"{seed_id}: author_hint must contain text keys and values.")
            capture["author_hint"] = author
        forwarder = entry.get("forwarded_by")
        if forwarder is not None:
            capture["forwarded_by"] = forwarder_hint(forwarder)
        expected = entry.get("expected")
        if not isinstance(expected, dict):
            raise ValueError(f"{seed_id}: expected must be a mapping of labels.")
        relations = expected.get("relations", [])
        if not isinstance(relations, list):
            raise ValueError(f"{seed_id}: expected relations must be a list.")
        for relation in relations:
            if not isinstance(relation, dict) or relation.get("type") not in {"DUPLICATES", "SUPERSEDES", "CONTRADICTS", "SUPPORTS", "ELABORATES"}:
                raise ValueError(f"{seed_id}: unknown relation type.")
            _required_text(relation, "target", seed_id)
        items.append(SeedItem(seed_id, text, capture, expected))

    for item in items:
        for relation in item.expected.get("relations", []):
            if relation["target"] not in seen or relation["target"] == item.seed_id:
                raise ValueError(f"{item.seed_id}: relation must target a different existing seed ID.")
    return items


def import_seed(items: list[SeedItem], vault_path: Path) -> list[tuple[str, CaptureResult]]:
    """Run each validated source through the same capture block as file import."""
    return [(item.seed_id, capture_text(item.text, vault_path, **item.capture)) for item in items]
