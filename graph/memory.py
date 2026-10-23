"""In-memory GraphStore, replayed from processed extraction records.

Used until the Neo4j store lands and kept afterwards as the test double. The relation check here is a
lexical baseline (no embeddings yet): near-identical text is a repost, a near-identical claim from a
different author is a confirmation. Victor's relate.py replaces it.
"""
import re
import unicodedata

from datetime import datetime
from typing import Iterable

from graph.store import ClaimNode, Edge, IdeaNode, ItemNode, PersonNode, Placement, ReviewReport, SourceNode
from refresher import scoring
from refresher.schemas import Extraction, Override

REPOST_SIMILARITY = 0.8  # item text, word 3-gram Jaccard
CLAIM_SIMILARITY = 0.8  # claim statement, word-set Jaccard
WORD = re.compile(r"\w+", re.UNICODE)


def person_id(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode() or name
    return "person:" + "-".join(WORD.findall(folded.casefold()))


def _words(text: str) -> list[str]:
    return WORD.findall(text.casefold())


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _shingles(text: str, n: int = 3) -> set[tuple[str, ...]]:
    w = _words(text)
    return {tuple(w[i:i + n]) for i in range(max(1, len(w) - n + 1))}


# Telegram Desktop "Copy" header, kept in raw text by capture: "Kirill R, [7 Oct 2026 at 12:13:38]:"
_TG_HEADER = re.compile(r"^.+, \[\d{1,2} \w{3} \d{4} at [\d:]+\]:$")


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("#").strip()
        if line and not _TG_HEADER.match(line):
            return line[:80]
    return fallback


class MemoryStore:
    def __init__(self, include_unreviewed: bool = False):
        # Place items that failed or have not finished ingestion's quality gate, marked by review_status.
        # Items triage chose not to keep stay out either way.
        self.include_unreviewed = include_unreviewed
        self._items: dict[str, ItemNode] = {}
        self._claims: dict[str, ClaimNode] = {}
        self._ideas: dict[str, IdeaNode] = {}  # filled by relate.py; empty with the lexical baseline
        self._people: dict[str, PersonNode] = {}
        self._sources: dict[str, SourceNode] = {}
        self._orgs: dict[str, str] = {}
        self._edges: list[Edge] = []
        self.held_back: dict[str, str] = {}  # item id -> reason

    # --- writes ---

    def apply(self, extraction: Extraction, text: str = "") -> Placement:
        ex, src = extraction, extraction.source
        if ex.item_id in self._items:
            return Placement(ex.item_id, "already_placed")
        skipped = ex.triage is not None and not ex.triage.keep
        if skipped or (not ex.quality_gate_passed and not self.include_unreviewed):
            self.held_back[ex.item_id] = "skipped by triage" if skipped else ex.quality_status
            return Placement(ex.item_id, "held_back")

        item = ItemNode(ex.item_id, src, _title(text, src.source_name or ex.item_id), text,
                        durability=ex.triage.durability_class if ex.triage else None,
                        review_status="accepted" if ex.quality_gate_passed else ex.quality_status)
        self._items[ex.item_id] = item
        new_edges: list[Edge] = []

        key = f"source:{src.channel}:{src.source_name or src.source_id}"
        self._sources.setdefault(key, SourceNode(key, src.source_name or src.source_id, src.channel)).items.add(ex.item_id)
        item.source_key = key
        new_edges.append(Edge(ex.item_id, "PUBLISHED_IN", key))

        if src.author_hint and src.author_hint.name:
            author = self._person(src.author_hint.name)
            if src.author_hint.handle:
                author.handles.add(src.author_hint.handle)
            author.authored.add(ex.item_id)
            item.author_id = author.person_id
            new_edges.append(Edge(ex.item_id, "AUTHORED_BY", author.person_id))
        for ent in ex.entities:
            if ent.kind == "person":
                pid = person_id(ent.name)
                if pid == item.author_id:
                    continue
                self._person(ent.name).mentioned_in.add(ex.item_id)
                new_edges.append(Edge(ex.item_id, "MENTIONS", pid))
            elif ent.kind == "organization":
                oid = "org:" + "-".join(_words(ent.name))
                self._orgs.setdefault(oid, ent.name)
                new_edges.append(Edge(ex.item_id, "MENTIONS", oid))

        # Repost check on the whole item, ordered by event time, not arrival order.
        shingles = _shingles(text)
        for other in self._items.values():
            if other.item_id == ex.item_id or not other.text:
                continue
            if _jaccard(shingles, _shingles(other.text)) >= REPOST_SIMILARITY:
                first, later = sorted((item, other), key=lambda i: (i.source.event_time, i.item_id))
                later.duplicate_of = first.duplicate_of or first.item_id
                new_edges.append(Edge(later.item_id, "DUPLICATES", first.item_id,
                                      "Near-identical text; the later one is a repost."))

        for claim in ex.claims:
            node = ClaimNode(claim, ex.item_id, claim.durability_class, claim.half_life_days, src.event_time)
            self._claims[claim.claim_id] = node
            new_edges += [Edge(claim.claim_id, "FROM", s.section_id) for s in claim.spans]
            new_edges += self._relate(node)

        self._edges += new_edges
        self._drop_repost_relations()
        self._recount_duplicates()
        self._derive()
        return Placement(ex.item_id, "placed", len(ex.claims), item.author_id, item.duplicate_of,
                         [e for e in new_edges if e.type in {"DUPLICATES", "SUPPORTS"}])

    def _relate(self, node: ClaimNode) -> list[Edge]:
        """Write relation edges only; _derive turns them into confirmations, half-lives and clocks."""
        words = set(_words(node.claim.statement))
        author = self._items[node.item_id].author_id
        out = []
        for other in list(self._claims.values()):
            if other.item_id == node.item_id:
                continue
            if _jaccard(words, set(_words(other.claim.statement))) < CLAIM_SIMILARITY:
                continue
            first, later = sorted((other, node), key=lambda c: (self._event_time(c), c.claim.claim_id))
            other_author = self._items[other.item_id].author_id
            if self._items[node.item_id].duplicate_of or self._items[other.item_id].duplicate_of:
                continue  # a repost's claims are noise; its item already DUPLICATES the original
            if author and other_author and author != other_author:
                out.append(Edge(later.claim.claim_id, "SUPPORTS", first.claim.claim_id,
                                "Same statement from a different author.", self._event_time(later)))
            else:
                out.append(Edge(later.claim.claim_id, "DUPLICATES", first.claim.claim_id,
                                "Same statement, same or unknown author.", self._event_time(later)))
        return out

    def _event_time(self, node: ClaimNode):
        return self._items[node.item_id].source.event_time

    def _drop_repost_relations(self):
        """An item found to be a repost only after its claims were related loses those claim relations,
        so the edges are the same whichever arrived first."""
        def reposted(claim_id):
            node = self._claims.get(claim_id)
            return node is not None and self._items[node.item_id].duplicate_of is not None
        self._edges = [e for e in self._edges if not (e.type in {"SUPPORTS", "DUPLICATES"} and e.src in self._claims
                                                       and (reposted(e.src) or reposted(e.dst)))]

    def _recount_duplicates(self):
        dup_claims = {e.src for e in self._edges if e.type == "DUPLICATES"}
        for node in self._claims.values():
            reposted = self._items[node.item_id].duplicate_of is not None
            node.duplicate_rank = 1 if reposted or node.claim.claim_id in dup_claims else 0

    def _derive(self):
        """Recompute what scoring decides from the edges, so the result does not depend on arrival order.

        An independent confirmation (SUPPORTS) extends the earlier claim's half-life and moves its clock to the
        later sighting; both authors gain a confirmation. CONTRADICTS counts against both authors.
        """
        for node in self._claims.values():
            node.confirmations, node.superseded_by = 0, None
            node.clock_start = self._event_time(node)
        for person in self._people.values():
            person.confirmations = person.contradictions = 0
        for e in self._edges:
            if e.src not in self._claims or e.dst not in self._claims:
                continue
            later, first = self._claims[e.src], self._claims[e.dst]
            authors = {self._items[later.item_id].author_id, self._items[first.item_id].author_id} - {None}
            if e.type == "SUPPORTS":
                first.confirmations += 1
                first.clock_start = max(first.clock_start, e.at or self._event_time(later))
                for pid in authors:
                    self._people[pid].confirmations += 1
            elif e.type == "SUPERSEDES":
                first.superseded_by = later.claim.claim_id
            elif e.type == "CONTRADICTS":
                for pid in authors:
                    self._people[pid].contradictions += 1
        for node in self._claims.values():
            base = (node.claim.half_life_days if node.durability == node.claim.durability_class
                    else scoring.HALF_LIFE_DAYS[node.durability])
            node.half_life_days = scoring.half_life(base, node.confirmations)

    def _person(self, name: str) -> PersonNode:
        pid = person_id(name)
        return self._people.setdefault(pid, PersonNode(pid, name))

    def apply_override(self, override: Override) -> None:
        o = override
        if o.target == "person":
            person = self._people.get(o.target_id) or self._people.get(person_id(o.target_id))
            if person is None:
                return
            if o.trust is not None:
                person.trust_pinned = o.trust
            if o.vote:
                person.up += o.vote > 0
                person.down += o.vote < 0
            return
        targets = ([self._claims[o.target_id]] if o.target == "claim" and o.target_id in self._claims
                   else [c for c in self._claims.values() if c.item_id == o.target_id] if o.target == "item" else [])
        for node in targets:
            if o.durability_class:
                node.durability = o.durability_class
            if o.vote:
                node.votes += o.vote
        self._derive()

    # --- reads ---

    def items(self):
        return self._items.values()

    def claims(self):
        return self._claims.values()

    def people(self):
        return self._people.values()

    def sources(self):
        return self._sources.values()

    def organizations(self):
        return dict(self._orgs)

    def edges(self):
        return list(self._edges)

    def item(self, item_id: str) -> ItemNode:
        return self._items[item_id]

    def person(self, pid: str) -> PersonNode:
        return self._people[pid]

    def ideas(self):
        return self._ideas.values()

    def idea(self, idea_id: str) -> IdeaNode:
        return self._ideas[idea_id]

    def review(self, at: datetime) -> ReviewReport:
        return ReviewReport(at)  # review.py (Victor) decides; nothing to firm up or merge without Ideas

    # --- graph.query: lexical baseline ---

    def claims_for(self, terms: Iterable[str], hops: int = 2) -> list[ClaimNode]:
        wanted = {w for t in terms for w in _words(t)}
        found = {cid for cid, c in self._claims.items() if wanted & set(_words(c.claim.statement))}
        # Undirected: edges, plus each claim to its item and Idea, so a hop reaches sibling claims via those.
        pairs = [(e.src, e.dst) for e in self._edges]
        pairs += [(cid, c.item_id) for cid, c in self._claims.items()]
        pairs += [(cid, c.idea_id) for cid, c in self._claims.items() if c.idea_id]
        near: dict[str, set[str]] = {}
        for a, b in pairs:
            near.setdefault(a, set()).add(b)
            near.setdefault(b, set()).add(a)
        frontier = set(found)
        for _ in range(hops):
            frontier = {n for f in frontier for n in near.get(f, ())} - found
            found |= frontier
        return sorted((self._claims[cid] for cid in found if cid in self._claims), key=lambda c: c.claim.claim_id)

    def chain(self, claim_id: str) -> list[ClaimNode]:
        older = {e.src: e.dst for e in self._edges if e.type == "SUPERSEDES"}
        newer = {v: k for k, v in older.items()}
        first = claim_id
        while first in older:
            first = older[first]
        out = [first]
        while out[-1] in newer:
            out.append(newer[out[-1]])
        return [self._claims[cid] for cid in out if cid in self._claims]

    def people_for(self, claim_ids: Iterable[str]) -> list[PersonNode]:
        pids = set()
        for cid in claim_ids:
            node = self._claims.get(cid)
            if node is None:
                continue
            pids |= {node.subject_id, self._items[node.item_id].author_id} - {None}
        return sorted((self._people[p] for p in pids if p in self._people), key=lambda p: p.person_id)

    def neighbours(self, item_id: str) -> list[Edge]:
        ids = {item_id} | {cid for cid, c in self._claims.items() if c.item_id == item_id}
        return [e for e in self._edges if e.src in ids or e.dst in ids]

    def trust(self, pid: str) -> float:
        p = self._people[pid]
        if p.trust_pinned is not None:
            return p.trust_pinned
        return scoring.trust_learned(p.confirmations, p.contradictions, p.up, p.down)
