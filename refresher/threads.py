"""Query threads: a question you come back to, as a line of stamped briefs, and what arrived since the last one.

Following a query (or a topic) makes it a thread. Stamping a thread stores its brief as it stands (the context of
refresher/context.py with its written paragraphs, refresher/brief.py) as an immutable snapshot in
threads/<slug>/<stamp>.json and .md: you are caught up through that moment. The next time, the brief opens on what
arrived since: the sources captured after the stamp that bear on the query, each sorted into

    critical  it updates or contradicts a thesis the last brief held; it starts a new major idea with a base of its
              own; it becomes the new base of an idea the brief had; its writer becomes a top authority on the query
    note      it builds on an idea the brief had, or lends one facts, or starts a minor idea
    noise     it retells a known thesis (or reposts), it is an older view something already replaced, or it is
              only near the query

and inside each class by relevance x impact x freshness. Something not near the query at all is not listed. Arrival
is capture time ("what reached me"); which view replaced which still goes by event time.

Snapshots are what you decided you know, so they live in the vault, not in a cache, and are never rewritten.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from engine.artifacts import atomic_json
from refresher import brief, context, thesis_relate

NEAR = 0.1  # an arrival this far under the floor is listed as noise ("only near the query"); further, not at all
IMPACT = {"updates": 1.0, "contradicts": 1.0, "new idea": 0.9, "new base": 0.8, "new authority": 0.6,
          "builds on": 0.5, "lends facts": 0.3, "minor idea": 0.3, "retells": 0.1, "outdated": 0.1, "near": 0.05}
TOP_AUTHORITY = 0.75  # the authority from which a new writer is critical


def slug(text: str) -> str:
    s = re.sub(r"[^\w]+", "-", text.casefold(), flags=re.UNICODE).strip("-")
    return s[:60] or "thread"


def folder(vault: Path, name: str) -> Path:
    return vault / "threads" / name


def stamp_name(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def follow(vault: Path, query: str | None = None, key: str | None = None, at: datetime | None = None,
           title: str | None = None) -> dict:
    """Start a thread (or return the one that exists) for a phrase or a topic key."""
    at = at or datetime.now(timezone.utc)
    name = slug(key or query or "")
    path = folder(vault, name) / "thread.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    record = {"slug": name, "query": query, "key": key, "title": title or query or key, "created": at.isoformat()}
    atomic_json(path, record)
    return record


def load(vault: Path, name: str) -> dict | None:
    try:
        return json.loads((folder(vault, name) / "thread.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def snapshots(vault: Path, name: str) -> list[dict]:
    """Every stamped snapshot of a thread, oldest first."""
    out = []
    for p in sorted(folder(vault, name).glob("2*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return out


def all_threads(vault: Path) -> list[dict]:
    root = vault / "threads"
    out = []
    for p in sorted(root.glob("*/thread.json")) if root.exists() else []:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return out


def _context(store, thread: dict, at: datetime, pool=None) -> dict | None:
    return context.build(store, key=thread.get("key"), query=thread.get("query"), at=at, pool=pool)


def stamp(vault: Path, store, name: str, at: datetime | None = None, model=None, pool=None) -> dict:
    """Store the thread's brief as of `at` as a snapshot; written paragraphs come from the model or the cache."""
    at = at or datetime.now(timezone.utc)
    thread = load(vault, name)
    if thread is None:
        raise KeyError(name)
    c = _context(store, thread, at, pool)
    if c is None:
        raise LookupError("Nothing in your knowledge matches this thread yet.")
    written = brief.write(vault, c, model)
    snapshot = {"thread": name, "query": thread.get("query"), "key": thread.get("key"), "title": thread["title"],
                "at": at.isoformat(), "stamp": stamp_name(at),
                "items": sorted(context.captured(store, at)),
                "major": [{"id": x["id"], "parts": x.get("parts", [x["id"]]), "title": x["title"], "base": x["base"],
                           "sources": x["sources"], "theses": [m["thesis"] for m in x["members"]],
                           "previously": x["previously"], "text": written["texts"][x["id"]]}
                          for x in c["major"]],
                "also": [{"id": x["id"], "title": x["title"], "base": x["base"],
                          "theses": [m["thesis"] for m in x["members"]]} for x in c["also"]],
                "people": [{"id": r["id"], "name": r["name"], "authority": r["authority"]} for r in c["people"]],
                "sources": {i: {"title": s["title"], "source": s["source"], "when": s["when"]}
                            for i, s in c["sources"].items()},
                "ids": written["ids"], "counts": c["counts"], "validity": written["validity"]}
    out = folder(vault, name) / f"{snapshot['stamp']}.json"
    atomic_json(out, snapshot)
    (out.with_suffix(".md")).write_text(brief.markdown(c, written), encoding="utf-8")
    wiki = vault / "wiki" / "briefs"
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / f"{name}.md").write_text(brief.markdown(c, written), encoding="utf-8")
    return snapshot


def delta(store, last: dict, current: dict, pool) -> dict:
    """What arrived after `last` (a snapshot) on the thread, sorted against it, with `current` the context now."""
    covered = set(last["items"])
    items = context.captured(store, datetime.fromisoformat(current["at"]))
    arrived = sorted(i for i in items if i not in covered)
    known_theses = {t for x in last["major"] + last.get("also", []) for t in x["theses"]}
    known_ideas = {p: x for x in last["major"] + last.get("also", []) for p in x.get("parts", [x["id"]])}
    known_people = {r["id"] for r in last.get("people", [])}
    edges = thesis_relate.edges(getattr(store, "thesis_relations", {}) or {}, pool.of_item)
    floor = current["floor"]
    ideas = current["major"] + current["also"]
    place: dict[str, list[tuple[dict, str]]] = {}
    for x in ideas:
        for i, role in x["sources"].items():
            place.setdefault(i, []).append((x, role))
    lent = {a["item"]: x for x in current["major"] for a in x.get("adjacent", [])}
    authority = {r["id"]: r for r in current["people"]}
    from refresher import authority as authority_mod
    critical, notes, noise = [], [], {}

    def row(i, kind, idea=None, about=None, why=""):
        s = current["sources"].get(i) or {}
        it = store.item(i)
        relevance = max(current["on_subject"].get(i, 0.0), (idea or {}).get("relevance", 0.0))
        fresh = (idea or {}).get("freshness", 1.0)
        return {"item": i, "kind": kind, "idea": idea["id"] if idea else None, "idea_title": idea["title"] if idea else None,
                "about": about, "why": why, "title": s.get("title") or it.title,
                "source": s.get("source") or it.source.source_name, "captured": it.source.captured_at.isoformat(),
                "when": it.source.event_time.isoformat(),
                "order": round(relevance * IMPACT[kind] * (0.5 + 0.5 * fresh), 4)}

    for i in arrived:
        relevance = current["on_subject"].get(i)
        on_thread = i in place or i in lent  # part of an idea on the query, or lending one facts, whatever its thesis
        if not on_thread and (relevance is None or relevance < floor - NEAR):
            continue  # not about this thread
        mine = set(t["id"] for t in pool.of_item.get(i, []))
        hit = next((e for e in edges if e["label"] in ("UPDATES", "CONFLICTS") and
                    ((e["thesis"] in mine and e["target"] in known_theses) or (e["target"] in mine and e["thesis"] in known_theses))), None)
        placed = sorted(place.get(i, []), key=lambda xr: context.ROLE_RANK[xr[1]])
        idea, role = placed[0] if placed else (None, None)
        if hit and role != "superseded":
            other = hit["target"] if hit["thesis"] in mine else hit["thesis"]
            kind = "updates" if hit["label"] == "UPDATES" else "contradicts"
            critical.append(row(i, kind, idea, other, hit["rationale"]))
            continue
        if role == "superseded":
            noise.setdefault("outdated", []).append(row(i, "outdated", idea, None, "an older view the brief already replaced"))
            continue
        if store.item(i).duplicate_of or role == "rewrite":
            noise.setdefault("retells", []).append(row(i, "retells", idea, idea["base"] if idea else store.item(i).duplicate_of,
                                                       "retells a known thesis"))
            continue
        wid = authority_mod.writer(store, store.item(i))[0]
        if role == "base":
            before = next((known_ideas[p] for p in idea.get("parts", [idea["id"]]) if p in known_ideas), None)
            if before is None and idea in current["major"]:
                critical.append(row(i, "new idea", idea, None, "a new major idea with a base of its own"))
            elif before is not None and before["base"] != i:
                critical.append(row(i, "new base", idea, before["base"], "the source to read on this idea now"))
            elif idea in current["major"]:
                notes.append(row(i, "minor idea", idea))
            else:
                notes.append(row(i, "minor idea", idea, None, "a minor idea on the query"))
            continue
        if wid in authority and wid not in known_people and authority[wid]["authority"] >= TOP_AUTHORITY:
            critical.append(row(i, "new authority", idea, wid, f"{authority[wid]['name']} is now a top voice here"))
            continue
        if role == "builder":
            notes.append(row(i, "builds on", idea, idea["base"]))
        elif i in lent:
            notes.append(row(i, "lends facts", lent[i]))
        else:
            noise.setdefault("near", []).append(row(i, "near", None, None, "near the query, part of no idea in it"))
    for rows in (critical, notes, *noise.values()):
        rows.sort(key=lambda r: (-r["order"], r["captured"], r["item"]))
    listed = len(critical) + len(notes) + sum(len(v) for v in noise.values())
    return {"since": last["at"], "stamp": last["stamp"], "arrived": listed, "critical": critical, "notes": notes,
            "noise": noise}


def view(vault: Path, store, name: str, at: datetime | None = None, pool=None) -> dict | None:
    """A thread now: its context, and since its last stamp what arrived."""
    at = at or datetime.now(timezone.utc)
    thread = load(vault, name)
    if thread is None:
        return None
    pool = pool or thesis_relate.Pool(store)
    c = _context(store, thread, at, pool)
    stamps = snapshots(vault, name)
    last = next((s for s in reversed(stamps) if datetime.fromisoformat(s["at"]) <= at), None)
    return {"thread": thread, "context": c, "stamps": [{"stamp": s["stamp"], "at": s["at"], "counts": s["counts"]}
                                                       for s in stamps],
            "delta": delta(store, last, c, pool) if last and c else None}


def summary(vault: Path, store, at: datetime | None = None, pool=None) -> list[dict]:
    """Home: every thread with its last stamp and what arrived since."""
    at = at or datetime.now(timezone.utc)
    pool = pool or thesis_relate.Pool(store)
    rows = []
    for t in all_threads(vault):
        v = view(vault, store, t["slug"], at, pool)
        d = v["delta"] if v else None
        rows.append({**t, "last": v["stamps"][-1]["at"] if v and v["stamps"] else None,
                     "stamps": len(v["stamps"]) if v else 0,
                     "arrived": d["arrived"] if d else None, "critical": len(d["critical"]) if d else None,
                     "notes": len(d["notes"]) if d else None,
                     "noise": sum(len(x) for x in d["noise"].values()) if d else None})
    return sorted(rows, key=lambda r: (-(r["critical"] or 0), r["title"] or ""))


def diff(a: dict, b: dict) -> dict:
    """Two snapshots of a thread: ideas added, dropped, rebased, and views replaced between them."""
    ia = {p: x for x in a["major"] for p in x.get("parts", [x["id"]])}
    ib = {p: x for x in b["major"] for p in x.get("parts", [x["id"]])}
    added = [x for x in b["major"] if not any(p in ia for p in x.get("parts", [x["id"]]))]
    dropped = [x for x in a["major"] if not any(p in ib for p in x.get("parts", [x["id"]]))]
    rebased = [{"idea": x["title"], "was": ia[p]["base"], "now": x["base"]} for x in b["major"]
               for p in x.get("parts", [x["id"]])[:1] if p in ia and ia[p]["base"] != x["base"]]
    old = {p["old"] for x in a["major"] for p in x["previously"]}
    replaced = [p for x in b["major"] for p in x["previously"] if p["old"] not in old]
    return {"from": a["stamp"], "to": b["stamp"], "added": [{"id": x["id"], "title": x["title"]} for x in added],
            "dropped": [{"id": x["id"], "title": x["title"]} for x in dropped], "rebased": rebased,
            "replaced": replaced}
