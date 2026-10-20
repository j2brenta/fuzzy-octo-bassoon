"""Deterministic artifact identity and atomic JSON publication."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

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
