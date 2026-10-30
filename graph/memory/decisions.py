"""What a stored review or a decided proposal changes, and the claims a new item is judged against."""
from graph.names import core as name_core
from graph.attribution import author_name
from graph.relate import Candidate
from graph.store import Affiliation, Edge, ReviewReport
from graph.memory.common import org_id


class Decisions:
    """Replayed reviews and proposals: merged and split people, merged Ideas, topics, rejected relations."""

    def apply_review(self, report: ReviewReport) -> None:
        """Replay a stored review: item duplicates and firmed Ideas. Unknown ids (not placed here) are skipped."""
        for e in report.item_duplicates:
            dup, first = self._items.get(e.src), self._items.get(e.dst)
            if dup is None or first is None or dup.duplicate_of is not None:
                continue
            dup.duplicate_of = first.duplicate_of or first.item_id
            self._edges.append(e)
        self._firm |= set(report.firmed)
        self._refresh()

    def apply_proposal(self, state) -> None:
        """Replay a decided proposal (a graph.proposals.ProposalState): accepted ones change the graph, a rejected
        relation is taken out. Proposed, stale and unplaced ones change nothing."""
        p = state.proposal
        if state.status == "rejected" and p.kind == "relation":
            self._suppressed.add(frozenset(p.payload["claims"]))
        elif state.status != "accepted":
            return
        elif p.kind == "merge_ideas":
            claims = p.payload["claims"]
            self._merges += [(claims[0], other) for other in claims[1:]]
        elif p.kind == "topic":
            self._topic_specs["topic:" + p.proposal_id[2:14]] = (p.payload["title"], list(p.payload["claims"]))
        elif p.kind == "merge_people":
            self._merge_person(*p.payload["people"])
        elif p.kind == "split_person":
            self._splits[p.payload["person"]] = list(p.payload["names"])
        self._refresh()

    def _merge_person(self, absorbed: str, kept: str) -> None:
        gone, person = self._people.get(absorbed), self._people.get(kept)
        if gone is None or person is None or absorbed == kept:
            return
        if (gone.identity_url or person.identity_url) and gone.identity_url != person.identity_url:
            return  # Reviewed identities cannot be collapsed by name-based merges.
        self._aliases[absorbed] = kept
        bracket = name_core(gone.name)[1]
        if bracket and org_id(bracket) in self._orgs and self._posts_of(gone):
            # "Herman Daly (University of Maryland)": the bracket was an affiliation; tied when that organization is
            # one the vault knows, so a bracket such as "ECLAC tradition" makes no organization.
            tie = Affiliation(kept, org_id(bracket), "affiliated", min(self._posts_of(gone)))
            if tie not in self._affiliations:
                self._affiliations.append(tie)
        person.handles |= gone.handles
        person.authored |= gone.authored
        person.mentioned_in |= gone.mentioned_in
        del self._people[absorbed]
        self._affiliations = list(dict.fromkeys(
            Affiliation(kept, a.org_id, a.relation, a.item_id, a.url) if a.person_id == absorbed else a
            for a in self._affiliations))
        for item in self._items.values():
            if item.author_id == absorbed:
                item.author_id = kept
        self._edges = list(dict.fromkeys(Edge(e.src, e.type, kept if e.dst == absorbed else e.dst, e.rationale, e.at)
                                         for e in self._edges))

    def _split_people(self) -> None:
        """Each accepted split, again on every refresh, since a post placed later may name the pair as one: the
        record's posts, facts, statements and ties go to every person it names (each joins a person the vault
        already has under that name), the claims and items that point at one id to the first, and an affiliation
        in brackets ("(MIT)") ties them all when the organization is known. The record itself is gone."""
        for compound, names in self._splits.items():
            gone = self._people.pop(compound, None)
            if gone is None:
                continue
            parts = [self._person(n) for n in names]
            first = parts[0].person_id
            for part in parts:
                part.handles |= gone.handles
                part.authored |= gone.authored
                part.mentioned_in |= gone.mentioned_in
                part.facts |= gone.facts
                part.statements |= gone.statements
            for item in self._items.values():
                if item.author_id == compound:
                    item.author_id = first
            for node in self._claims.values():
                node.speaker_id = first if node.speaker_id == compound else node.speaker_id
                node.subject_id = first if node.subject_id == compound else node.subject_id
            bracket = name_core(gone.name)[1]
            posts = self._posts_of(gone)  # the record's own posts, not older ones of a namesake
            known = [Affiliation(p.person_id, org_id(bracket), "affiliated", min(posts)) for p in parts] \
                if bracket and org_id(bracket) in self._orgs and posts else []
            moved = [Affiliation(p.person_id, a.org_id, a.relation, a.item_id, a.url)
                     for a in self._affiliations if a.person_id == compound for p in parts]
            self._affiliations = list(dict.fromkeys(
                [a for a in self._affiliations if a.person_id != compound] + moved + known))
            self._edges = list(dict.fromkeys(
                e2 for e in self._edges
                for e2 in ([Edge(e.src, e.type, p.person_id, e.rationale, e.at) for p in parts]
                           if e.dst == compound else [e])))

    def placed_fingerprint(self) -> str:
        """Which items are placed, in O(1): stored with each verdict file (Relation.pool)."""
        return f"{self._placed_hash:016x}"

    def candidate_pool(self, item_id: str) -> list[Candidate]:
        """World claims from other items, hidden stubs included, for relate.judge. A repost's claims are left
        out: its original stands for them."""
        if self._dirty:
            self._refresh()
        out = []
        for cid, node in self._claims.items():
            verdict = self._verdicts.get(cid)
            item = self._items[node.item_id]
            if node.item_id == item_id or item.duplicate_of or (verdict is not None and verdict.subject != "world"):
                continue
            author = author_name(item.source)
            out.append(Candidate(cid, node.claim.statement, node.item_id, item.source.event_time,
                                 item.source.source_name, author, node.idea_id, self._entities[node.item_id]))
        return out
