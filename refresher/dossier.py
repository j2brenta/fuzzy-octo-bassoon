"""A topic dossier: what you know about one subject, ranked, with what changed, what is contested and what to read.

Pure functions over the replayed store at one moment, like km.py: no model calls and no writes. resolve() turns a
topic, a cluster or a phrase into a scope of Ideas; build() ranks them and returns plain data for the page
(/api/dossier) and for render(), which writes the vault note wiki/dossiers/<topic>.md.

The page is a reading map: documents first, Ideas as what links them. Every visible source is in it, in one of three
rings: core holds a live Idea of the subject; adjacent does not, but is close to the subject by embedding, related to
a core source, or sits in a cluster linked to the subject's; periphery is the rest, noise included, to see and skip.
Within the rings, in that order, sources follow marginal coverage: each next one adds the most importance the ones
above it did not (the greedy cover of the reading order, run to the end), so the first is where to start, a source
adding nothing new sits low, and the first one to three are key. Each sits in the thread (cluster) that holds most of
its importance, carries its hook and gist (refresher/summarize.py, or its lead sentences), and badges for the sources
it updates, is outdated by or disagrees with. people ranks who is involved in the core, by the importance of what
they wrote and what is said of them, with their organizations and the ties between them, for the people map.

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

from engine.extract import source_passages
from graph import cluster as grouping
from graph import embed
from graph.memory import MemoryStore
from graph.store import AFFILIATION_WORDS
from refresher import km, scoring, summarize, translate

WEIGHTS = {"support": 0.35, "trust": 0.25, "central": 0.25, "firm": 0.15}
SUPPORT_FULL = 4  # independent sources at which support counts in full
VOTE, MAX_USER = 0.1, 0.3  # each vote moves importance this much, up to this far
CORE_TOP, CORE_MIN = 5, 0.6  # the top five, and any other at this importance, are core
READ_FIRST = 7
KEY_MAX, KEY_SHARE = 3, 0.5  # key documents: the first pick, and the next ones adding at least half as much
RINGS = ("core", "adjacent", "periphery")
ADJACENT_MIN = 0.6  # relevance (0..1) at which a source outside the subject is adjacent
NEAR_SPAN = 0.2  # relevance rises from 0 to 1 over this much cosine, ending at the embedder's topic_link
LINKED = 0.8  # relevance of a source whose claims relate to a core source's, or whose cluster links to the subject's
OFF_SUBJECT = 0.25  # an Idea outside the subject weighs this much of its closeness, for the order of the outer rings
KEY_BONUS = 1.5  # writing a key document counts this much more for a person
WORDS_PER_MINUTE = 220
TOP_PEOPLE = 5
MAIN_TIES = ("leads", "founded", "works_at", "member_of", "studied_at", "affiliated", "published_at")
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
    center: list[float] = field(default_factory=list)  # the subject as a vector: rings score every source against it


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
        return Scope("topic", key, t.title, ideas, central, set(t.attached) - t.ideas, center)
    if key in clusters:
        c = clusters[key]
        ideas = sorted(c.ideas & visible)
        return Scope("cluster", key, c.title, ideas, _closeness(store, ideas, c.centroid, th.cluster_link), set(),
                     list(c.centroid or []))
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
    return Scope("query", query.strip(), query.strip(), sorted(ideas), central, set(), vectors[0])


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
    parts = [f"{sources} independent source{'s' * (sources != 1)}", f"authors trusted {trust:.2f}", band]
    parts.append("firm" if firm else "provisional")
    parts.append("central to the subject" if central >= 0.8 else "near the subject" if central >= 0.4
                 else "at the edge of the subject")
    if votes:
        parts.append(f"{votes:+d} from you")
    return " · ".join(parts)


def build(store: MemoryStore, scope: Scope, at: datetime, documents: bool = True) -> dict:
    """The dossier as plain data: Ideas ranked into tiers, what changed, what is contested, what to read first, and
    (unless `documents` is False, as for the catalog) every source in its ring and the people."""
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
        authors = sorted({i.author_id for i in items if i.author_id})
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
    docs, threads = _documents(store, scope, rows, changed, contested, scores) if documents else ([], [])
    nearby = _nearby(store, scope)
    counts = {t: sum(r["tier"] == t for r in rows) for t in TIERS}
    item_ids = sorted({i for r in rows for i in r["items"]} | {s["item"] for c in changed for s in c["steps"]})
    sources = {i: {"title": (item := store.item(i)).title, "source": item.source.source_name,
                   "author": _name(store, item.author_id),
                   "time": item.source.event_time.isoformat(), "url": item.source.url, "repost_of": item.duplicate_of,
                   "hidden": item.hidden} for i in item_ids}
    return {"scope": {"kind": scope.kind, "id": scope.key, "title": scope.title}, "at": at.isoformat(),
            "counts": {**counts, "ideas": len(rows), "items": len({i for r in rows for i in r["items"]})},
            "ideas": rows, "changed": changed, "contested": contested, "reading": reading, "also": also,
            "documents": docs, "threads": threads,
            "rings": {ring: sum(d["ring"] == ring for d in docs) for ring in RINGS},
            "people": _people(store, [d for d in docs if d["ring"] == "core"], rows, scope_claims) if documents
            else {"people": [], "orgs": [], "edges": []},
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


def _lines(store: MemoryStore, item) -> list[str]:
    """The item's text as the reader sees it: the translation where there is one, else the original."""
    record = getattr(store, "translations", {}).get(item.item_id)
    return translate.translated_text(record, item.text) or item.text.splitlines()


def _original(store: MemoryStore, item_id: str) -> str:
    item = store.item(item_id)
    return item.duplicate_of if item.duplicate_of and not store.item(item.duplicate_of).hidden else item_id


def _relevance(cos: float) -> float:
    """A cosine to the subject as 0..1: 0 at what unrelated text scores, 1 at the embedder's topic_link."""
    hi = embed.thresholds().topic_link
    return max(0.0, min(1.0, (cos - (hi - NEAR_SPAN)) / NEAR_SPAN))


def _documents(store: MemoryStore, scope: Scope, rows: list[dict], changed: list[dict], contested: list[dict],
               scores: dict) -> tuple[list[dict], list[dict]]:
    """Every visible source in its ring, ordered by marginal coverage ring by ring (a repost counts as its original
    and is folded into it), with its thread, summary and badges; and the threads, best first."""
    weight = {r["id"]: max(r["importance"], 0.0) for r in rows if r["tier"] in ("core", "supporting", "fading")}
    rank = {r["id"]: r["rank"] for r in rows}
    in_scope = {r["id"] for r in rows}
    claims = [c for c in store.claims() if c.claim.claim_id in scores]
    ideas: dict[str, set[str]] = defaultdict(set)
    reposts: dict[str, list[str]] = defaultdict(list)
    claims_of: dict[str, set[str]] = defaultdict(set)
    for item in store.items():
        first = _original(store, item.item_id)
        ideas.setdefault(first, set())
        if first != item.item_id and item.item_id not in reposts[first]:
            reposts[first].append(item.item_id)
    for c in claims:
        first = _original(store, c.item_id)
        claims_of[first].add(c.claim.claim_id)
        if c.idea_id:
            ideas[first].add(c.idea_id)

    # How close each Idea outside the subject is to it, for the outer rings' relevance and order.
    outside = sorted({i for held in ideas.values() for i in held} - in_scope)
    close = {}
    if scope.center and outside:
        vectors = embed.vectors([store.idea(i).title for i in outside])
        close = {i: _relevance(embed.cosine(v, scope.center)) for i, v in zip(outside, vectors)}
    fresh = {}
    for c in claims:
        if c.idea_id and c.idea_id not in in_scope and c.superseded_by is None:
            fresh[c.idea_id] = max(fresh.get(c.idea_id, 0.0), scores[c.claim.claim_id].freshness)
    off = {i: OFF_SUBJECT * fresh.get(i, 0.0) * max(close.get(i, 0.0), 0.2) for i in outside}

    core = {i for i, held in ideas.items() if held & set(weight)}
    core_claims = {cid for i in core for cid in claims_of[i]}
    item_of = {c.claim.claim_id: _original(store, c.item_id) for c in claims}
    related = {item_of[b] for e in store.edges() if e.type in ("SUPPORTS", "CONTRADICTS", "ELABORATES", "SUPERSEDES")
               for a, b in ((e.src, e.dst), (e.dst, e.src)) if a in core_claims and b in item_of}
    clusters = {c.cluster_id: c for c in store.clusters()}
    cluster_of = {i: cid for cid, c in sorted(clusters.items()) for i in c.ideas}
    scope_clusters = {cluster_of[i] for i in weight if i in cluster_of}
    # A cluster linked to one of the subject's (relations, shared Ideas, or centroids past topic_neighbour) is close
    # enough by that alone: the link already passed the embedder's own cut-off.
    linked = {b: LINKED for link in store.links("cluster") for a, b in ((link.a, link.b), (link.b, link.a))
              if a in scope_clusters and b not in scope_clusters}
    relevance, ring = {}, {}
    for i, held in ideas.items():
        if i in core:
            relevance[i] = max(scope.central.get(x, 0.0) for x in held & set(weight))
            ring[i] = "core"
            continue
        near = max((close.get(x, 0.0) for x in held), default=0.0)
        via = max((linked.get(cluster_of.get(x), 0.0) for x in held), default=0.0)
        relevance[i] = round(max(near, via, LINKED if i in related else 0.0), 3)
        ring[i] = "adjacent" if relevance[i] >= ADJACENT_MIN else "periphery"

    def trust(item_id: str) -> float:
        author = store.item(item_id).author_id
        return store.trust(author) if author else scoring.TRUST_PRIOR

    def worth(idea_id: str) -> float:
        return weight.get(idea_id, off.get(idea_id, 0.0))

    order, gains, fresh_ideas, covered = [], {}, {}, set()
    for band in RINGS:
        left = {i for i in ideas if ring[i] == band}
        while left:
            gain = {i: sum(worth(x) for x in ideas[i] - covered) * trust(i) for i in left}
            best = max(gain, key=lambda i: (gain[i], relevance[i], _when(store, i), i))
            if gain[best] <= 0:  # the rest add nothing new: the most relevant, then the newest, first
                for i in sorted(left, key=lambda i: (relevance[i], _when(store, i), i), reverse=True):
                    order.append(i)
                    gains[i], fresh_ideas[i] = 0.0, 0
                break
            order.append(best)
            gains[best], fresh_ideas[best] = gain[best], len(ideas[best] - covered)
            covered |= ideas[best]
            left.discard(best)
    key = set()
    for n, i in enumerate(order[:KEY_MAX]):
        if ring[i] == "core" and gains[i] > 0 and (n == 0 or gains[i] >= KEY_SHARE * gains[order[0]]):
            key.add(i)
        else:
            break

    thread_of = {}
    for item_id in order:
        held: dict[str, float] = defaultdict(float)
        for i in ideas[item_id]:
            if i in cluster_of and not (scope.kind == "cluster" and i in weight):
                held[cluster_of[i]] += worth(i) or 0.001
        if scope.kind == "cluster" and ideas[item_id] & set(weight):
            thread_of[item_id] = scope.key
        else:
            thread_of[item_id] = max(held, key=lambda c: (held[c], c)) if held else "other"

    badges: dict[str, list[dict]] = defaultdict(list)

    def badge(item_id: str, kind: str, other: str, idea: str | None):
        a, b = _original(store, item_id), _original(store, other)
        if a != b and a in ideas and not any(x["kind"] == kind and x["item"] == b for x in badges[a]):
            badges[a].append({"kind": kind, "item": b, "idea": idea, "in_scope": b in ideas})
    for c in changed:
        for old, new in zip(c["steps"], c["steps"][1:]):
            badge(new["item"], "updates", old["item"], new["idea"])
            badge(old["item"], "outdated_by", new["item"], new["idea"])
    for pair in contested:
        badge(pair["a"]["item"], "disagrees", pair["b"]["item"], pair["a"]["idea"])
        badge(pair["b"]["item"], "disagrees", pair["a"]["item"], pair["b"]["idea"])

    summaries = getattr(store, "summaries", {})
    total = sum(weight.values()) or 1.0
    deepest = {}
    for item_id in order:
        t = thread_of[item_id]
        if ring[item_id] == "core" and (t not in deepest or sum(map(worth, ideas[item_id])) >
                                         sum(map(worth, ideas[deepest[t]]))):
            deepest[t] = item_id
    documents = []
    for n, item_id in enumerate(order, 1):
        item = store.item(item_id)
        lines = _lines(store, item)
        if item_id in summaries:
            hook, gist, kind = summaries[item_id]["hook"], summaries[item_id].get("gist", ""), "model"
        else:
            (hook, gist), kind = summarize.lead(lines, item.title), "lead"
        mine = sorted(ideas[item_id] & in_scope, key=lambda i: rank[i])
        others = sorted(ideas[item_id] - in_scope, key=lambda i: (-off.get(i, 0.0), i))
        documents.append({
            "id": item_id, "rank": n, "ring": ring[item_id], "relevance": round(relevance[item_id], 3),
            "key": item_id in key, "deepest": deepest.get(thread_of[item_id]) == item_id,
            "title": item.title, "source": item.source.source_name,
            "author": _name(store, item.author_id), "author_id": item.author_id,
            "time": item.source.event_time.isoformat(), "captured": item.source.captured_at.isoformat(),
            "url": item.source.url, "minutes": max(1, round(sum(len(ln.split()) for ln in lines) / WORDS_PER_MINUTE)),
            "hook": hook, "gist": gist, "summary": kind, "ideas": mine,
            "other_ideas": [{"id": i, "title": store.idea(i).title} for i in others],
            "weight": round(sum(weight.get(i, 0.0) for i in mine), 4), "gain": round(gains[item_id], 4),
            "share": round(sum(weight.get(i, 0.0) for i in mine) / total, 3), "new_ideas": fresh_ideas[item_id],
            "noise": not mine and not others or (bool(mine) and not any(i in weight for i in mine) and not others),
            "thread": thread_of[item_id], "reposts": reposts.get(item_id, []), "badges": badges.get(item_id, [])})
    threads = []
    for tid in dict.fromkeys(thread_of[i] for i in order):
        docs = [d for d in documents if d["thread"] == tid]
        title = clusters[tid].title if tid in clusters else scope.title if tid == scope.key else "Other sources"
        threads.append({"id": tid, "title": title, "documents": [d["id"] for d in docs],
                        "ring": min((d["ring"] for d in docs), key=RINGS.index),
                        "weight": round(sum(d["weight"] for d in docs), 4)})
    return documents, threads


def _people(store: MemoryStore, documents: list[dict], rows: list[dict], scope_claims: set[str]) -> dict:
    """Who is involved in the documents, most first: the importance of what they wrote (a key document counts
    more) and of what is said about them, by how far you trust them. With their organizations and the ties between
    them: same document, writes about, agrees and disagrees."""
    docs = {d["id"]: d for d in documents}
    claims = {c.claim.claim_id: c for c in store.claims()}
    weight = {r["id"]: max(r["importance"], 0.0) for r in rows if r["tier"] in ("core", "supporting", "fading")}
    in_doc: dict[str, set[str]] = defaultdict(set)  # item -> the people it involves
    rows_out = {}
    for p in store.people():
        wrote = sorted(i for i in p.authored if i in docs)
        named = sorted(i for i in p.mentioned_in if i in docs and i not in wrote)
        facts = [claims[c] for c in sorted(p.facts) if c in claims and _original(store, claims[c].item_id) in docs]
        about = sorted({_original(store, c.item_id) for c in facts})
        if not (wrote or named or about):
            continue
        for i in {*wrote, *named, *about}:
            in_doc[i].add(p.person_id)
        trust = store.trust(p.person_id)
        involvement = sum(docs[i]["weight"] * (KEY_BONUS if docs[i]["key"] else 1.0) for i in wrote) \
            + 0.5 * sum(docs[i]["weight"] for i in about) / max(1, len(about)) * min(len(facts), 3) \
            + 0.05 * len(named)
        key_doc = next((docs[i] for i in wrote if docs[i]["key"]), None)
        reason = f"wrote ★ {key_doc['title']}" if key_doc else \
            f"wrote {len(wrote)} source{'s' * (len(wrote) != 1)}" if wrote else \
            f"{len(facts)} claim{'s' * (len(facts) != 1)} about them" if facts else \
            f"named in {len(named)} source{'s' * (len(named) != 1)}"
        threads: dict[str, int] = defaultdict(int)
        for i in {*wrote, *about}:
            threads[docs[i]["thread"]] += 1
        found = lambda f: {"value": f["value"], "url": f.get("url")} if isinstance(f, dict) and f.get("value") else None
        rows_out[p.person_id] = {
            "id": p.person_id, "name": p.name, "trust": round(trust, 2), "score": round(involvement * (0.5 + trust), 4),
            "reason": reason, "role": found(p.role), "affiliation": found(p.affiliation),
            "domains": [d for d in map(found, p.domains) if d], "wrote": wrote, "about": about, "named": named,
            "threads": dict(sorted(threads.items())), "ties": [], "org": None}
    orgs = {}
    for a in store.affiliations():
        if a.person_id not in rows_out:
            continue
        person = rows_out[a.person_id]
        if any(t["org"] == a.org_id and t["relation"] == a.relation for t in person["ties"]):
            continue
        person["ties"].append({"org": a.org_id, "relation": a.relation,
                               "label": AFFILIATION_WORDS.get(a.relation, a.relation), "item": a.item_id, "url": a.url})
        org = store.organization(a.org_id)
        orgs.setdefault(a.org_id, {"id": a.org_id, "name": org.name, "type": org.type, "members": [], "main": []})
        if a.person_id not in orgs[a.org_id]["members"]:
            orgs[a.org_id]["members"].append(a.person_id)
    for person in rows_out.values():
        stated = lambda t: (t["item"] not in docs, MAIN_TIES.index(t["relation"]) if t["relation"] in MAIN_TIES
                            else len(MAIN_TIES), t["org"])
        if person["ties"]:
            person["org"] = min(person["ties"], key=stated)["org"]
            orgs[person["org"]]["main"].append(person["id"])
    ranked = sorted(rows_out.values(), key=lambda r: (-r["score"], r["name"], r["id"]))
    for n, r in enumerate(ranked):
        r["top"] = n < TOP_PEOPLE

    edges: dict[tuple, dict] = {}

    def edge(a: str, b: str, kind: str, item_id: str, directed: bool = False):
        if a == b or a not in rows_out or b not in rows_out:
            return
        ends = (a, b) if directed else tuple(sorted((a, b)))
        row = edges.setdefault((*ends, kind), {"a": ends[0], "b": ends[1], "kind": kind, "documents": []})
        if item_id not in row["documents"]:
            row["documents"].append(item_id)
    for item_id, involved in sorted(in_doc.items()):
        for a in sorted(involved):
            for b in sorted(involved):
                if a < b:
                    edge(a, b, "same_document", item_id)
    for r in rows_out.values():
        for c in (claims[c] for c in sorted(store.person(r["id"]).facts) if c in claims):
            author = store.item(c.item_id).author_id
            if author:
                edge(author, r["id"], "writes_about", _original(store, c.item_id), directed=True)
    for e in store.edges():
        if e.type in ("CONTRADICTS", "SUPPORTS") and e.src in claims and e.dst in claims \
                and (e.src in scope_claims or e.dst in scope_claims):
            a, b = store.item(claims[e.src].item_id).author_id, store.item(claims[e.dst].item_id).author_id
            if a and b:
                kind = "disagrees" if e.type == "CONTRADICTS" else "agrees"
                edge(a, b, kind, _original(store, claims[e.src].item_id))
    return {"people": ranked, "orgs": sorted(orgs.values(), key=lambda o: (-len(o["members"]), o["name"], o["id"])),
            "edges": sorted(edges.values(), key=lambda e: (e["kind"], e["a"], e["b"]))}


def document(store: MemoryStore, item_id: str) -> dict | None:
    """One source for the reader: its text, its translation line by line, and where each claim sits, by Idea."""
    try:
        item = store.item(item_id)
    except KeyError:
        return None
    if item.hidden:
        return None
    record = getattr(store, "translations", {}).get(item_id)
    done = (record or {}).get("passages") or {}
    translation = [{"start": p["start"], "end": p["end"], "text": done[str(p["id"])]}
                   for p in source_passages(item.text) if str(p["id"]) in done]
    spans = []
    for c in sorted(store.claims(), key=lambda c: c.claim.claim_id):
        if c.item_id != item_id:
            continue
        for span in c.claim.spans:
            start, end = max(0, span.start_char), min(len(item.text), span.end_char)
            if start < end:
                spans.append({"start": start, "end": end, "claim": c.claim.claim_id, "idea": c.idea_id,
                              "subject": c.subject_id, "statement": c.claim.statement})
    return {"id": item_id, "title": item.title, "source": item.source.source_name,
            "author": _name(store, item.author_id), "author_id": item.author_id, "time": item.source.event_time.isoformat(), "url": item.source.url,
            "text": item.text, "translation": translation, "spans": sorted(spans, key=lambda s: (s["start"], s["end"]))}


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
    docs = {x["id"]: x for x in d["documents"]}
    key = [x for x in d["documents"] if x["key"]]
    if key:
        lines += ["## Start here", ""]
        lines += [f"{n}. {link(x['id'])}: {x['hook']}" for n, x in enumerate(key, 1)]
        lines.append("")
    threads = [(t, [docs[i] for i in t["documents"] if docs[i]["ring"] == "core" and not docs[i]["noise"]])
               for t in d["threads"]]
    if any(rows for _, rows in threads):
        lines += ["## Threads", ""]
        for t, rows in threads:
            if rows:
                lines += [f"### {t['title']}", ""]
                lines += [f"- {'★ ' if x['key'] else ''}{link(x['id'])}: {x['hook']}" for x in rows]
                lines.append("")
    titles = {t["id"]: t["title"] for t in d["threads"]}
    for ring, intro in (("adjacent", "Close to the subject, from another cluster"),
                        ("periphery", "Weak overlap: perspectives for after the main part")):
        rows = [x for x in d["documents"] if x["ring"] == ring]
        if rows:
            lines += [f"## {ring.capitalize()}", "", f"{intro}.", ""]
            lines += [f"- {link(x['id'])}: {x['hook']} ({titles.get(x['thread'], 'other')}"
                      f"{', noise' if x['noise'] else ''})" for x in rows]
            lines.append("")
    people = [p for p in d["people"]["people"] if p["top"]]
    if people:
        lines += ["People: " + ", ".join(f"{link(p['id'])} ({p['reason']})" for p in people), ""]
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
    near = d["nearby"]
    parts = [("Linked", near["linked"]), ("Clusters", near["clusters"]), ("In topics", near["topics"])]
    if any(rows for _, rows in parts):
        lines += ["", "## Nearby", ""]
        lines += [f"- {label}: " + ", ".join(link(r["id"]) for r in rows) for label, rows in parts if rows]
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
        d = build(store, resolve(store, key), at, documents=False)
        if not d["ideas"]:
            continue
        top = next((r for r in d["ideas"] if r["tier"] == "core"), d["ideas"][0])
        latest = max(store.item(i).source.event_time for r in d["ideas"] for i in r["items"])
        rows.append({"kind": kind, "id": key, "title": title, "counts": d["counts"], "top": top["title"],
                     "changed": len(d["changed"]), "contested": len(d["contested"]), "latest": latest.isoformat(),
                     "in_topics": sorted(topics_of.get(key, []))})
    return rows
