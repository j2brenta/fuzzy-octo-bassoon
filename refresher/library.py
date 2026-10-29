"""Read-only collection projection. Raw captures remain visible before graph placement."""
import re
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

from refresher.web import read_json
from refresher import reading

SOURCE_ID = re.compile(r"^[\w-]{1,200}$")


def app_url(item_id: str) -> str | None:
    """An operator-configured reachable app; never guess a localhost link for another device."""
    base = os.environ.get("REFRESHER_APP_URL", "").strip().rstrip("/")
    try:
        url = urlsplit(base)
    except ValueError:
        return None
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment or url.path or any(c.isspace() for c in base):
        return None
    return f"{base}/library/?source={quote(item_id, safe='')}"


def source(vault: Path, item_id: str, *, marks: dict | None = None) -> dict | None:
    if not SOURCE_ID.fullmatch(item_id):
        return None
    raw = read_json(vault / "raw" / f"{item_id}.json")
    original = None
    if not raw:
        for kind in ("pdfs", "images"):
            media = read_json(vault / "raw" / kind / f"{item_id}.json")
            if media:
                raw = {**media.get("source", {}), "captured_at": media.get("captured_at", ""),
                       "channel": media.get("channel", "telegram"), "text": ""}
                original = {"id": item_id, "kind": kind}
                break
    if not raw:
        return None
    metadata = raw.get("channel_metadata") or {}
    for key in ("pdf_reading", "image_reading"):
        ref = metadata.get(key) or {}
        if match := re.fullmatch(r"raw/(pdfs|images)/([a-f0-9]{64})\.json", ref.get("receipt_path", "")):
            original = {"id": match[2], "kind": match[1]}
    processed = read_json(vault / "processed" / f"{item_id}.json")
    extraction = read_json(vault / "extractions" / f"{item_id}.json")
    triage = processed.get("triage") or read_json(vault / "triage" / f"{item_id}.json").get("labels") or {}
    claims = processed.get("claims", extraction.get("claims", []))
    text = raw.get("text", "")
    metadata = processed.get("source") or {}
    title = (raw.get("channel_metadata") or {}).get("web_article", {}).get("title") or raw.get("source_name") or (text.splitlines()[0][:120] if text else "Saved source")
    capture_metadata = raw.get("channel_metadata") or {}
    receipt = capture_metadata.get("submission_id")
    parent_id = capture_metadata.get("parent", {}).get("item_id", "")
    if not receipt and isinstance(parent_id, str) and SOURCE_ID.fullmatch(parent_id):
        parent = read_json(vault / "raw" / f"{parent_id}.json")
        receipt = (parent.get("channel_metadata") or {}).get("submission_id")
    marks = marks if marks is not None else reading.load(vault)["items"]
    return {"id": item_id, "title": title, "original": original, "receipt": receipt if isinstance(receipt, str) and re.fullmatch(r"[a-f0-9]{32}", receipt) else None,
            "channel": raw.get("channel", metadata.get("channel", "import")), "created_at": raw.get("captured_at", metadata.get("captured_at", "")),
            "url": raw.get("url"), "text": text, "claims": claims,
            "quality": processed.get("quality_status", "pending"),
            "status": "not_selected" if triage.get("keep") is False else "analyzed" if claims else "saved",
            "reason": triage.get("reason", ""), "author": raw.get("author_hint"),
            "published_at": raw.get("published_at"), "read": marks.get(item_id, {}).get("state", "unread")}


def catalog(vault: Path, jobs: list[dict]) -> list[dict]:
    # Retain independent notes and article children; link-only receipts are grouped below.
    parents = {job["items"][0]: job for job in jobs if job.get("items")}
    rows = []
    marks = reading.load(vault)["items"]
    seen_media = set()
    paths = list((vault / "raw").glob("*.json"))
    paths += list((vault / "raw" / "pdfs").glob("*.json")) + list((vault / "raw" / "images").glob("*.json"))
    seen_ids = set()
    for path in paths:
        if path.stem in seen_ids or (path.parent.name, path.stem) in seen_media:
            continue
        row = source(vault, path.stem, marks=marks)
        if row is None:
            continue
        seen_ids.add(path.stem)
        if row["original"]:
            seen_media.add((row["original"]["kind"], row["original"]["id"]))
        row.pop("text")
        row["claims"] = len(row["claims"])
        if job := parents.get(path.stem):
            row.update(title=job["title"], receipt=job["id"], status=job["status"], stage=job["stage"])
        rows.append(row)
    # A link-only capture is a receipt for its fetched article, not another article.
    by_id = {row["id"]: row for row in rows}
    hidden_parents = set()
    for job in jobs:
        if job.get("kind") != "link" or not job.get("items"):
            continue
        parent_id = job["items"][0]
        parent = read_json(vault / "raw" / f"{parent_id}.json")
        for link in job.get("links", []):
            child_id = link.get("item_id")
            if link.get("status") != "captured" or child_id == parent_id or child_id not in by_id:
                continue
            child = read_json(vault / "raw" / f"{child_id}.json")
            if (parent.get("text", "").strip() == link.get("url")
                    and (child.get("channel_metadata") or {}).get("parent", {}).get("item_id") == parent_id):
                hidden_parents.add(parent_id)
                by_id[child_id]["receipt"] = job["id"]
    rows = [row for row in rows if row["id"] not in hidden_parents]
    for job in jobs:
        if not job.get("items"):
            rows.append({"id": None, "receipt": job["id"], "title": job["title"], "channel": "app",
                         "created_at": job["created_at"], "status": job["status"], "stage": job["stage"], "claims": 0})
    return sorted(rows, key=lambda r: (r["created_at"], r.get("id") or r["receipt"]), reverse=True)


def original(vault: Path, item_id: str, kind: str) -> tuple[bytes, str] | None:
    if not re.fullmatch(r"[a-f0-9]{64}", item_id) or kind not in {"pdfs", "images"}:
        return None
    receipt = vault / "raw" / kind / f"{item_id}.json"
    if not receipt.is_file():
        return None
    # Reuse the channel's identity and content-hash checks before serving stored bytes.
    from channels.pdf import checked_pdf
    from channels.pictures import checked_receipt
    from engine.vision import ImageError
    try:
        _, path = (checked_pdf if kind == "pdfs" else checked_receipt)(receipt)
    except ImageError:
        raise ValueError("The original image could not be verified.") from None
    return path.read_bytes(), path.suffix
