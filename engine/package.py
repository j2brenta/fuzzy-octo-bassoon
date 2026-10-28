"""Build one provisional extraction handoff from validated local artifacts; no API calls."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

from engine.review_policy import POLICY_VERSION, compatible_stored_review
from engine.artifacts import atomic_json, fingerprint
from engine.entities import (EntityError, SUPPORTED_ENTITY_VERSIONS, _cached_affiliations, _cached_entities,
                             supported_affiliations, validate_entity_review)
from engine.extract import ExtractionError, MAX_CLAIMS, MAX_COVERAGE_CLAIMS, SUPPORTED_PROMPT_VERSIONS, _cached_claims, source_passages
from engine.judge import JudgeError, validate_review
from engine.parse import parse_capture
from engine.parse.article import citation_authors
from store.raw import forwarder_hint
from engine.triage import TriageError, validate_labels

BUILDER_VERSION = "handoff-v3"
CANDIDATES = {"extractions": "reviews", "repairs": "repair-reviews", "coverage": "coverage-reviews", "evidence": "evidence-reviews"}


class PackageError(RuntimeError):
    """Artifacts could not form a consistent handoff; no success was recorded."""


@dataclass(frozen=True)
class PackageResult:
    path: Path
    record: dict
    reused: bool


def build_extraction(raw_path: Path, *, claims_from: str = "auto") -> PackageResult:
    raw_path = Path(raw_path)
    if claims_from != "auto" and claims_from not in CANDIDATES:
        raise PackageError("Unknown claim candidate stage.")
    prepared = parse_capture(raw_path)
    vault, name = raw_path.parent.parent.resolve(), raw_path.name
    inputs = {}

    def read(path):
        path = Path(path)
        resolved = path.resolve()
        if not resolved.is_relative_to(vault):
            raise PackageError("Handoff artifacts must stay inside their source vault.")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise PackageError("Handoff inputs must be JSON objects.")
        inputs[str(resolved.relative_to(vault))] = fingerprint(data)
        return data

    raw, document = read(raw_path), read(prepared.path)
    if document["source_content_hash"] != raw["content_hash"]:
        raise PackageError("Source changed during packaging; retry the handoff.")
    item_id, text = raw["item_id"], raw["text"]
    section = document["sections"][0]["section_id"]
    passages = source_passages(text)

    def source_matches(record):
        return (record.get("item_id") == item_id and record.get("source_content_hash") == raw["content_hash"])

    candidates = []
    selected = None
    ignored = []
    for folder, review_folder in CANDIDATES.items():
        path = vault / folder / name
        if not path.exists():
            continue
        record = read(path)
        if not source_matches(record):
            if folder == "extractions" or claims_from == folder:
                raise PackageError("Claim candidate does not match the current source.")
            ignored.append({"stage": folder, "reason": "stale_source"})
            continue
        if folder != "extractions":
            parents = [c for c in candidates if fingerprint(c["record"]) == record.get("parent_extraction_hash")
                       and c["review"] is not None and c["stored_review_hash"] == record.get("parent_review_hash")]
            if not parents:
                if claims_from == folder:
                    raise PackageError("Requested claim candidate has a stale or missing parent.")
                ignored.append({"stage": folder, "reason": "stale_parent"})
                continue
        if (record.get("stage") != "claim_extraction" or record.get("prompt_version") not in SUPPORTED_PROMPT_VERSIONS
            or record.get("parser_version") != document["parser_version"]):
            raise PackageError("Claim candidate uses an unsupported source/parser format.")
        limit = MAX_COVERAGE_CLAIMS if record.get("coverage_prompt_version") else MAX_CLAIMS
        claims = _cached_claims(record.get("claims"), text, item_id, section, max_claims=limit)
        reviewed = None
        stored_review_hash = None
        review_path = vault / review_folder / name
        if review_path.exists():
            review_record = read(review_path)
            if source_matches(review_record) and review_record.get("extraction_hash") == fingerprint(record):
                stored = review_record.get("review")
                if not isinstance(stored, dict):
                    raise PackageError("Claim review is malformed.")
                reviewed = validate_review({k: v for k, v in stored.items() if k != "status"}, claims, passages)
                if not compatible_stored_review(stored, reviewed, review_record.get("quality_policy_version"), "claims", "omitted_concepts"):
                    raise PackageError("Stored claim review status disagrees with its findings.")
                stored_review_hash = fingerprint(stored)
        candidate = {"folder": folder, "record": record, "claims": claims, "review": reviewed, "stored_review_hash": stored_review_hash}
        candidates.append(candidate)
        if claims_from == "auto" or claims_from == folder:
            selected = candidate
        if claims_from == folder:
            break
    if selected is None:
        raise PackageError("No matching claim extraction is available for this handoff.")

    entity_path = vault / "entities" / name
    entities, affiliations, entity_review = [], [], None
    entity_record = None
    if entity_path.exists():
        entity_record = read(entity_path)
        if (entity_record.get("stage") != "entity_extraction" or not source_matches(entity_record) or entity_record.get("prompt_version") not in SUPPORTED_ENTITY_VERSIONS
            or entity_record.get("parser_version") != document["parser_version"]):
            raise PackageError("Entity extraction is stale or does not match this source.")
        entities = _cached_entities(entity_record.get("entities"), text, item_id, section)
        affiliations = _cached_affiliations(entity_record.get("affiliations", []), entities, item_id)
        review_path = vault / "entity-reviews" / name
        if review_path.exists():
            review_record = read(review_path)
            if source_matches(review_record) and review_record.get("entity_extraction_hash") == fingerprint(entity_record):
                stored = review_record.get("review")
                if not isinstance(stored, dict):
                    raise PackageError("Entity review is malformed.")
                entity_review = validate_entity_review({k: v for k, v in stored.items() if k != "status"}, entities, text,
                                                       item_id, section, affiliations)
                if not compatible_stored_review(stored, entity_review, review_record.get("quality_policy_version"), "entities", "omitted_entities"):
                    raise PackageError("Stored entity review status disagrees with its findings.")

    labels = None
    triage_path = vault / "triage" / name
    if triage_path.exists():
        triage = read(triage_path)
        if source_matches(triage):
            labels = validate_labels(triage.get("labels"))
    claim_review = selected["review"]
    reviews = [claim_review, entity_review]
    if labels is not None and not labels["keep"]:
        status = "skipped"
    elif any(r is not None and r["status"] == "needs_revision" for r in reviews):
        status = "needs_revision"
    elif labels is None or any(r is None for r in reviews):
        status = "pending"
    else:
        status = "accepted"
    source_forwarder = raw.get("forwarded_by")
    if isinstance(source_forwarder, str) and source_forwarder.isdigit() and raw.get("channel") == "telegram":
        source_forwarder = {"url": f"tg://user?id={source_forwarder}"}
    source_forwarder = forwarder_hint(source_forwarder)
    core = {
        "schema_version": 1, "stage": "processed_extraction", "builder_version": BUILDER_VERSION,
        "contract_status": "provisional_awaiting_shared_schema", "item_id": item_id,
        "source_content_hash": raw["content_hash"], "raw_path": f"raw/{name}",
        "source": {k: raw.get(k) for k in ("channel", "source_id", "source_name", "message_id", "captured_at", "published_at", "author_hint", "forwarded_by", "url", "kind")},
        "sections": document["sections"], "links": document["links"],
        "attachments": raw.get("attachments", []),
        "channel_metadata_ref": {"path": f"raw/{name}", "field": "channel_metadata"},
        "claims": selected["claims"], "entities": entities,
        "affiliations": supported_affiliations(affiliations, entity_review), "triage": labels,
        "reviews": {"claims": claim_review or {"status": "pending"}, "entities": entity_review or {"status": "pending"}},
        "quality_status": status, "quality_gate_passed": status == "accepted",
        "quality_policy_version": POLICY_VERSION,
        "coverage_notes": {"claims": (claim_review or {}).get("omitted_concepts", []),
                           "entities": (entity_review or {}).get("omitted_entities", [])},
        "concept_review_notes": [r for r in (entity_review or {}).get("entities", [])
                                 if r["verdict"] != "supported" and r["entity_id"] in
                                 {e["entity_id"] for e in entities if e["kind"] == "concept"}],
        "graph_status": "not_applied", "claim_candidate": selected["folder"],
        "entity_status": "saved" if entity_record is not None else "pending",
        "chunks": [], "embeddings_status": "not_implemented",
        "inputs": dict(sorted(inputs.items())), "ignored_candidates": ignored,
    }
    if "parent" in document:
        core["parent"] = document["parent"]
        snapshot = document["sections"][0]["provenance"].get("derived_from", {}).get("path", "")
        if snapshot.startswith("web-snapshots/"):
            # Captures before citation_author parsing stored "Last; First" from trafilatura; the snapshot,
            # already checked against its hash by the parser, gives the author as written.
            authors = citation_authors((vault / snapshot).read_bytes())
            if authors:
                core["source"]["author_hint"] = {"name": authors[0]}
    if "image_source" in document:
        core["image_source"] = document["image_source"]
    if "pdf_source" in document:
        core["pdf_source"] = document["pdf_source"]
    copied = document.get("telegram_copy")
    if copied:
        core["telegram_copy"] = copied
        if len(copied["messages"]) == 1:
            header = copied["messages"][0]
            core["source"].update({key: header.get(key) for key in ("copied_at", "copied_at_local")})
            if source_forwarder is None:
                source_forwarder = header["sender_hint"]
    core["source"]["forwarded_by"] = source_forwarder
    core["bundle_id"] = fingerprint(core)
    destination = vault / "processed" / name
    # Do not publish a mixed snapshot if another process changed an input.
    for relative, digest in list(inputs.items()):
        if fingerprint(json.loads((vault / relative).read_text(encoding="utf-8"))) != digest:
            raise PackageError("An input changed during packaging; retry the handoff.")
    if destination.exists():
        try:
            cached = json.loads(destination.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and {k: v for k, v in cached.items() if k != "created_at"} == core:
                return PackageResult(destination, cached, True)
        except ValueError:
            pass
    output = {**core, "created_at": datetime.now(timezone.utc).isoformat()}
    atomic_json(destination, output)
    return PackageResult(destination, output, False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vault", type=Path)
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--claims-from", choices=["auto", *CANDIDATES], default="auto")
    args = parser.parse_args()
    try:
        paths = [p for p in (args.vault / "raw").glob("*.json") if p.stem == args.item_id]
        if len(paths) != 1:
            raise PackageError("No matching capture found.")
        result = build_extraction(paths[0], claims_from=args.claims_from)
        from engine.placement import apply_storage_stub
        placement = apply_storage_stub(result.path)
        print(f"{'Cached' if result.reused else 'Packaged'}: {len(result.record['claims'])} claims | {len(result.record['entities'])} entities | quality={result.record['quality_status']}")
        print(f"Placement: {placement['status']} | graph_applied={placement['graph_applied']}")
        print(result.path)
    except (PackageError, EntityError, ExtractionError, JudgeError, TriageError, OSError, ValueError) as error:
        safe = isinstance(error, (PackageError, EntityError, ExtractionError, JudgeError, TriageError))
        parser.exit(1, f"Handoff pending: {str(error) if safe else type(error).__name__}\n")
    return 0


if __name__ == "__main__":
    main()
