"""Prepare captured text and links without downloading or interpreting content."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from engine.artifacts import atomic_json

PARSER_VERSION = "text-v1"
URL_PATTERN = re.compile(r'''https?://[^\s<>"'\u200b]+''')


@dataclass(frozen=True)
class ParseResult:
    path: Path
    section_count: int
    link_count: int
    reused: bool


def _http_url(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parts = urlsplit(value)
        return parts.scheme in {"http", "https"} and bool(parts.hostname)
    except ValueError:
        return False


def _trim_url(value: str) -> str:
    value = value.rstrip(".,;:!?，。！？；")
    # Keep balanced parentheses in URLs, but remove surrounding Markdown syntax.
    changed = True
    while value and changed:
        changed = False
        for closing, opening in ((")", "("), ("]", "["), ("}", "{")):
            if value.endswith(closing) and value.count(closing) > value.count(opening):
                value = value[:-1].rstrip(".,;:!?，。！？；")
                changed = True
    return value


def _entity_span(text: str, offset, length) -> tuple[int, int]:
    # Telegram counts UTF-16 code units; Python string positions count characters.
    # Emoji therefore require conversion before we can cite the correct passage.
    if type(offset) is not int or type(length) is not int or offset < 0 or length <= 0:
        raise ValueError("Telegram entity positions must be nonnegative integers with positive length.")
    encoded = text.encode("utf-16-le")
    if (offset + length) * 2 > len(encoded):
        raise ValueError("Telegram entity is outside the original text.")
    try:
        start = len(encoded[:offset * 2].decode("utf-16-le"))
        end = len(encoded[:(offset + length) * 2].decode("utf-16-le"))
    except UnicodeDecodeError:
        raise ValueError("Telegram entity cuts through a Unicode character.") from None
    return start, end


def find_links(text: str, metadata: dict, section_id: str) -> list[dict]:
    links = {}
    for match in URL_PATTERN.finditer(text):
        url = _trim_url(match.group())
        if _http_url(url):
            start, end = match.start(), match.start() + len(url)
            links[(start, end, url)] = dict(url=url, section_id=section_id, start_char=start, end_char=end, kind="url")

    entities = metadata.get("entities", [])
    if not isinstance(entities, list):
        raise ValueError("Telegram entities must be a list.")
    for entity in entities:
        if not isinstance(entity, dict):
            raise ValueError("Telegram entities must be mappings.")
        kind = entity.get("type")
        if kind not in {"url", "text_link"}:
            continue
        start, end = _entity_span(text, entity.get("offset"), entity.get("length"))
        url = entity.get("url") if kind == "text_link" else text[start:end]
        if _http_url(url):
            links[(start, end, url)] = dict(url=url, section_id=section_id, start_char=start, end_char=end, kind=kind)
    return sorted(links.values(), key=lambda link: (link["start_char"], link["end_char"], link["url"]))



def text_without_links(text: str, links: list[dict]) -> str:
    """Remove link spans to distinguish a bare pointer from a message comment."""
    for link in sorted(links, key=lambda row: row["start_char"], reverse=True):
        text = text[:link["start_char"]] + text[link["end_char"]:]
    return text


def parse_capture(raw_path: Path) -> ParseResult:
    """Write a rebuildable document while keeping source text only in raw/.

    These parser records are provisional pending the shared Document contract.
    A text-only message has one section spanning its original body. Later PDF,
    email and image parsers will add sections for individual source parts.
    """
    raw_path = Path(raw_path)
    if raw_path.parent.name != "raw":
        raise ValueError("A captured JSON record must be inside a raw/ directory.")
    record = json.loads(raw_path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("kind") != "text":
        raise ValueError("This parser supports text capture records only.")
    item_id = record.get("item_id")
    if not isinstance(item_id, str) or not re.fullmatch(r"[0-9a-f]{64}", item_id) or raw_path.stem != item_id:
        raise ValueError("Capture item ID must match its filename.")
    text = record.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Captured text must not be blank.")
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if record.get("content_hash") != content_hash:
        raise ValueError("Captured text does not match its content hash; source left unchanged.")
    metadata = record.get("channel_metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("Channel metadata must be a mapping.")
    section_id = f"{item_id}:section:1"
    document = {
        "schema_version": 1,
        "parser_version": PARSER_VERSION,
        "item_id": item_id,
        "raw_path": f"raw/{raw_path.name}",
        "source_content_hash": content_hash,
        "sections": [{
            "section_id": section_id,
            "kind": "text",
            "provenance": {"item_id": item_id, "field": "text", "start_char": 0, "end_char": len(text)},
        }],
        "links": find_links(text, metadata, section_id),
    }
    if isinstance(metadata.get("web_article"), dict):
        parent = metadata.get("parent")
        snapshot = metadata["web_article"].get("snapshot")
        if not isinstance(parent, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(parent.get("item_id", ""))):
            raise ValueError("Article parent provenance is invalid.")
        if parent.get("raw_path") != f"raw/{parent['item_id']}.json":
            raise ValueError("Article parent path does not match its ID.")
        digest = snapshot.get("sha256") if isinstance(snapshot, dict) else None
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Article snapshot identity is invalid.")
        if snapshot.get("path") != f"web-snapshots/{digest}.html":
            raise ValueError("Article snapshot path does not match its hash.")
        snapshot_path = raw_path.parent.parent / snapshot["path"]
        if hashlib.sha256(snapshot_path.read_bytes()).hexdigest() != digest:
            raise ValueError("Article snapshot failed its content check.")
        document["parent"] = parent
        document["source_url"] = record.get("url")
        document["sections"][0]["provenance"]["derived_from"] = snapshot
    encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    folder = raw_path.parent.parent / "documents"
    destination = folder / raw_path.name
    reused = destination.exists() and destination.read_bytes() == encoded
    if not reused:
        atomic_json(destination, document)
    return ParseResult(destination, len(document["sections"]), len(document["links"]), reused)
