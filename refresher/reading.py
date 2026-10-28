"""What you have read: the reading map's memory, kept in the vault as reading/log.jsonl.

Each line is one event: an item marked read, skimmed or unread, or a visit to a topic's page. The latest event for an
item wins, so the log only grows and a mistake is undone by marking again. The page uses it to say what a source would
add that you have not read yet, how much of a topic you have covered, and what is new since your last visit. Like
votes it is yours, not the sources': claims and reviews never read it. Later the knowledge half-life may (what you
read and when is what you may have forgotten).
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock

STATES = ("read", "skimmed", "unread")
LOG = Path("reading") / "log.jsonl"
SESSION = timedelta(hours=1)  # visits this close together are one sitting: reloading keeps what was new
_write = Lock()  # the app's request threads append to one file


def load(vault: Path) -> dict:
    """{"items": {item_id: {"state", "at"}}, "visits": {scope_id: [the sitting before, the latest sitting]}}, each
    sitting the time of its first visit; an unread mark removes the item. A line that does not parse is skipped."""
    items, visits = {}, {}
    try:
        lines = (vault / LOG).read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("item") and event.get("state") in STATES:
            if event["state"] == "unread":
                items.pop(event["item"], None)
            else:
                items[event["item"]] = {"state": event["state"], "at": event.get("at")}
        elif event.get("visit") and isinstance(event.get("at"), str):
            seen = visits.setdefault(event["visit"], [None, None])
            if not _same_sitting(seen[1], event["at"]):
                seen[0], seen[1] = seen[1], event["at"]
    return {"items": items, "visits": visits}


def _same_sitting(start: str | None, at: str) -> bool:
    try:
        return start is not None and datetime.fromisoformat(at) - datetime.fromisoformat(start) < SESSION
    except ValueError:
        return False


def _append(vault: Path, event: dict) -> None:
    path = vault / LOG
    with _write:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


def mark(vault: Path, item_id: str, state: str, at: datetime) -> None:
    if state not in STATES:
        raise ValueError(f"state must be one of {', '.join(STATES)}")
    if not item_id or len(item_id) > 200:
        raise ValueError("item must be an item id")
    _append(vault, {"at": at.isoformat(), "item": item_id, "state": state})


def visit(vault: Path, scope_id: str, at: datetime) -> None:
    if not scope_id or len(scope_id) > 300:
        raise ValueError("visit must be a topic, a cluster or a phrase")
    _append(vault, {"at": at.isoformat(), "visit": scope_id})


def for_scope(log: dict, scope_id: str, item_ids, now: datetime) -> dict:
    """The part of the log one page needs: the marks on its items and the visit before this sitting."""
    wanted = set(item_ids)
    earlier, latest = log["visits"].get(scope_id, [None, None])
    recent = _same_sitting(latest, now.isoformat())
    return {"items": {i: v for i, v in log["items"].items() if i in wanted},
            "last_visit": earlier if recent else latest}
