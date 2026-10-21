"""Capture linked HTML articles as children of an immutable message or file."""

import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from engine.artifacts import atomic_json
from engine.parse import parse_capture
from engine.parse.article import ARTICLE_PARSER_VERSION, parse_article
from engine.parse.web import WebError, fetch_html, normalize_url
from store.raw import capture_text, normalize_timestamp

MAX_LINKS = 3
UNSUPPORTED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".mp3", ".mp4", ".ogg", ".wav", ".zip"}


def _save_snapshot(vault: Path, content: bytes) -> dict:
    digest = hashlib.sha256(content).hexdigest()
    path = vault / "web-snapshots" / f"{digest}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise WebError("Stored article snapshot failed its content check.") from None
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(path.relative_to(vault)), "sha256": digest}


def _published_at(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return normalize_timestamp(value)
    except ValueError:
        pass
    # Retain date-only values in metadata instead of inventing midnight/a timezone.
    return None


def _cached_child(vault: Path, row: dict, parent_id: str) -> Path | None:
    item_id = row.get("item_id")
    snapshot_ref = row.get("snapshot")
    digest = snapshot_ref.get("sha256") if isinstance(snapshot_ref, dict) else None
    if not isinstance(item_id, str) or not re.fullmatch(r"[0-9a-f]{64}", item_id):
        return None
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return None
    path = vault / "raw" / f"{item_id}.json"
    snapshot = vault / "web-snapshots" / f"{digest}.html"
    try:
        record = json.loads(path.read_text())
        metadata = record["channel_metadata"]
        if (metadata["parent"]["item_id"] != parent_id
            or metadata["web_article"]["parser_version"] != ARTICLE_PARSER_VERSION
            or metadata["web_article"]["requested_url"] != row["url"]
            or metadata["web_article"]["snapshot"]["sha256"] != digest
            or hashlib.sha256(snapshot.read_bytes()).hexdigest() != digest):
            return None
        parse_capture(path)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return path


def fetch_linked_articles(raw_path: Path, *, force: bool = False, fetcher=None) -> list[dict]:
    """Fetch at most three distinct direct links; reuse successes and retry failures."""
    raw_path = Path(raw_path)
    prepared = parse_capture(raw_path)
    document = json.loads(prepared.path.read_text())
    parent = json.loads(raw_path.read_text())
    # Child pages are leaves: article links are indexed but never recursively fetched.
    if (parent.get("channel_metadata") or {}).get("web_article"):
        return []
    urls = []
    seen = set()
    for link in document["links"]:
        try:
            identity_url = normalize_url(link["url"])
        except WebError:
            identity_url = link["url"]
        if identity_url not in seen:
            seen.add(identity_url)
            urls.append(link["url"])
    if not urls:
        return []
    vault = raw_path.parent.parent
    identity = {"schema_version": 1, "parent_item_id": parent["item_id"],
                "source_content_hash": parent["content_hash"], "parser_version": ARTICLE_PARSER_VERSION}
    destination = vault / "web-links" / raw_path.name
    cached = {}
    if destination.exists() and not force:
        try:
            record = json.loads(destination.read_text())
            if all(record.get(k) == v for k, v in identity.items()):
                cached = {row["url"]: row for row in record["links"] if isinstance(row, dict) and isinstance(row.get("url"), str)}
        except (ValueError, KeyError, TypeError, AttributeError):
            pass
    results = []
    fetcher = fetcher or fetch_html
    for index, url in enumerate(urls):
        row = {"url": url}
        try:
            normalized = normalize_url(url)
            parts = urlsplit(normalized)
            host = parts.hostname
            if (Path(parts.path).suffix.lower() in UNSUPPORTED_SUFFIXES
                or host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")):
                row.update(status="unsupported", reason="PDF, image, audio and video links need their own parsers.")
            elif index >= MAX_LINKS:
                row.update(status="deferred", reason="Only the first three links are downloaded per item.")
            elif url in cached and cached[url].get("status") == "captured" and _cached_child(vault, cached[url], parent["item_id"]) is not None:
                row = {**cached[url], "reused": True}
            else:
                page = fetcher(normalized)
                snapshot = _save_snapshot(vault, page.content)
                row["snapshot"] = snapshot
                article = parse_article(page.content, page.final_url)
                web = {**asdict(article), "parser_version": ARTICLE_PARSER_VERSION,
                       "requested_url": url, "final_url": page.final_url,
                       "fetched_at": page.fetched_at, "snapshot": snapshot}
                web.pop("text")  # Article body lives once, in the child's raw text field.
                shared_by = parent.get("forwarded_by") or parent.get("author_hint")
                if isinstance(shared_by, str):
                    shared_by = {"name": shared_by}
                if shared_by:
                    shared_by = {k: v for k, v in shared_by.items() if k in {"name", "handle", "email", "url"}}
                link_identity = hashlib.sha256(normalized.encode()).hexdigest()
                message_id = f"{parent['item_id']}:{ARTICLE_PARSER_VERSION}:{link_identity}:{snapshot['sha256']}"
                child = capture_text(
                    article.text,
                    vault,
                    channel="import",
                    source_id=page.final_url,
                    source_name=article.site_name or parts.hostname,
                    message_id=message_id,
                    captured_at=page.fetched_at,
                    published_at=_published_at(article.publication_date),
                    author_hint={"name": article.author} if article.author else None,
                    forwarded_by=shared_by or None,
                    url=page.final_url,
                    channel_metadata={
                        "web_article": web,
                        "parent": {
                            "item_id": parent["item_id"],
                            "raw_path": f"raw/{raw_path.name}",
                            "url": url,
                        },
                    },
                )
                parse_capture(child.path)
                row.update(status="captured", item_id=child.item_id, raw_path=f"raw/{child.path.name}", reused=child.duplicate)
        except (WebError, ValueError) as error:
            row.update(status="failed", reason=str(error) if isinstance(error, WebError) else "Article source validation failed; original retained.")
        results.append(row)
        atomic_json(destination, {**identity, "links": [{k: v for k, v in r.items() if k != "reused"} for r in results]})
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--vault", type=Path, default=Path(".local-vault/web"))
    parser.add_argument("--refresh", action="store_true", help="Download again; preserve earlier snapshots and child captures")
    args = parser.parse_args(argv)
    try:
        url = normalize_url(args.url)
        parent = capture_text(url, args.vault, channel="import", source_id=f"web-input:{url}", source_name="Web import")
        rows = fetch_linked_articles(parent.path, force=args.refresh)
        for row in rows:
            print(row["status"], row.get("raw_path") or row.get("reason"))
        return 0 if any(row["status"] == "captured" for row in rows) else 1
    except (WebError, OSError, ValueError) as error:
        parser.exit(1, f"Article import failed: {error if isinstance(error, WebError) else type(error).__name__}\n")


if __name__ == "__main__":
    raise SystemExit(main())
