"""The vault: files are the source of truth, wiki/ is a generated Obsidian view of the store.

Layout (ingestion owns raw/ … processed/; this module owns wiki/, overrides.yaml and log.md):
    raw/<item_id>.json          captures, never changed
    processed/<item_id>.json    Extraction handoff records, replayed into the store
    relations/<item_id>.json    relation verdicts, written once by `refresher relate` and replayed
    overrides.yaml              the user's word: durability, trust, votes
    wiki/items|people|sources/  generated notes; text below MARK and user_* frontmatter are kept
    wiki/health.md, index.md, log.md
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from graph.memory import MemoryStore
from graph.store import GraphStore
from refresher import km
from refresher.schemas import Extraction, Override, Relation

MARK = "<!-- your notes: everything below this line is kept on rebuild -->"
USER_KEYS = ("user_durability", "user_trust")
INVERSE = {"DUPLICATES": "duplicated by", "SUPPORTS": "supported by", "SUPERSEDES": "superseded by",
           "CONTRADICTS": "contradicted by", "ELABORATES": "elaborated by"}
UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]]+')


# --- truth: read ---

def load_extractions(vault: Path) -> list[tuple[Extraction, str]]:
    """Processed records with their raw text, ordered by event time so replays are deterministic."""
    out = []
    for path in sorted((vault / "processed").glob("*.json")):
        ex = Extraction.model_validate_json(path.read_text(encoding="utf-8"))
        raw = vault / (ex.raw_path or f"raw/{ex.item_id}.json")
        text = json.loads(raw.read_text(encoding="utf-8")).get("text", "") if raw.exists() else ""
        out.append((ex, text))
    return sorted(out, key=lambda pair: (pair[0].source.event_time, pair[0].item_id))


def load_relation(vault: Path, extraction: Extraction) -> Relation | None:
    """The stored verdicts for an item, or None when there are none or they no longer fit its claims."""
    path = vault / "relations" / f"{extraction.item_id}.json"
    if not path.exists():
        return None
    try:
        relation = Relation.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as error:
        print(f"warning: ignoring {path.name}: {error.errors()[0]['msg']}", file=sys.stderr)
        return None
    return relation if relation.matches(extraction) else None


def save_relation(vault: Path, relation: Relation) -> Path:
    path = vault / "relations" / f"{relation.item_id}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(relation.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def load_overrides(vault: Path) -> list[Override]:
    path = vault / "overrides.yaml"
    rows = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    return [Override(**row) for row in rows or []]


def save_overrides(vault: Path, overrides: list[Override]) -> None:
    rows = [o.model_dump(mode="json", exclude_none=True) for o in overrides]
    (vault / "overrides.yaml").write_text(yaml.safe_dump(rows, allow_unicode=True, sort_keys=False), encoding="utf-8")


def note_overrides(vault: Path) -> list[Override]:
    """user_durability on item notes and user_trust on people notes, edited by hand in Obsidian."""
    out = []
    for kind, key, field in (("item", "user_durability", "durability_class"), ("person", "user_trust", "trust")):
        for path in sorted((vault / "wiki" / ("items" if kind == "item" else "people")).glob("*.md")):
            meta, _ = _split(path.read_text(encoding="utf-8"))
            if meta.get(key) in (None, ""):
                continue
            try:
                out.append(Override(target=kind, target_id=meta["item_id" if kind == "item" else "person_id"],
                                    **{field: meta[key]}, note="from note frontmatter"))
            except (KeyError, ValidationError) as error:
                print(f"warning: ignoring {key} in {path.name}: {error}", file=sys.stderr)
    return out


def merged_overrides(vault: Path) -> list[Override]:
    """overrides.yaml plus note edits; a note edit replaces an older value for the same target and field."""
    merged: dict[tuple, Override] = {}
    for o in load_overrides(vault) + note_overrides(vault):
        field = "durability" if o.durability_class else "trust" if o.trust is not None else f"vote-{len(merged)}"
        merged[(o.target, o.target_id, field)] = o
    return list(merged.values())


def replay(vault: Path, overrides: list[Override] | None = None, include_unreviewed: bool = False) -> MemoryStore:
    store = MemoryStore(include_unreviewed=include_unreviewed)
    for ex, text in load_extractions(vault):
        store.apply(ex, text, load_relation(vault, ex))
    for o in merged_overrides(vault) if overrides is None else overrides:
        store.apply_override(o)
    return store


# --- views: write ---

def _split(text: str) -> tuple[dict, str]:
    if text.startswith("---\n") and "\n---\n" in text[4:]:
        head, body = text[4:].split("\n---\n", 1)
        return yaml.safe_load(head) or {}, body
    return {}, text


def _safe(name: str, limit: int = 80) -> str:
    return UNSAFE.sub(" ", name).strip()[:limit].strip() or "untitled"


def _write_note(path: Path, meta: dict, body: str, written: set[Path]) -> None:
    kept_meta, kept_notes = {}, ""
    if path.exists():
        old_meta, old_body = _split(path.read_text(encoding="utf-8"))
        kept_meta = {k: old_meta.get(k) for k in USER_KEYS if k in old_meta}
        if MARK in old_body:
            kept_notes = old_body.split(MARK, 1)[1].lstrip("\n")
    meta = {**meta, **kept_meta}
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{front}\n---\n{body.rstrip()}\n\n{MARK}\n{kept_notes}", encoding="utf-8")
    written.add(path)


def note_names(store: GraphStore) -> dict[str, str]:
    """Stable, readable, unique note names for items, people and sources, used as wikilink targets."""
    names = {}
    for item in store.items():
        names[item.item_id] = _safe(f"{item.source.event_time:%Y-%m-%d} {item.title[:60]} {item.item_id[:6]}")
    for p in store.people():
        names[p.person_id] = _safe(p.name)
    for s in store.sources():
        names[s.key] = _safe(s.name)
    return names


def export(store: MemoryStore, vault: Path, at: datetime) -> list[Path]:
    """Write wiki/ from the store. Same store and `at` give the same bytes."""
    wiki = vault / "wiki"
    names = note_names(store)
    link = lambda key: f"[[{names[key]}]]" if key in names else f"`{key[:12]}`"
    scores = km.score_claims(store, at)
    rel: dict[str, list[tuple[str, str, str]]] = {}  # node -> (label, other node, rationale), both directions
    for e in store.edges():
        if e.type in INVERSE:
            rel.setdefault(e.src, []).append((e.type.lower(), e.dst, e.rationale))
            rel.setdefault(e.dst, []).append((INVERSE[e.type], e.src, e.rationale))
    claim_item = {c.claim.claim_id: c.item_id for c in store.claims()}
    written: set[Path] = set()

    for item in sorted(store.items(), key=lambda i: i.item_id):
        src = item.source
        claims = sorted((s for s in scores.values() if s.node.item_id == item.item_id),
                        key=lambda s: -s.freshness)
        lines = [f"# {item.title}", "",
                 " · ".join(filter(None, [f"in {link(item.source_key)}" if item.source_key else None,
                                         f"by {link(item.author_id)}" if item.author_id else None,
                                         f"forwarded by {src.forwarded_by.name}" if src.forwarded_by and src.forwarded_by.name else None,
                                         f"{src.event_time:%Y-%m-%d}"]))]
        if src.url:
            lines.append(f"[original]({src.url})")
        if item.review_status != "accepted":
            lines += ["", f"> [!warning] Unreviewed: ingestion quality {item.review_status}. "
                          "Placed by `rebuild --include-unreviewed`; check the claims before relying on them."]
        if rel.get(item.item_id):
            lines.append("")
        for label, other, _ in rel.get(item.item_id, []):
            lines.append(f"> [!note] {'Repost of' if label == 'duplicates' else 'Reposted as'} {link(other)}")
        lines += ["", "## Claims", ""]
        for s in claims:
            c = s.node
            flags = " · noise" if s.noise else ""
            lines.append(f"- **{c.claim.kind} · {c.durability} · {s.freshness:.2f} {s.band}{flags}** {c.claim.statement}")
            for label, other, why in rel.get(c.claim.claim_id, []):
                lines.append(f"    - {label} a claim in {link(claim_item.get(other, other))}: {why}")
        mentions = sorted({e.dst for e in store.edges() if e.src == item.item_id and e.type == "MENTIONS"})
        if mentions:
            lines += ["", "## Mentions", "", ", ".join(link(m) if m in names else store.organizations().get(m, m)
                                                     for m in mentions)]
        if item.text:
            lines += ["", "## Original", "", *("> " + ln if ln else ">" for ln in item.text.splitlines())]
        meta = {"type": "item", "item_id": item.item_id, "channel": src.channel, "source": src.source_name,
                "author": store.person(item.author_id).name if item.author_id else None,
                "event_time": src.event_time.isoformat(), "captured_at": src.captured_at.isoformat(),
                "url": src.url, "durability": item.durability, "duplicate_of": item.duplicate_of,
                "review_status": item.review_status, "user_durability": None}
        _write_note(wiki / "items" / f"{names[item.item_id]}.md", meta, "\n".join(lines), written)

    for p in sorted(store.people(), key=lambda p: p.person_id):
        lines = [f"# {p.name}", "", f"Trust {store.trust(p.person_id):.2f} · enrichment {p.enrichment_status}"]
        for head, ids in (("Wrote", p.authored), ("Mentioned in", p.mentioned_in)):
            if ids:
                lines += ["", f"## {head}", ""] + [f"- {link(i)}" for i in sorted(ids, key=lambda i: names[i])]
        meta = {"type": "person", "person_id": p.person_id, "name": p.name, "handles": sorted(p.handles),
                "trust": round(store.trust(p.person_id), 3), "enrichment_status": p.enrichment_status,
                "user_trust": None}
        _write_note(wiki / "people" / f"{names[p.person_id]}.md", meta, "\n".join(lines), written)

    for s in sorted(store.sources(), key=lambda s: s.key):
        lines = [f"# {s.name}", "", f"Channel: {s.channel}", "", "## Items", ""]
        lines += [f"- {link(i)}" for i in sorted(s.items, key=lambda i: names[i])]
        _write_note(wiki / "sources" / f"{names[s.key]}.md", {"type": "source", "source_key": s.key,
                    "channel": s.channel}, "\n".join(lines), written)

    # Remove generated notes that no longer exist in the store, unless the user wrote in them.
    for folder in ("items", "people", "sources"):
        for path in (wiki / folder).glob("*.md") if (wiki / folder).exists() else []:
            if path not in written and not path.read_text(encoding="utf-8").split(MARK, 1)[-1].strip():
                path.unlink()

    report = km.run(store, at)
    health = wiki / "health.md"
    health.write_text(km.render(report, store), encoding="utf-8")
    index = ["# Knowledge base", "", f"Built {at:%Y-%m-%d %H:%M %Z}. See [[health]].", ""]
    for head, keys in (("Items", [i.item_id for i in sorted(store.items(), key=lambda i: i.source.event_time, reverse=True)]),
                       ("People", sorted((p.person_id for p in store.people()), key=lambda k: names[k])),
                       ("Sources", sorted((s.key for s in store.sources()), key=lambda k: names[k]))):
        index += [f"## {head}", ""] + [f"- {link(k)}" for k in keys] + [""]
    (wiki / "index.md").write_text("\n".join(index), encoding="utf-8")
    return sorted(written | {health, wiki / "index.md"})


def log(vault: Path, at: datetime, message: str) -> None:
    with (vault / "log.md").open("a", encoding="utf-8") as f:
        f.write(f"- {at:%Y-%m-%d %H:%M} {message}\n")
