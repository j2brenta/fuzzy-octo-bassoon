"""A topic dossier: what you know about one subject, ranked, with what changed, what is contested and what to read.

Pure functions over the replayed store at one moment, like km.py: no model calls and no writes. resolve() turns a
topic, a cluster or a phrase into a scope of Ideas; build() ranks them and returns plain data for the page
(/api/dossier) and for render(), which writes the vault note wiki/dossiers/<topic>.md.

Each Idea gets an importance with its parts kept, so the page can say why it ranks where it does:
    importance = fresh · (0.35 support + 0.25 trust + 0.25 central + 0.15 firm) + user
support is how many independent sources say it, trust what the store has learned of their authors, central how
close it sits to the subject, firm whether the review firmed it up, fresh the half-life score of its newest
current claim, user your votes. Then a tier: noise when every claim is a repost or an expired ephemeral; replaced
when every claim was superseded by a newer one; fading when its freshness has fallen out of the fresh band; core
for the top of the rest; supporting for the others.
"""
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from graph import cluster as grouping
from graph import embed
from graph.memory import MemoryStore
from refresher import km, scoring

WEIGHTS = {"support": 0.35, "trust": 0.25, "central": 0.25, "firm": 0.15}
SUPPORT_FULL = 4  # independent sources at which support counts in full
VOTE, MAX_USER = 0.1, 0.3  # each vote moves importance this much, up to this far
CORE_TOP, CORE_MIN = 5, 0.6  # the top five, and any other at this importance, are core
READ_FIRST = 7
QUERY_IDEAS = 40  # a phrase gathers at most this many Ideas
TIERS = ("core", "supporting", "fading", "replaced", "noise")


@dataclass
class Scope:
    """The Ideas a dossier is about. `central` holds each Idea's closeness to the subject, 0..1."""
    kind: str  # topic | cluster | query
    key: str  # topic or cluster id, or the phrase
    title: str
    ideas: list[str]
    central: dict[str, float] = field(default_factory=dict)
    provisional: set[str] = field(default_factory=set)  # Ideas a topic took without your accepting them


def _visible_ideas(store: MemoryStore) -> set[str]:
    return {c.idea_id for c in store.claims() if c.idea_id}


def _closeness(store: MemoryStore, idea_ids: list[str], center: list[float], scale: float) -> dict[str, float]:
    """Cosine to the centre over the embedder's own cut-off, capped at 1: cosines differ between embedders."""
    if not idea_ids or not center:
        return {i: 0.0 for i in idea_ids}
    vectors = embed.vectors([store.idea(i).title for i in idea_ids])
    return {i: max(0.0, min(1.0, embed.cosine(v, center) / scale)) for i, v in zip(idea_ids, vectors)}


def resolve(store: MemoryStore, key: str | None = None, query: str | None = None) -> Scope | None:
    """A topic or cluster by id, or a phrase: a topic or cluster with that title, else the Ideas close to it by
    embedding or holding all its words (graph.query's claims_for). None when nothing matches."""
    visible = _visible_ideas(store)
    topics = {t.topic_id: t for t in store.topics()}
    clusters = {c.cluster_id: c for c in store.clusters()}
    if query is not None and key is None:
        wanted = query.strip().casefold()
        key = next((g for g, t in [*topics.items(), *clusters.items()] if t.title.casefold() == wanted), None)
    th = embed.thresholds()
    if key in topics:
        t = topics[key]
        ideas = sorted((t.ideas | t.attached) & visible)
        center = grouping.centroid(embed.vectors([store.idea(i).title for i in sorted(t.ideas & visible)])) \
            if t.ideas & visible else []
        central = {**_closeness(store, ideas, center, th.topic_attach), **{i: 1.0 for i in t.ideas}}
        return Scope("topic", key, t.title, ideas, central, set(t.attached) - t.ideas)
    if key in clusters:
        c = clusters[key]
        ideas = sorted(c.ideas & visible)
        return Scope("cluster", key, c.title, ideas, _closeness(store, ideas, c.centroid, th.cluster_link))
    if not query or not query.strip():
        return None
    candidates = sorted(visible)
    vectors = embed.vectors([query.strip()] + [store.idea(i).title for i in candidates])
    close = {i: embed.cosine(v, vectors[0]) for i, v in zip(candidates, vectors[1:])}
    # A short phrase sits closer to a cluster's centre than to any one statement in it (measured on Qwen3,
    # 28 October: "hallucination" 0.62 to its cluster, 0.59 to the nearest Idea), so a close cluster comes whole,
    # as topics are made of whole clusters.
    for c in clusters.values():
        cos = embed.cosine(c.centroid, vectors[0]) if c.centroid else 0.0
        if cos >= th.topic_link:
            close.update({i: max(close.get(i, 0.0), cos) for i in c.ideas & visible})
    picked = {i for i, cos in close.items() if cos >= th.topic_link}
    picked |= {c.idea_id for c in store.claims_for([query.strip()]) if c.idea_id in visible}
    ideas = sorted(picked, key=lambda i: (-close.get(i, 0.0), i))[:QUERY_IDEAS]
    if not ideas:
        return None
    central = {i: max(0.0, min(1.0, close.get(i, 0.0) / th.topic_attach)) for i in ideas}
    return Scope("query", query.strip(), query.strip(), sorted(ideas), central)


def _when(store: MemoryStore, item_id: str) -> datetime:
    return store.item(item_id).source.event_time


def _name(store: MemoryStore, person_id: str | None) -> str | None:
    if not person_id:
        return None
    try:
        return store.person(person_id).name
    except KeyError:
        return person_id


def _claim_row(store: MemoryStore, node) -> dict:
    item = store.item(node.item_id)
    return {"claim": node.claim.claim_id, "statement": node.claim.statement, "item": item.item_id,
            "title": item.title, "time": item.source.event_time.isoformat(), "idea": node.idea_id}


def _why(sources: int, trust: float, band: str, firm: bool, central: float, votes: int) -> str:
    parts = [f"{sources} independent source{'s' * (sources != 1)}", f"contributors trusted {trust:.2f}", band]
    parts.append("firm" if firm else "provisional")
    parts.append("central to the subject" if central >= 0.8 else "near the subject" if central >= 0.4
                 else "at the edge of the subject")
    if votes:
        parts.append(f"{votes:+d} from you")
    return " · ".join(parts)


def build(store: MemoryStore, scope: Scope, at: datetime) -> dict:
    """The dossier as plain data: Ideas ranked into tiers, what changed, what is contested, what to read first."""
    scores = km.score_claims(store, at)
    nodes = {c.claim.claim_id: c for c in store.claims(hidden=True)}
    in_scope = set(scope.ideas)
    rows = []
    for idea_id in scope.ideas:
        idea = store.idea(idea_id)
        members = sorted((cid for cid in idea.members if cid in scores), key=lambda cid: (nodes[cid].clock_start, cid))
        if not members:
            continue
        current = [cid for cid in members if nodes[cid].superseded_by is None]
        independent = [cid for cid in members if nodes[cid].duplicate_rank == 0]
        items = [store.item(nodes[cid].item_id) for cid in independent]
        sources = len({i.source_key or i.item_id for i in items})
        authors = sorted({nodes[cid].speaker_id if nodes[cid].attribution is not None else store.item(nodes[cid].item_id).author_id
                          for cid in independent if store.item(nodes[cid].item_id).review_status == "accepted"} - {None})
        trust = sum(store.trust(a) for a in authors) / len(authors) if authors else scoring.TRUST_PRIOR
        fresh = max((scores[cid].freshness for cid in current), default=0.0)
        support = min(1.0, math.log2(1 + sources) / math.log2(1 + SUPPORT_FULL))
        firm = idea.status == "firm"
        central = scope.central.get(idea_id, 0.0)
        votes = sum(nodes[cid].votes for cid in members)
        user = max(-MAX_USER, min(MAX_USER, VOTE * votes))
        parts = {"support": support, "trust": trust, "central": central, "firm": 1.0 if firm else 0.0}
        importance = fresh * sum(WEIGHTS[k] * v for k, v in parts.items()) + user
        band = scoring.band(fresh)
        replaced_by = sorted({nodes[nodes[cid].superseded_by].idea_id for cid in members
                              if nodes[cid].superseded_by in nodes and nodes[nodes[cid].superseded_by].idea_id}
                             - {idea_id})
        if all(scores[cid].noise for cid in members):
            tier = "noise"
        elif not current:
            tier = "replaced"
        elif band != "fresh":
            tier = "fading"
        else:
            tier = ""  # core or supporting, decided by rank below
        rows.append({"id": idea_id, "title": idea.title, "tier": tier, "importance": round(importance, 4),
                     "parts": {k: round(v, 3) for k, v in {**parts, "fresh": fresh, "user": user}.items()},
                     "why": _why(sources, trust, band, firm, central, votes), "status": idea.status, "band": band,
                     "freshness": round(fresh, 3), "sources": sources, "authors": authors,
                     "claims": members, "current": current,
                     "items": sorted({nodes[cid].item_id for cid in members}, key=lambda i: (_when(store, i), i)),
                     "replaced_by": [{"id": i, "title": store.idea(i).title, "in_scope": i in in_scope}
                                     for i in replaced_by],
                     "provisional": idea_id in scope.provisional})
    rows.sort(key=lambda r: (-r["importance"], r["id"]))
    ranked = [r for r in rows if not r["tier"]]
    for n, r in enumerate(ranked):
        r["tier"] = "core" if n < CORE_TOP or r["importance"] >= CORE_MIN else "supporting"
    rows.sort(key=lambda r: (TIERS.index(r["tier"]), -r["importance"], r["id"]))
    for n, r in enumerate(rows, 1):
        r["rank"] = n

    scope_claims = {cid for r in rows for cid in r["claims"]}
    changed, seen = [], set()
    superseding = {n.superseded_by for n in nodes.values() if n.superseded_by}
    for cid in sorted(scope_claims):
        if nodes[cid].superseded_by is None and cid not in superseding:
            continue
        chain = store.chain(cid)
        if len(chain) < 2:
            continue
        changed.append({"steps": [_claim_row(store, c) for c in chain]})
    # A repost and its original lead to the same correction: one change, told from the earliest original.
    changed.sort(key=lambda c: (c["steps"][0]["time"], -len(c["steps"]), c["steps"][0]["claim"]))
    changed = [c for c in changed if c["steps"][-1]["claim"] not in seen and not seen.add(c["steps"][-1]["claim"])]
    changed.sort(key=lambda c: (c["steps"][-1]["time"], c["steps"][-1]["claim"]), reverse=True)

    contested = []
    for e in store.edges():
        if e.type == "CONTRADICTS" and (e.src in scope_claims or e.dst in scope_claims) \
                and e.src in scores and e.dst in scores:
            contested.append({"a": _claim_row(store, nodes[e.src]), "b": _claim_row(store, nodes[e.dst]),
                              "rationale": e.rationale})
    contested.sort(key=lambda c: (c["a"]["time"], c["a"]["claim"]), reverse=True)
    # A correction or a conflict joins the Idea it is about (graph/memory.py), so an Idea that changed its mind
    # shows it on its own row: what it said before, and whether its sources disagree.
    for r in rows:
        r["was"] = [c["steps"][:-1] for c in changed if c["steps"][-1]["idea"] == r["id"]]
        r["contested"] = sum(r["id"] in (p["a"]["idea"], p["b"]["idea"]) for p in contested)

    reading, also = _reading_order(store, rows, changed)
    ranked = store.experts(at, scope.key, idea_ids=scope.ideas, subject_title=scope.title)
    all_people = {p.person_id: p for p in store.people()}
    scope_items = {nodes[c].item_id for c in scope_claims}
    about = {pid: {cid for cid in p.facts if cid in nodes and nodes[cid].item_id in scope_items
                   and store.item(nodes[cid].item_id).review_status == "accepted"
                   and store.item(nodes[cid].item_id).source.event_time <= at}
             for pid, p in all_people.items()}
    ranked = [r for r in ranked if set(r["claim_ids"]) & scope_claims or about[r["person_id"]]]
    nearby = _nearby(store, scope)
    nearby["people"] = [{"id": p.person_id, "name": p.name, "trust": round(store.trust(p.person_id), 2),
                         "importance": r["importance"], "score": r,
                         "stated": len(set(r["claim_ids"]) & scope_claims),
                         "wrote": len({nodes[c].item_id for c in scope_claims} & p.authored),
                         "about": len(about[p.person_id])}
                        for r in ranked[:8] for p in [all_people[r["person_id"]]]]
    counts = {t: sum(r["tier"] == t for r in rows) for t in TIERS}
    item_ids = sorted({i for r in rows for i in r["items"]} | {s["item"] for c in changed for s in c["steps"]})
    sources = {i: {"title": (item := store.item(i)).title, "source": item.source.source_name,
                   "author": _name(store, item.author_id),
                   "time": item.source.event_time.isoformat(), "url": item.source.url, "repost_of": item.duplicate_of,
                   "hidden": item.hidden} for i in item_ids}
    return {"scope": {"kind": scope.kind, "id": scope.key, "title": scope.title}, "at": at.isoformat(),
            "counts": {**counts, "ideas": len(rows), "items": len({i for r in rows for i in r["items"]})},
            "ideas": rows, "changed": changed, "contested": contested, "reading": reading, "also": also,
            "nearby": nearby, "sources": sources}


def _reading_order(store: MemoryStore, rows: list[dict], changed: list[dict]) -> tuple[list[dict], list[dict]]:
    """A greedy cover: each pick is the source that adds the most importance not yet covered, weighted by its
    author's trust; a repost counts as its original; the newest word in each chain of changes is always read."""
    weight = {r["id"]: max(r["importance"], 0.0) for r in rows if r["tier"] in ("core", "supporting", "fading")}
    rank = {r["id"]: r["rank"] for r in rows}
    title = {r["id"]: r["title"] for r in rows}

    def original(item_id: str) -> str:
        item = store.item(item_id)
        return item.duplicate_of if item.duplicate_of and not store.item(item.duplicate_of).hidden else item_id

    covers: dict[str, set[str]] = defaultdict(set)
    reposts: dict[str, int] = defaultdict(int)
    for r in rows:
        for item_id in r["items"]:
            first = original(item_id)
            if store.item(first).hidden:
                continue
            reposts[first] += first != item_id
            if r["id"] in weight:
                covers[first].add(r["id"])
            else:
                covers.setdefault(first, set())
    newest = {original(c["steps"][-1]["item"]): c["steps"][-1]["idea"] for c in changed}
    newest = {i: idea for i, idea in newest.items() if i in covers}

    def trust(item_id: str) -> float:
        author = store.item(item_id).author_id
        return store.trust(author) if author else scoring.TRUST_PRIOR

    picks, covered = [], set()
    while len(picks) < READ_FIRST:
        gains = {i: sum(weight[x] for x in covers[i] - covered) * trust(i) for i in covers if i not in picks}
        best = max(gains, key=lambda i: (gains[i], _when(store, i), i), default=None)
        if best is None or gains[best] <= 0:
            break
        picks.append(best)
        covered |= covers[best]
    for item_id in sorted(newest, key=lambda i: _when(store, i), reverse=True):
        if item_id not in picks:
            picks = (picks if len(picks) < READ_FIRST else picks[:-1]) + [item_id]

    def row(item_id: str, n: int | None = None) -> dict:
        item = store.item(item_id)
        ideas = sorted(covers[item_id], key=lambda i: rank[i])
        reasons = []
        if ideas:
            core = [i for i in ideas if any(r["id"] == i and r["tier"] == "core" for r in rows)]
            reasons.append(f"covers {len(core)} core Idea{'s' * (len(core) != 1)}" if core
                           else f"covers {len(ideas)} Idea{'s' * (len(ideas) != 1)}")
            reasons[-1] += f", including #{rank[ideas[0]]} {title[ideas[0]].rstrip('.')}"
        if item_id in newest:
            reasons.append("the newest word on what changed")
        if reposts[item_id]:
            reasons.append(f"the original; reposted {reposts[item_id]} time{'s' * (reposts[item_id] != 1)}")
        if item.author_id:
            reasons.append(f"author trusted {trust(item_id):.2f}")
        return {"rank": n, "item": item_id, "title": item.title, "source": item.source.source_name,
                "author": _name(store, item.author_id), "time": item.source.event_time.isoformat(), "url": item.source.url,
                "ideas": ideas, "reason": "; ".join(reasons)}

    reading = [row(i, n) for n, i in enumerate(picks, 1)]
    also = [row(i) for i in sorted(set(covers) - set(picks), key=lambda i: (_when(store, i), i))]
    return reading, also


def _nearby(store: MemoryStore, scope: Scope) -> dict:
    topics = {t.topic_id: t for t in store.topics()}
    clusters = {c.cluster_id: c for c in store.clusters()}
    level = {"topic": "topic", "cluster": "cluster"}.get(scope.kind)
    linked = []
    for link in store.links(level) if level else []:
        if scope.key in (link.a, link.b):
            other = link.b if link.a == scope.key else link.a
            group = topics.get(other) or clusters.get(other)
            if group is not None:
                linked.append({"id": other, "title": group.title, "weight": round(link.weight, 3),
                               "evidence": bool(link.relations or link.shared)})
    ideas = set(scope.ideas)
    inside = [{"id": c.cluster_id, "title": c.title, "ideas": len(c.ideas & ideas)}
              for c in clusters.values() if c.ideas & ideas and c.cluster_id != scope.key]
    holding = [{"id": t.topic_id, "title": t.title, "ideas": len((t.ideas | t.attached) & ideas)}
               for t in topics.values() if (t.ideas | t.attached) & ideas and t.topic_id != scope.key]
    order = lambda rows: sorted(rows, key=lambda r: (-r.get("ideas", r.get("weight", 0)), r["id"]))
    return {"linked": sorted(linked, key=lambda r: (-r["weight"], r["id"])),
            "clusters": order(inside), "topics": order(holding)}


def render(d: dict, names: dict[str, str]) -> str:
    """The dossier as a vault note, with wikilinks to the Idea, item and people notes."""
    link = lambda key: f"[[{names[key]}]]" if key in names else f"`{key[:12]}`"
    day = lambda iso: iso[:10]
    c = d["counts"]
    lines = [f"# {d['scope']['title']}: what you know", "",
             f"As of {day(d['at'])} · {c['ideas']} Ideas from {c['items']} sources · {c['core']} core, "
             f"{c['supporting']} supporting, {c['fading']} fading, {c['replaced']} replaced, {c['noise']} noise.", ""]
    shown = [r for r in d["ideas"] if r["tier"] in ("core", "supporting", "fading")]
    if shown:
        lines += ["## The current view", ""]
        for r in shown:
            lines.append(f"{r['rank']}. **{r['title']}** {link(r['id'])} · {r['tier']} · {r['why']}")
    if d["changed"]:
        lines += ["", "## What changed", ""]
        for chain in d["changed"]:
            steps = [f"{s['statement']} ({link(s['item'])}, {day(s['time'])})" for s in chain["steps"]]
            lines.append("- was " + " → now ".join(steps))
    if d["contested"]:
        lines += ["", "## Contested", ""]
        for pair in d["contested"]:
            a, b = pair["a"], pair["b"]
            lines.append(f"- {a['statement']} ({link(a['item'])}) ⟷ {b['statement']} ({link(b['item'])})"
                         + (f": {pair['rationale']}" if pair["rationale"] else ""))
    if d["reading"]:
        lines += ["", "## Read in order", ""]
        lines += [f"{r['rank']}. {link(r['item'])}: {r['reason']}" for r in d["reading"]]
        if d["also"]:
            lines += ["", "Also: " + ", ".join(link(r["item"]) for r in d["also"])]
    near = d["nearby"]
    parts = [("Linked", near["linked"]), ("Clusters", near["clusters"]), ("In topics", near["topics"])]
    if any(rows for _, rows in parts) or near["people"]:
        lines += ["", "## Nearby", ""]
        lines += [f"- {label}: " + ", ".join(link(r["id"]) for r in rows) for label, rows in parts if rows]
        if near["people"]:
            lines.append("- People: " + ", ".join(f"{link(p['id'])} (importance {p['importance']:.2f})" if p.get("importance") is not None else f"{link(p['id'])} (importance unscored)" for p in near["people"]))
    hidden = [r for r in d["ideas"] if r["tier"] in ("replaced", "noise")]
    if hidden:
        lines += ["", "## Replaced and noise", ""]
        for r in hidden:
            by = ", ".join(link(x["id"]) for x in r["replaced_by"])
            lines.append(f"- {r['title']} {link(r['id'])} · {r['tier']}" + (f", replaced by {by}" if by else ""))
    return "\n".join(lines)


def catalog(store: MemoryStore, at: datetime) -> list[dict]:
    """Every topic, then every cluster, with what its dossier holds: the index a reader picks a subject from."""
    rows = []
    groups = [("topic", t.topic_id, t.title) for t in sorted(store.topics(), key=lambda t: t.topic_id)]
    groups += [("cluster", c.cluster_id, c.title)
               for c in sorted(store.clusters(), key=lambda c: (-len(c.ideas), c.cluster_id))]
    topics_of = defaultdict(list)
    for t in store.topics():
        for cid in t.clusters:
            topics_of[cid].append(t.title)
    for kind, key, title in groups:
        d = build(store, resolve(store, key), at)
        if not d["ideas"]:
            continue
        top = next((r for r in d["ideas"] if r["tier"] == "core"), d["ideas"][0])
        latest = max(store.item(i).source.event_time for r in d["ideas"] for i in r["items"])
        rows.append({"kind": kind, "id": key, "title": title, "counts": d["counts"], "top": top["title"],
                     "changed": len(d["changed"]), "contested": len(d["contested"]), "latest": latest.isoformat(),
                     "in_topics": sorted(topics_of.get(key, []))})
    return rows
