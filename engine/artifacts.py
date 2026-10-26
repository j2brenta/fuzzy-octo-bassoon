"""Deterministic artifact identity and atomic JSON publication."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import re
from uuid import uuid4

def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def atomic_json(destination: Path, value: dict) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False, suffix=".tmp") as file:
            temporary = Path(file.name)
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        archive_descendants(destination, value)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)




def save_rejected(raw_path: Path, stage: str, attempt: int, error: Exception, reply=None) -> str:
    """Keep a rejected model answer under <vault>/rejected/<stage>/ for inspection; the pipeline never reads it.

    Returns the validation message so callers can report every attempt's reason.
    """
    raw_path = Path(raw_path)
    destination = raw_path.parent.parent / "rejected" / stage / f"{raw_path.stem}.{attempt + 1}.json"
    atomic_json(destination, {
        "stage": stage, "item_id": raw_path.stem, "attempt": attempt + 1, "error": str(error),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": getattr(reply, "actual_model", None), "data": getattr(reply, "data", None),
    })
    return str(error)


# Dependencies among live stage folders. Raw sources and seed fixtures are never moved.
DEPENDENCIES = {
    "documents": ["triage", "extractions", "entities"],
    "triage": ["extractions"],
    "extractions": ["reviews", "repairs", "coverage", "evidence", "processed"],
    "reviews": ["repairs", "coverage", "evidence", "processed"],
    "repairs": ["repair-reviews", "coverage", "evidence", "processed"],
    "repair-reviews": ["coverage", "evidence", "processed"],
    "coverage": ["coverage-reviews", "evidence", "processed"],
    "coverage-reviews": ["evidence", "processed"],
    "evidence": ["evidence-reviews", "processed"],
    "evidence-reviews": ["processed"],
    "entities": ["entity-reviews", "processed"],
    "entity-reviews": ["processed"],
    "processed": ["placements"],
}


def archive_descendants(destination: Path, value: dict) -> list[str]:
    """Remove stale derived stages from the live pipeline, retaining recoverable files."""
    destination = Path(destination)
    if destination.parent.name not in DEPENDENCIES or not re.fullmatch(r"[0-9a-f]{64}\.json", destination.name):
        return []
    previous = None
    if destination.exists():
        try:
            previous = json.loads(destination.read_text(encoding="utf-8"))
            if previous == value:
                return []
        except (ValueError, UnicodeError):
            pass
    vault = destination.parent.parent
    if not (vault / "raw" / destination.name).exists():
        return []
    # Copied-message metadata affects source timeline/handoff, not the text or
    # source passages sent to existing extraction/review calls. Preserve those
    # paid caches when this is the only document change.
    copy_metadata_only = (destination.parent.name == "documents"
                          and isinstance(previous, dict)
                          and {k: v for k, v in previous.items() if k != "telegram_copy"}
                          == {k: v for k, v in value.items() if k != "telegram_copy"})
    pending = ["processed"] if copy_metadata_only else list(DEPENDENCIES[destination.parent.name])
    stages = set()
    while pending:
        stage = pending.pop()
        if stage not in stages:
            stages.add(stage)
            pending.extend(DEPENDENCIES.get(stage, []))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    moved = []
    for stage in sorted(stages):
        source = vault / stage / destination.name
        if source.exists():
            target = vault / "stale" / destination.stem / stamp / stage / destination.name
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, target)
            moved.append(stage)
    return moved
