"""People and organizations: who they are, what is said about them, and what enrichment adds."""
import hashlib

from graph.names import display_name
from graph.relate import words as _words
from graph.store import Affiliation, OrgNode, PersonNode
from refresher import scoring
from refresher.schemas import Override
from graph.memory.common import org_id, person_id


class Profiles:
    """Profiles derived from the claims, and the person and organization records behind them."""

    def _derive(self):
        """Recompute what scoring decides from the edges, so the result does not depend on arrival order.

        An independent confirmation (SUPPORTS) extends the earlier claim's half-life and moves its clock to the
        later sighting; known speakers gain a confirmation. Unresolved disagreements do not decide who is wrong.
        """
        for node in self._claims.values():
            node.confirmations, node.superseded_by = 0, None
            node.clock_start = self._event_time(node)
        for person in self._people.values():
            person.confirmations = person.contradictions = person.disagreements = 0
        for e in self._relations:
            if e.src not in self._claims or e.dst not in self._claims:
                continue
            later, first = self._claims[e.src], self._claims[e.dst]
            authors = {n.speaker_id if n.attribution is not None else self._items[n.item_id].author_id
                       for n in (later, first)} & self._people.keys()
            reviewed = all(self._items[n.item_id].review_status == "accepted" for n in (later, first))
            if e.type == "SUPPORTS":
                first.confirmations += 1
                first.clock_start = max(first.clock_start, e.at or self._event_time(later))
                # Legacy records keep their original author-trust behavior. Attributed
                # records use checked origins, computed by assertion group below.
                for pid in authors if reviewed and later.attribution is None and first.attribution is None else ():
                    self._people[pid].confirmations += 1
            elif e.type == "SUPERSEDES":
                first.superseded_by = later.claim.claim_id
            elif e.type == "CONTRADICTS" and reviewed:
                if scoring.elapsed_days(self._event_time(first), e.at or self._event_time(later)) <= first.claim.half_life_days:
                    for pid in authors:
                        self._people[pid].disagreements += 1
        from graph.corroboration import assertion_groups, checked_origins
        accepted = {cid:n for cid,n in self._claims.items()
                    if self._items[n.item_id].review_status == 'accepted'
                    and not self._items[n.item_id].duplicate_of}
        groups = assertion_groups(accepted, self._relations)
        duplicates = {e.src for e in self._relations if e.type == 'DUPLICATES'}
        current = {cid:n for cid,n in accepted.items() if cid not in duplicates and n.superseded_by is None}
        for pid,origins in checked_origins(current,groups).items():
            self._people[pid].confirmations += len(origins)
        for node in self._claims.values():
            base = (node.claim.half_life_days if node.durability == node.claim.durability_class
                    else scoring.HALF_LIFE_DAYS[node.durability])
            node.half_life_days = scoring.half_life(base, node.confirmations)

    def _person(self, name: str, identity_url: str | None = None) -> PersonNode:
        if identity_url:
            pid = 'person:profile-' + hashlib.sha256(identity_url.encode()).hexdigest()[:24]
            return self._people.setdefault(pid, PersonNode(pid, display_name(name), identity_url=identity_url))
        pid = self._aliases.get(person_id(name), person_id(name))
        return self._people.setdefault(pid, PersonNode(pid, display_name(name)))

    def _posts_of(self, p: PersonNode) -> set[str]:
        """Items they wrote, are named in, or that state facts about them."""
        return p.authored | p.mentioned_in | {self._claims[c].item_id for c in p.facts if c in self._claims}

    def canonical_person(self, pid: str) -> str:
        """The person id a merged-away id now stands for."""
        return self._aliases.get(pid, pid)

    def _links_to(self, posts: set[str], pages: set[str]) -> bool:
        """Whether one of the pages was captured from a link in one of the posts."""
        return any(self._items[i].linked_from in posts for i in pages if i in self._items)

    def _org(self, name: str) -> OrgNode:
        return self._orgs.setdefault(org_id(name), OrgNode(org_id(name), name))

    def add_enrichment(self, person_id: str, affiliation: dict | None) -> None:
        """Tie a person to the organization web enrichment found for them ({"value", "url"}), creating it when no
        item names it. Called by vault.replay after the enrichment records are read."""
        if person_id not in self._people or not affiliation or not _words(affiliation.get("value", "")):
            return
        tie = Affiliation(person_id, self._org(affiliation["value"]).org_id, "affiliated", url=affiliation.get("url"))
        if tie not in self._affiliations:
            self._affiliations.append(tie)

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
