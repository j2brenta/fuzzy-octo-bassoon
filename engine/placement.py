"""Storage-only adapter pending Victor's graph.apply contract; no graph writes."""

import json
from pathlib import Path

from engine.artifacts import atomic_json, fingerprint

class PlacementError(ValueError):
    """Storage-only handoff validation failed."""


def apply_storage_stub(record_path: Path) -> dict:
    """Save an idempotent handoff receipt, never claim graph placement succeeded."""
    record_path = Path(record_path)
    if record_path.parent.name != "processed":
        raise PlacementError("Storage handoff requires a processed extraction record.")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("stage") != "processed_extraction":
        raise PlacementError("Storage handoff requires a processed extraction record.")
    core = {k: v for k, v in record.items() if k not in {"created_at", "bundle_id"}}
    if record.get("bundle_id") != fingerprint(core) or record.get("item_id") != record_path.stem:
        raise PlacementError("Processed extraction identity failed validation.")
    reviews = record.get("reviews")
    if not isinstance(reviews, dict):
        raise PlacementError("Processed extraction reviews are missing.")
    for key, id_key, collection, omissions_key in (
        ("claims", "claim_id", "claims", "omitted_concepts"),
        ("entities", "entity_id", "entities", "omitted_entities"),
    ):
        reviewed = reviews.get(key)
        if not isinstance(reviewed, dict):
            raise PlacementError("Processed extraction review status is missing.")
        if reviewed.get("status") == "accepted":
            rows = reviewed.get(collection)
            expected = record.get(collection)
            if (not isinstance(rows, list) or not isinstance(expected, list)
                or any(not isinstance(r, dict) or r.get("verdict") != "supported" for r in rows)
                or {r.get(id_key) for r in rows} != {r.get(id_key) for r in expected}
                or len(rows) != len(expected) or reviewed.get(omissions_key) != []):
                raise PlacementError("Accepted handoff review contains unresolved findings.")
    triage = record.get("triage")
    if triage is not None and not isinstance(triage, dict):
        raise PlacementError("Processed extraction triage is invalid.")
    quality_passed = (record.get("quality_status") == "accepted"
                      and all(reviews[key].get("status") == "accepted" for key in ("claims", "entities"))
                      and (triage or {}).get("keep") is True)
    if record.get("quality_gate_passed") is not quality_passed:
        raise PlacementError("Processed extraction quality gate is inconsistent.")
    output = {
        "schema_version": 1, "adapter": "storage_only_v1", "item_id": record["item_id"],
        "bundle_id": record["bundle_id"], "processed_path": f"processed/{record_path.name}",
        "status": "stored_only" if quality_passed else "pending_quality", "graph_applied": False,
        "quality_status": record["quality_status"], "quality_gate_passed": quality_passed,
        "reason": "graph_adapter_pending" if quality_passed else "quality_gate_not_passed",
        "node_ids": [], "edge_ids": [],
    }
    destination = record_path.parent.parent / "placements" / record_path.name
    if destination.exists():
        try:
            if json.loads(destination.read_text(encoding="utf-8")) == output:
                return output
        except ValueError:
            pass
    atomic_json(destination, output)
    return output
