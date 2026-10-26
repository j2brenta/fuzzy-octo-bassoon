"""Knowledge management pass: score everything at one moment and report what needs attention.

No model calls and no writes to the store. Freshness is computed at read time, so running this with a
future --at shows how the knowledge base will look then.
"""
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from graph.store import ClaimNode, GraphStore
from refresher import scoring

BANDS = ("fresh", "fading", "stale")
DURABILITIES = tuple(scoring.HALF_LIFE_DAYS)
SOON = timedelta(days=14)


@dataclass(frozen=True)
class ClaimScore:
    node: ClaimNode
    freshness: float
    band: str
    noise: bool


@dataclass
class KnowledgeReport:
    at: datetime
    items: int = 0
    claims: int = 0
    by_durability: dict[str, Counter] = field(default_factory=dict)
    noise: int = 0
    confirmations: int = 0
    duplicates: list[tuple[str, str]] = field(default_factory=list)  # (later item, first item)
    supersessions: list[tuple[str, str]] = field(default_factory=list)
    contradictions: list[tuple[str, str]] = field(default_factory=list)
    abstentions: list = field(default_factory=list)  # Verdicts where the relator would not guess
    held_back: dict[str, str] = field(default_factory=dict)
    unreviewed: dict[str, str] = field(default_factory=dict)  # placed without passing the quality gate
    ideas: Counter = field(default_factory=Counter)  # status -> count
    conflicted_ideas: list[str] = field(default_factory=list)  # titles of Ideas holding a CONTRADICTS edge
    topics: int = 0
    attached: int = 0  # Ideas a topic took provisionally since it was accepted
    proposals: Counter = field(default_factory=Counter)  # status -> count
    people: int = 0
    people_pending: int = 0
    fading_soon: list[ClaimScore] = field(default_factory=list)  # fresh now, fading within SOON
    refresh_due: list[tuple[str, int]] = field(default_factory=list)  # (item id, durable claims gone fading)


def score_claims(store: GraphStore, at: datetime) -> dict[str, ClaimScore]:
    out = {}
    for node in store.claims():
        f = scoring.freshness(node.half_life_days, node.clock_start, at, superseded=node.superseded_by is not None)
        noise = scoring.is_noise(node.durability, node.half_life_days, node.clock_start, at,
                                 confirmations=node.confirmations, duplicate_rank=node.duplicate_rank)
        out[node.claim.claim_id] = ClaimScore(node, f, scoring.band(f), noise)
    return out


def run(store: GraphStore, at: datetime) -> KnowledgeReport:
    scores = score_claims(store, at)
    r = KnowledgeReport(at=at, items=len(list(store.items())), claims=len(scores))
    r.by_durability = {d: Counter() for d in DURABILITIES}
    for s in scores.values():
        r.by_durability[s.node.durability][s.band] += 1
        r.noise += s.noise
        r.confirmations += s.node.confirmations
    item_ids = {i.item_id for i in store.items()}
    for e in store.edges():
        if e.type == "DUPLICATES" and e.src in item_ids:
            r.duplicates.append((e.src, e.dst))
        elif e.type == "SUPERSEDES":
            r.supersessions.append((e.src, e.dst))
        elif e.type == "CONTRADICTS":
            r.contradictions.append((e.src, e.dst))
    r.held_back = dict(getattr(store, "held_back", {}))
    r.abstentions = store.abstentions() if hasattr(store, "abstentions") else []
    r.unreviewed = {i.item_id: i.review_status for i in store.items() if i.review_status != "accepted"}
    ideas = list(store.ideas())
    r.ideas = Counter(i.status for i in ideas)
    contradicted = {e.src for e in store.edges() if e.type == "CONTRADICTS"}
    r.conflicted_ideas = sorted(i.title for i in ideas if i.members & contradicted)
    topics = list(store.topics()) if hasattr(store, "topics") else []
    r.topics, r.attached = len(topics), sum(len(t.attached) for t in topics)
    r.proposals = Counter(st.status for st in getattr(store, "proposals", {}).values())
    people = list(store.people())
    r.people, r.people_pending = len(people), sum(p.enrichment_status == "pending" for p in people)

    later = score_claims(store, at + SOON)
    r.fading_soon = sorted((s for cid, s in scores.items()
                            if s.band == "fresh" and not s.noise and later[cid].band != "fresh"),
                           key=lambda s: s.freshness)
    durable = Counter(s.node.item_id for s in scores.values()
                      if s.node.durability in ("foundation", "reference") and s.band != "fresh" and not s.noise)
    r.refresh_due = sorted(durable.items(), key=lambda kv: (-kv[1], kv[0]))
    return r


def render(r: KnowledgeReport, store: GraphStore) -> str:
    def title(item_id):
        try:
            return store.item(item_id).title
        except (AttributeError, KeyError):
            return item_id

    claims = {c.claim.claim_id: c.claim.statement for c in store.claims(hidden=True)}

    def statement(cid):
        text = claims.get(cid, cid)
        return f"«{text[:80]}{'…' if len(text) > 80 else ''}»"

    def context(v):
        text = "; ".join(f"{k.replace('_', ' ')}: {value}" for k, value in (v.context or {}).items() if value)
        return text[:240]

    lines = [f"# Knowledge health at {r.at:%Y-%m-%d %H:%M %Z}", "",
             f"{r.items} items, {r.claims} claims, {r.people} people ({r.people_pending} awaiting enrichment). "
             f"{r.noise} claims collapsed as noise, {r.confirmations} confirmations.", "",
             f"{sum(r.ideas.values())} Ideas: {r.ideas['firm']} firm, {r.ideas['provisional']} provisional, "
             f"{len(r.conflicted_ideas)} with conflicts. {r.topics} topics ({r.attached} Ideas attached provisionally).",
             "", f"Review: {r.proposals['proposed']} proposals waiting for you, {r.proposals['stale']} stale, "
             f"{r.proposals['accepted']} accepted, {r.proposals['rejected']} rejected.", "",
             "## Freshness by durability", "", "| durability | half-life | " + " | ".join(BANDS) + " |",
             "|---|---|" + "---|" * len(BANDS)]
    for d in DURABILITIES:
        c = r.by_durability.get(d, Counter())
        lines.append(f"| {d} | {scoring.HALF_LIFE_DAYS[d]} d | " + " | ".join(str(c[b]) for b in BANDS) + " |")
    sections = [
        ("Reposts", [f"- {title(a)} repeats {title(b)}" for a, b in r.duplicates]),
        ("Supersessions", [f"- `{a[:8]}` supersedes `{b[:8]}`" for a, b in r.supersessions]),
        ("Open contradictions", [f"- `{a[:8]}` contradicts `{b[:8]}`" for a, b in r.contradictions]),
        ("Ideas with conflicts", [f"- {t}" for t in r.conflicted_ideas]),
        ("Not compared: the relator would not guess", [
            f"- {statement(v.claim_id)} / {statement(v.target_id)}: "
            f"{v.abstention.replace('_', ' ')}" + (f" ({context(v)})" if context(v) else "")
            for v in r.abstentions]),
        (f"Fading within {SOON.days} days", [f"- {s.freshness:.2f} {s.node.durability}: {s.node.claim.statement}"
                                             for s in r.fading_soon[:20]]),
        ("Worth a refresher: durable knowledge going stale", [f"- {title(i)}: {n} claims" for i, n in r.refresh_due[:20]]),
        ("Placed without passing review", [f"- {title(i)}: {why}" for i, why in sorted(r.unreviewed.items())]),
        ("Held back from the graph", [f"- `{i[:12]}`: {why}" for i, why in sorted(r.held_back.items())]),
    ]
    for head, body in sections:
        lines += ["", f"## {head}", ""] + (body or ["None."])
    return "\n".join(lines) + "\n"
