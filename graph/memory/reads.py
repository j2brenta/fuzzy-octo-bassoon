"""Reading the graph: its nodes and edges, and the lexical graph.query baseline."""
from datetime import datetime
from typing import Iterable

from graph.relate import words as _words
from graph.store import (Affiliation, ClaimNode, Edge, IdeaNode, ItemNode, Link, OrgNode, PersonNode)
from refresher import scoring
from refresher.schemas import Verdict
from graph.memory.common import (person_id)


class Reads:
    """Read-only views of the store."""

    def items(self, hidden: bool = False):
        return [i for i in self._items.values() if hidden or not i.hidden]

    def claims(self, hidden: bool = False):
        return [c for c in self._claims.values() if hidden or not self._items[c.item_id].hidden]

    def people(self):
        return self._people.values()

    def sources(self):
        return self._sources.values()

    def organizations(self):
        return self._orgs.values()

    def organization(self, oid: str) -> OrgNode:
        return self._orgs[oid]

    def affiliations(self) -> list[Affiliation]:
        return sorted(self._affiliations, key=lambda a: (a.person_id, a.org_id, a.relation, a.item_id or "", a.url or ""))

    def edges(self):
        return self._edges + self._relations

    def verdict(self, claim_id: str) -> Verdict | None:
        return self._verdicts.get(claim_id)

    def abstentions(self) -> list[Verdict]:
        """Verdicts where the relator would not guess (not comparable, or not enough evidence), between visible
        claims. They are recorded, drawn as no relation, and listed for the reader."""
        visible = {c.claim.claim_id for c in self.claims()}
        return sorted((v for cid, v in self._verdicts.items()
                       if v.abstention and cid in visible and v.target_id in self._claims),
                      key=lambda v: v.claim_id)

    def org_facts(self) -> dict[str, set[str]]:
        return {oid: set(o.facts) for oid, o in self._orgs.items() if o.facts}

    def item(self, item_id: str) -> ItemNode:
        return self._items[item_id]

    def person(self, pid: str) -> PersonNode:
        return self._people[pid]

    def ideas(self):
        return self._ideas.values()

    def idea(self, idea_id: str) -> IdeaNode:
        return self._ideas[idea_id]

    def topics(self):
        self._groups()
        return self._topics.values()

    def clusters(self):
        self._groups()
        return self._clusters.values()

    def links(self, level: str | None = None) -> list[Link]:
        self._groups()
        return [link for link in self._links if level is None or link.level == level]

    def claims_for(self, terms: Iterable[str], hops: int = 2) -> list[ClaimNode]:
        """A claim matches a term holding all its words; a term naming a person starts from that person. From
        there, hops follow meaning only (Idea membership, profile facts, claim relations), never a whole item.
        Visible claims, nearest first."""
        starts = set()
        for term in terms:
            want = set(_words(term))
            if not want:
                continue
            starts |= {cid for cid, c in self._claims.items() if want <= set(_words(c.claim.statement))}
            if person_id(term) in self._people:
                starts.add(person_id(term))
        near: dict[str, set[str]] = {}
        for e in self._relations:  # PART_OF, ABOUT and the claim relations
            near.setdefault(e.src, set()).add(e.dst)
            near.setdefault(e.dst, set()).add(e.src)
        distance = {node: 0 for node in starts}
        frontier = set(starts)
        for hop in range(1, hops + 1):
            frontier = {n for f in frontier for n in near.get(f, ())} - distance.keys()
            distance.update({n: hop for n in frontier})
        found = [(d, cid) for cid, d in distance.items()
                 if cid in self._claims and not self._items[self._claims[cid].item_id].hidden]
        return [self._claims[cid] for d, cid in sorted(found)]

    def chain(self, claim_id: str) -> list[ClaimNode]:
        if claim_id not in self._claims:
            return []
        out = [self._claims[claim_id]]
        while out[-1].superseded_by and self._claims[out[-1].superseded_by] not in out:
            out.append(self._claims[out[-1].superseded_by])
        while True:  # an update can replace several sightings; walk back through the earliest
            older = sorted((c for c in self._claims.values() if c.superseded_by == out[0].claim.claim_id),
                           key=self._order)
            if not older or older[0] in out:
                return out
            out.insert(0, older[0])

    def people_for(self, claim_ids: Iterable[str]) -> list[PersonNode]:
        """Authors of the claims' items and the people the claims are about, most involved first."""
        count: dict[str, int] = {}
        for cid in claim_ids:
            node = self._claims.get(cid)
            if node is None:
                continue
            for pid in {node.subject_id, self._items[node.item_id].author_id} - {None}:
                count[pid] = count.get(pid, 0) + 1
        return [self._people[p] for p in sorted(count, key=lambda p: (-count[p], p)) if p in self._people]

    def neighbours(self, item_id: str) -> list[Edge]:
        """Edges touching the item or its claims, without span bookkeeping (FROM)."""
        ids = {item_id} | {cid for cid, c in self._claims.items() if c.item_id == item_id}
        return [e for e in self.edges() if (e.src in ids or e.dst in ids) and e.type != "FROM"]

    def trust(self, pid: str) -> float:
        p = self._people[pid]
        if p.trust_pinned is not None:
            return p.trust_pinned
        return scoring.trust_learned(p.confirmations, p.contradictions, p.up, p.down)

    def experts(self, at: datetime, subject_id: str | None = None, *,
                idea_ids: Iterable[str] | None = None, subject_title: str | None = None) -> list[dict]:
        """Importance in this collection, scoped to an accepted topic or provisional cluster."""
        from refresher.people_scoring import score_people
        return score_people(self, at, subject_id, idea_ids=idea_ids, subject_title=subject_title)
