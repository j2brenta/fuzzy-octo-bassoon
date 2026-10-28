"""The vault: files are the source of truth, wiki/ is a generated Obsidian view of the store.

Layout (ingestion owns raw/ … processed/; this module owns wiki/, overrides.yaml and log.md):
    raw/<item_id>.json          captures, never changed
    processed/<item_id>.json    Extraction handoff records, replayed into the store
    relations/<item_id>.json    relation verdicts, written once by `refresher relate` and replayed
    graph-reviews/<time>.json   the graph review's automatic decisions, written by `refresher review` and replayed
                                (reviews/ is ingestion's extraction reviews)
    graph-reviews/proposals/<id>.json   what the review suggests and the user decides (graph/proposals.py)
    graph-reviews/decisions/<time>-<id>.json   the user's answers, append-only; the latest per proposal counts
    people/<person>.json        web enrichment with a source for every field, written by `refresher enrich`;
                                a match's affiliation becomes a tie to that organization
    overrides.yaml              the user's word: durability, trust, votes
    submissions/<id>.json, uploads/<sha256>   web captures and their originals (refresher/web.py)
    wiki/items|ideas|clusters|topics|dossiers|people|organizations|sources/  generated notes; text below MARK and user_*
                                frontmatter are kept (a dossier ranks a topic: refresher/dossier.py)
    wiki/health.md, index.md, log.md
"""
import fcntl
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from graph import proposals as proposal_stage
from graph.memory import MemoryStore
from graph.store import AFFILIATION_WORDS, Affiliation, Decision, Edge, GraphStore, Proposal, ReviewReport
from refresher import dossier, km, scoring
from refresher import translate
from engine import language
from refresher.schemas import Extraction, Override, Relation

MARK = "<!-- your notes: everything below this line is kept on rebuild -->"
USER_KEYS = ("user_durability", "user_trust")
INVERSE = {"DUPLICATES": "duplicated by", "SUPPORTS": "supported by", "SUPERSEDES": "superseded by",
           "CONTRADICTS": "contradicted by", "ELABORATES": "elaborated by"}
UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]]+')


# --- writing safely: the bot, the hourly review and the accept page can write at the same time ---

def atomic_write(path: Path, text: str) -> Path:
    """Write to a temporary file beside `path`, then rename it over: a reader sees the old file or the new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


@contextmanager
def lock(vault: Path):
    """One writer at a time for read-check-write steps (a decision, a review, a rebuild)."""
    vault.mkdir(parents=True, exist_ok=True)
    with (vault / ".refresher.lock").open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


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
    return atomic_write(vault / "relations" / f"{relation.item_id}.json", relation.model_dump_json(indent=2) + "\n")


def save_review(vault: Path, report: ReviewReport) -> Path:
    path = vault / "graph-reviews" / f"{report.at.astimezone().strftime('%Y%m%dT%H%M%S%z')}.json"
    data = {"at": report.at.isoformat(),
            "item_duplicates": [{"src": e.src, "dst": e.dst, "rationale": e.rationale} for e in report.item_duplicates],
            "firmed": report.firmed, "merged_ideas": report.merged_ideas, "topics": report.topics,
            "merged_people": report.merged_people}
    for p in report.proposals:
        save_proposal(vault, p)
    return atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def load_reviews(vault: Path) -> list[ReviewReport]:
    out = []
    for path in sorted((vault / "graph-reviews").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        out.append(ReviewReport(datetime.fromisoformat(data["at"]),
                                [Edge(d["src"], "DUPLICATES", d["dst"], d.get("rationale", ""))
                                 for d in data.get("item_duplicates", [])],
                                list(data.get("firmed", [])), [tuple(p) for p in data.get("merged_ideas", [])],
                                dict(data.get("topics", {})), [tuple(p) for p in data.get("merged_people", [])]))
    return sorted(out, key=lambda r: r.at)


def save_proposal(vault: Path, p: Proposal) -> Path:
    data = {"proposal_id": p.proposal_id, "kind": p.kind, "payload": p.payload, "rationale": p.rationale,
            "at": p.at.isoformat(), "fingerprint": p.fingerprint}
    return atomic_write(vault / "graph-reviews" / "proposals" / f"{p.proposal_id}.json",
                        json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def load_proposals(vault: Path) -> list[Proposal]:
    out = []
    for path in sorted((vault / "graph-reviews" / "proposals").glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        out.append(Proposal(d["proposal_id"], d["kind"], d["payload"], d.get("rationale", ""),
                            datetime.fromisoformat(d["at"]), d["fingerprint"]))
    return out


def save_decision(vault: Path, d: Decision) -> Path:
    data = {"proposal_id": d.proposal_id, "action": d.action, "token": d.token, "fingerprint": d.fingerprint,
            "rationale": d.rationale, "at": d.at.isoformat()}
    name = f"{d.at.astimezone().strftime('%Y%m%dT%H%M%S%f%z')}-{d.proposal_id}.json"
    return atomic_write(vault / "graph-reviews" / "decisions" / name, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def load_decisions(vault: Path) -> list[Decision]:
    out = []
    for path in sorted((vault / "graph-reviews" / "decisions").glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        out.append(Decision(d["proposal_id"], d["action"], d["token"], d["fingerprint"], d.get("rationale", ""),
                            datetime.fromisoformat(d["at"])))
    return sorted(out, key=lambda d: d.at)


class StaleToken(Exception):
    """The proposal or its evidence changed after the user looked at it."""


def decide(vault: Path, proposal_id: str, action: str, token: str, rationale: str = "",
           at: datetime | None = None) -> Decision:
    """Record the user's answer if `token` is the one the current graph gives; otherwise raise StaleToken."""
    from datetime import timezone
    if action not in proposal_stage.ACTIONS:
        raise ValueError(f"action must be one of {', '.join(proposal_stage.ACTIONS)}")
    with lock(vault):
        store = replay(vault, include_unreviewed=True)
        state = store.proposals.get(proposal_id)
        if state is None:
            raise KeyError(proposal_id)
        if state.token != token:
            raise StaleToken("The evidence or the proposal changed since you looked; look again.")
        if action == "accept" and state.status in ("stale", "unplaced"):
            raise StaleToken(state.reason)
        current = proposal_stage.fingerprint(store, state.proposal.kind, state.proposal.payload)
        decision = Decision(proposal_id, action, token, current, rationale, at or datetime.now(timezone.utc))
        save_decision(vault, decision)
        return decision


def load_overrides(vault: Path) -> list[Override]:
    path = vault / "overrides.yaml"
    rows = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    return [Override(**row) for row in rows or []]


def save_overrides(vault: Path, overrides: list[Override]) -> None:
    rows = [o.model_dump(mode="json", exclude_none=True) for o in overrides]
    atomic_write(vault / "overrides.yaml", yaml.safe_dump(rows, allow_unicode=True, sort_keys=False))


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
    with store.deferred():  # one recomputation for the whole vault
        for ex, text in load_extractions(vault):
            store.apply(ex, text, load_relation(vault, ex))
    for report in load_reviews(vault):
        store.apply_review(report)
    # Every state is judged against the graph before any proposal applies, so the order of decisions never
    # changes a fingerprint, and the accept page sees the same tokens a rebuild does.
    decisions = load_decisions(vault)
    store.proposals = {p.proposal_id: proposal_stage.state(store, p, decisions) for p in load_proposals(vault)}
    for state in sorted(store.proposals.values(), key=lambda s: (s.proposal.at, s.proposal.proposal_id)):
        store.apply_proposal(state)
    for o in merged_overrides(vault) if overrides is None else overrides:
        store.apply_override(o)
    people = {p.person_id: p for p in store.people()}
    # A record for a merged-away person serves the person they merged into, unless that one has its own.
    for record in sorted(load_enrichments(vault), key=lambda r: r["person_id"] not in people):
        person = people.get(store.canonical_person(record["person_id"]))
        if person is not None and person.enrichment_status == "pending":
            person.enrichment_status = "enriched" if record.get("match") else "no_match"
            person.role, person.affiliation = record.get("role"), record.get("affiliation")
            person.domains = list(record.get("domains") or [])
            if record.get("match"):
                store.add_enrichment(person.person_id, record.get("affiliation"))
    store.use_cluster_names(load_cluster_names(vault))
    store.use_themes(load_themes(vault))
    store.translations = translate.load_all(vault)  # item id -> passages in the output language, for reading
    return store


CLUSTER_NAMES = Path(".cache") / "cluster-names.json"


def load_cluster_names(vault: Path) -> dict[str, str]:
    """Cluster fingerprint -> name a model gave it (a cache: without it clusters take their central Idea)."""
    try:
        data = json.loads((vault / CLUSTER_NAMES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v["title"] for k, v in data.items() if isinstance(v, dict) and isinstance(v.get("title"), str)}


CLUSTER_THEMES = Path(".cache") / "cluster-themes.json"


def load_themes(vault: Path) -> dict[str, dict]:
    """Canonical claim id -> where a model placed that Idea: {"cluster": id or None, "theme": name}."""
    try:
        data = json.loads((vault / CLUSTER_THEMES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def save_themes(vault: Path, placements: dict[str, dict]) -> None:
    from engine.artifacts import atomic_json
    atomic_json(vault / CLUSTER_THEMES, {**load_themes(vault), **placements})


def save_cluster_names(vault: Path, names: dict[str, dict]) -> None:
    from engine.artifacts import atomic_json
    try:
        data = json.loads((vault / CLUSTER_NAMES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    atomic_json(vault / CLUSTER_NAMES, {**data, **names})


def _person_file(vault: Path, person_id: str) -> Path:
    return vault / "people" / f"{person_id.replace(':', '-')}.json"


def load_enrichments(vault: Path) -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted((vault / "people").glob("*.json"))]


def save_enrichment(vault: Path, record: dict) -> Path:
    return atomic_write(_person_file(vault, record["person_id"]), json.dumps(record, indent=2, ensure_ascii=False) + "\n")


def enriched(vault: Path, person_id: str) -> bool:
    return _person_file(vault, person_id).exists()


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
    """Stable, readable, unique note names for items, Ideas, clusters, topics, people and sources, used as wikilink
    targets. An Idea's or a cluster's title can change as members join, so its note is named after its id; an
    accepted topic keeps its title."""
    names = {}
    for idea in store.ideas():
        names[idea.idea_id] = idea.idea_id.replace(":", "-")
    for c in store.clusters():
        names[c.cluster_id] = c.cluster_id.replace(":", "-")
    for t in store.topics():
        names[t.topic_id] = _safe(f"{t.title} (topic)")
    for item in store.items():
        names[item.item_id] = _safe(f"{item.source.event_time:%Y-%m-%d} {item.title[:60]} {item.item_id[:6]}")
    for p in store.people():
        names[p.person_id] = _safe(p.name)
    taken = set(names.values())
    for o in store.organizations():
        names[o.org_id] = _safe(o.name if _safe(o.name) not in taken else f"{o.name} (organization)")
    for s in store.sources():
        names[s.key] = _safe(s.name)
    return names


def export(store: MemoryStore, vault: Path, at: datetime) -> list[Path]:
    """Write wiki/ from the store. Same store and `at` give the same bytes."""
    wiki = vault / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    names = note_names(store)
    link = lambda key: f"[[{names[key]}]]" if key in names else f"`{key[:12]}`"

    def tie_basis(a: Affiliation) -> str:  # where a tie comes from: the post that states it, or the web page
        return f"stated in {link(a.item_id)}" if a.item_id else f"from the web: {a.url}" if a.url else "from the web"
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
            where = (f" · {link(c.idea_id)}" if c.idea_id else f" · about {link(c.subject_id)}" if c.subject_id else "")
            lines.append(f"- **{c.claim.kind} · {c.durability} · {s.freshness:.2f} {s.band}{flags}** {c.claim.statement}{where}")
            for label, other, why in rel.get(c.claim.claim_id, []):
                lines.append(f"    - {label} a claim in {link(claim_item.get(other, other))}: {why}")
        mentions = sorted({e.dst for e in store.edges() if e.src == item.item_id and e.type == "MENTIONS"})
        if mentions:
            lines += ["", "## Mentions", ""]
            for head, prefix in (("People", "person:"), ("Organizations", "org:")):
                named = [link(m) for m in mentions if m.startswith(prefix)]
                if named:
                    lines.append(f"- {head}: " + ", ".join(named))
        if item.text:
            lines += ["", "## Original", "", *("> " + ln if ln else ">" for ln in item.text.splitlines())]
            translated = translate.translated_text(getattr(store, "translations", {}).get(item.item_id), item.text)
            if translated:
                lines += ["", f"## Translation ({language.name()}, machine, for reading only)", "",
                          *("> " + ln for ln in translated)]
        meta = {"type": "item", "item_id": item.item_id, "channel": src.channel, "source": src.source_name,
                "author": store.person(item.author_id).name if item.author_id else None,
                "event_time": src.event_time.isoformat(), "captured_at": src.captured_at.isoformat(),
                "url": src.url, "durability": item.durability, "duplicate_of": item.duplicate_of,
                "review_status": item.review_status, "user_durability": None}
        _write_note(wiki / "items" / f"{names[item.item_id]}.md", meta, "\n".join(lines), written)

    all_claims = {c.claim.claim_id: c for c in store.claims(hidden=True)}
    hidden_items = {i.item_id for i in store.items(hidden=True) if i.hidden}

    def where(node):
        return "a held-back item" if node.item_id in hidden_items else link(node.item_id)

    cluster_of = {i: c.cluster_id for c in store.clusters() for i in c.ideas}
    topics = sorted(store.topics(), key=lambda t: t.topic_id)

    for idea in sorted(store.ideas(), key=lambda i: i.idea_id):
        members = sorted((all_claims[cid] for cid in idea.members),
                         key=lambda c: (c.clock_start, c.claim.claim_id))
        sources = sorted({store.item(c.item_id).source_key for c in members if c.item_id not in hidden_items} - {None})
        lines = [f"# {idea.title}", "",
                 f"{idea.status} · {idea.durability} · latest {idea.valid_at:%Y-%m-%d} · "
                 f"{len(members)} claims from {len({c.item_id for c in members})} items", "", "## Claims", ""]
        for c in members:
            s = scores.get(c.claim.claim_id)
            flags = [f for f, on in (("current", c.claim.claim_id == idea.canonical_claim_id),
                                     ("superseded", c.superseded_by), ("noise", s and s.noise)) if on]
            lines.append(f"- {c.claim.statement} ({where(c)}, {store.item(c.item_id).source.event_time:%Y-%m-%d}"
                         + (f"; {', '.join(flags)}" if flags else "") + ")")
        chains = [store.chain(c.claim.claim_id) for c in members
                  if c.superseded_by and not any(e.type == "SUPERSEDES" and e.src == c.claim.claim_id
                                                 for e in store.edges())]
        if chains:
            lines += ["", "## Chain", ""]
            lines += [" → ".join(f"{n.claim.statement} ({store.item(n.item_id).source.event_time:%Y-%m-%d})"
                                 for n in chain) for chain in chains]
        conflicts = [e for e in store.edges() if e.type == "CONTRADICTS" and e.src in idea.members]
        if conflicts:
            lines += ["", "## Conflicts", ""]
            lines += [f"- {all_claims[e.src].claim.statement} contradicts {all_claims[e.dst].claim.statement}"
                      for e in conflicts]
        if sources:
            lines += ["", "## Sources", "", ", ".join(link(k) for k in sources)]
        groups = [link(cluster_of[idea.idea_id])] if idea.idea_id in cluster_of else []
        groups += [link(t.topic_id) for t in topics if idea.idea_id in t.ideas | t.attached]
        if groups:
            lines += ["", "## In", "", ", ".join(groups)]
        meta = {"type": "idea", "idea_id": idea.idea_id, "status": idea.status, "durability": idea.durability,
                "canonical_claim_id": idea.canonical_claim_id, "members": len(members)}
        _write_note(wiki / "ideas" / f"{names[idea.idea_id]}.md", meta, "\n".join(lines), written)

    def linked(gid, level):
        out = []
        for lk in store.links(level):
            if gid not in (lk.a, lk.b):
                continue
            why = [f"{len(lk.relations)} relations" if lk.relations else "",
                   f"{len(lk.shared)} shared Ideas" if lk.shared else "", f"similarity {lk.similarity:.2f}"]
            out.append(f"- {link(lk.b if lk.a == gid else lk.a)} · weight {lk.weight:.2f} · "
                       + ", ".join(w for w in why if w))
            out += [f"    - {all_claims[a].claim.statement} {kind.lower()} {all_claims[b].claim.statement}"
                    for a, kind, b in lk.relations if a in all_claims and b in all_claims]
        return out

    for c in sorted(store.clusters(), key=lambda c: c.cluster_id):
        lines = [f"# {c.title}", "", f"Cluster of {len(c.ideas)} Ideas from {len(c.sources)} sources, found by "
                 "similarity and recomputed with the graph.", "", "## Ideas", ""]
        lines += [f"- {link(i)} {store.idea(i).title}" for i in sorted(c.ideas)]
        lines += ["", "## Sources", "", ", ".join(link(k) for k in sorted(c.sources))] if c.sources else []
        holders = [link(t.topic_id) for t in topics if c.cluster_id in t.clusters]
        lines += ["", "## In topics", "", ", ".join(holders)] if holders else []
        near = linked(c.cluster_id, "cluster")
        lines += ["", "## Linked clusters", "", *near] if near else []
        _write_note(wiki / "clusters" / f"{names[c.cluster_id]}.md", {"type": "cluster", "cluster_id": c.cluster_id,
                    "ideas": len(c.ideas)}, "\n".join(lines), written)

    dossiers = {t.topic_id: _safe(f"{t.title} (dossier)") for t in topics}
    for t in topics:
        lines = [f"# {t.title}", "", f"Topic you accepted: {len(t.ideas)} Ideas, {len(t.attached)} more joined "
                 f"provisionally, {len(t.clusters)} clusters. Ranked, with what to read first: "
                 f"[[{dossiers[t.topic_id]}]].", ""]
        lines += ["## Clusters", "", *[f"- {link(c)}" for c in sorted(t.clusters)], ""] if t.clusters else []
        lines += ["## Ideas", "", *[f"- {link(i)} {store.idea(i).title}" for i in sorted(t.ideas)]]
        if t.attached:
            lines += ["", "## Joined provisionally", "",
                      *[f"- {link(i)} {store.idea(i).title}" for i in sorted(t.attached)]]
        near = linked(t.topic_id, "topic")
        lines += ["", "## Linked topics", "", *near] if near else []
        _write_note(wiki / "topics" / f"{names[t.topic_id]}.md", {"type": "topic", "topic_id": t.topic_id,
                    "ideas": len(t.ideas), "attached": len(t.attached)}, "\n".join(lines), written)

    for t in topics:  # what you know about the topic, ranked: the same dossier as the app's topic page
        d = dossier.build(store, dossier.resolve(store, t.topic_id), at)
        _write_note(wiki / "dossiers" / f"{dossiers[t.topic_id]}.md",
                    {"type": "dossier", "topic_id": t.topic_id, "ideas": d["counts"]["ideas"],
                     "core": d["counts"]["core"]}, dossier.render(d, names), written)

    for p in sorted(store.people(), key=lambda p: p.person_id):
        lines = [f"# {p.name}", ""]
        who = " at ".join(f"[{f['value']}]({f['url']})" for f in (p.role, p.affiliation) if f)
        if who:
            lines += [who, ""]
        trust = scoring.trust_rationale(p.confirmations, p.contradictions, p.up, p.down, p.trust_pinned)
        lines.append(f"Trust {trust} · enrichment {p.enrichment_status}")
        if p.domains:
            lines += ["", "## Works on", ""] + [f"- [{d['value']}]({d['url']})" for d in p.domains]
        facts = sorted((all_claims[cid] for cid in p.facts), key=lambda c: (c.clock_start, c.claim.claim_id))
        if facts:
            lines += ["", "## Profile", ""] + [f"- {c.claim.statement} ({where(c)})" for c in facts]
        ideas = sorted({c.idea_id for c in all_claims.values() if c.idea_id and c.idea_id in names
                        and (c.item_id in p.authored or c.claim.claim_id in p.facts)})
        if ideas:
            lines += ["", "## Ideas", ""] + [f"- {link(i)}" for i in ideas]
        ties = [a for a in store.affiliations() if a.person_id == p.person_id]
        if ties:
            lines += ["", "## Organizations", ""] + [f"- {AFFILIATION_WORDS[a.relation]} {link(a.org_id)} ({tie_basis(a)})"
                                                     for a in ties]
        for head, ids in (("Wrote", p.authored), ("Mentioned in", p.mentioned_in)):
            if ids:
                lines += ["", f"## {head}", ""] + [f"- {link(i)}" for i in sorted(ids, key=lambda i: names[i])]
        meta = {"type": "person", "person_id": p.person_id, "name": p.name, "handles": sorted(p.handles),
                "trust": round(store.trust(p.person_id), 3), "enrichment_status": p.enrichment_status,
                "user_trust": None}
        _write_note(wiki / "people" / f"{names[p.person_id]}.md", meta, "\n".join(lines), written)

    for o in sorted(store.organizations(), key=lambda o: o.org_id):
        lines = [f"# {o.name}", "", f"Type: {o.type.replace('_', ' ')}"]
        ties = [a for a in store.affiliations() if a.org_id == o.org_id]
        if ties:
            lines += ["", "## People", ""] + [f"- {link(a.person_id)} {AFFILIATION_WORDS[a.relation]} it ({tie_basis(a)})"
                                              for a in ties]
        facts = sorted((all_claims[cid] for cid in o.facts if cid in all_claims),
                       key=lambda c: (c.clock_start, c.claim.claim_id))
        if facts:
            lines += ["", "## Profile", ""] + [f"- {c.claim.statement} ({where(c)})" for c in facts]
        if o.mentioned_in:
            lines += ["", "## Mentioned in", ""] + [f"- {link(i)}" for i in sorted(o.mentioned_in, key=lambda i: names.get(i, i))]
        _write_note(wiki / "organizations" / f"{names[o.org_id]}.md",
                    {"type": "organization", "org_id": o.org_id, "name": o.name, "org_type": o.type},
                    "\n".join(lines), written)

    for s in sorted(store.sources(), key=lambda s: s.key):
        lines = [f"# {s.name}", "", f"Channel: {s.channel}", "", "## Items", ""]
        lines += [f"- {link(i)}" for i in sorted(s.items, key=lambda i: names[i])]
        _write_note(wiki / "sources" / f"{names[s.key]}.md", {"type": "source", "source_key": s.key,
                    "channel": s.channel}, "\n".join(lines), written)

    # Remove generated notes that no longer exist in the store, unless the user wrote in them.
    for folder in ("items", "ideas", "clusters", "topics", "dossiers", "people", "organizations", "sources"):
        for path in (wiki / folder).glob("*.md") if (wiki / folder).exists() else []:
            if path not in written and not path.read_text(encoding="utf-8").split(MARK, 1)[-1].strip():
                path.unlink()

    report = km.run(store, at)
    health = wiki / "health.md"
    health.write_text(km.render(report, store), encoding="utf-8")
    index = ["# Knowledge base", "", f"Built {at:%Y-%m-%d %H:%M %Z}. See [[health]].", ""]
    names.update({f"dossier:{tid}": name for tid, name in dossiers.items()})
    for head, keys in (("Items", [i.item_id for i in sorted(store.items(), key=lambda i: i.source.event_time, reverse=True)]),
                       ("Topics", [t.topic_id for t in topics]),
                       ("Dossiers", [f"dossier:{t.topic_id}" for t in topics]),
                       ("Clusters", [c.cluster_id for c in sorted(store.clusters(), key=lambda c: (-len(c.ideas), c.cluster_id))]),
                       ("Ideas", [i.idea_id for i in sorted(store.ideas(), key=lambda i: (-len(i.members), i.idea_id))]),
                       ("People", sorted((p.person_id for p in store.people()), key=lambda k: names[k])),
                       ("Organizations", sorted((o.org_id for o in store.organizations()), key=lambda k: names[k])),
                       ("Sources", sorted((s.key for s in store.sources()), key=lambda k: names[k]))):
        index += [f"## {head}", ""] + [f"- {link(k)}" for k in keys] + [""]
    (wiki / "index.md").write_text("\n".join(index), encoding="utf-8")
    return sorted(written | {health, wiki / "index.md"})


def log(vault: Path, at: datetime, message: str) -> None:
    with (vault / "log.md").open("a", encoding="utf-8") as f:
        f.write(f"- {at:%Y-%m-%d %H:%M} {message}\n")
