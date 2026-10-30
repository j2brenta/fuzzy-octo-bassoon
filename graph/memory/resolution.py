"""Turning every verdict into relation edges, profile facts and Idea groups."""
from graph.relate import jaccard as _jaccard, words as _words
from graph.store import ClaimNode, Edge
from graph.memory.common import EDGE_FOR, REPOST_CLAIM_SIMILARITY, org_id, person_id


class Resolution:
    """_resolve() reads the verdicts in event order; provenance decides what counts as independent support."""

    def _event_time(self, node: ClaimNode):
        return self._items[node.item_id].source.event_time

    def _order(self, node: ClaimNode):
        return self._event_time(node), node.claim.claim_id

    def _independent(self, a: ClaimNode, b: ClaimNode) -> bool:
        """Provenance decides SUPPORTS: two known, different authors, and neither item a repost."""
        ia, ib = self._items[a.item_id], self._items[b.item_id]
        pa, pb = (a.speaker_id, b.speaker_id) if a.attribution is not None or b.attribution is not None else (ia.author_id, ib.author_id)
        return (pa is not None and pb is not None and pa != pb
                and ia.duplicate_of is None and ib.duplicate_of is None)

    def _resolve(self) -> dict[str, list[str]]:
        """Turn all verdicts into relation edges, profile facts and Idea groups (root claim id -> members)."""
        self._relations = []
        for profile in [*self._people.values(), *self._orgs.values()]:
            profile.facts = set()
        for person in self._people.values():
            person.statements = set()
        world: list[ClaimNode] = []
        for node in sorted(self._claims.values(), key=self._order):
            verdict = self._verdicts.get(node.claim.claim_id)
            hidden = self._items[node.item_id].hidden
            node.speaker_id = None
            if not hidden and node.attribution and node.attribution.person_name:
                speaker = self._person(node.speaker_name or node.attribution.person_name, node.identity_url)
                node.speaker_id = speaker.person_id
                speaker.statements.add(node.claim.claim_id)
            if verdict is None or verdict.subject == "world":
                node.subject_id = None
                world.append(node)
                continue
            # A profile fact: it joins the person's or organization's profile and gets no Idea.
            if verdict.subject == "person":
                node.subject_id = self._aliases.get(person_id(verdict.subject_name), person_id(verdict.subject_name))
                if not hidden:
                    self._person(verdict.subject_name).facts.add(node.claim.claim_id)
            else:
                node.subject_id = org_id(verdict.subject_name)
                if not hidden:
                    self._org(verdict.subject_name).facts.add(node.claim.claim_id)
            if not hidden:
                self._relations.append(Edge(node.claim.claim_id, "ABOUT", node.subject_id))

        parent = {n.claim.claim_id: n.claim.claim_id for n in world}

        def find(cid):
            while parent[cid] != cid:
                parent[cid] = parent[parent[cid]]
                cid = parent[cid]
            return cid

        # A repost's claims follow their match in the original item, whatever their own verdicts say. A claim that
        # matches nothing there, or matches a profile fact, stands on its own.
        redirect = {}
        for node in world:
            original = self._items[node.item_id].duplicate_of
            mine = set(_words(node.claim.statement))
            scored = sorted((-_jaccard(mine, set(_words(n.claim.statement))), n.claim.claim_id)
                            for n in self._claims.values() if n.item_id == original)
            if scored and -scored[0][0] >= REPOST_CLAIM_SIMILARITY and scored[0][1] in parent:
                redirect[node.claim.claim_id] = scored[0][1]

        links = []  # (node, label, target, rationale), SAME first so the update pass sees whole SAME classes
        for node in world:
            cid = node.claim.claim_id
            if cid in redirect:
                links.append((node, "SAME", redirect[cid], "Repost of the original item."))
                continue
            verdict = self._verdicts.get(cid)
            if verdict is not None and verdict.label != "NONE":
                links.append((node, verdict.label, redirect.get(verdict.target_id, verdict.target_id), verdict.rationale))
        links.sort(key=lambda link: link[1] != "SAME")

        same = {cid: {cid} for cid in parent}  # claims that say the same thing, through SAME links
        related = set()
        for node, label, target, why in links:
            cid = node.claim.claim_id
            seen = set()
            while target not in parent and target in self._held_same and target not in seen:
                seen.add(target)  # a held-back repeat: relate to what it repeats
                target = redirect.get(self._held_same[target], self._held_same[target])
            if target not in parent or target == cid or frozenset((cid, target)) in related | self._suppressed:
                continue  # not placed (yet), a profile fact, already related, or the user rejected the relation
            related.add(frozenset((cid, target)))
            other = self._claims[target]
            first, later = sorted((other, node), key=self._order)
            at = self._event_time(later)
            if label == "ADDS_DETAIL":
                self._relations.append(Edge(cid, "ELABORATES", target, why, at))
                continue
            parent[find(cid)] = find(target)
            if label == "SAME":
                kind = "SUPPORTS" if self._independent(node, other) else "DUPLICATES"
                self._relations.append(Edge(later.claim.claim_id, kind, first.claim.claim_id, why, at))
                merged = same[cid] | same[target]
                for member in merged:
                    same[member] = merged
            elif label == "UPDATES":
                # The newer claim replaces every earlier sighting of the older statement, not just the one judged.
                new = later.claim.claim_id
                for old in sorted(same[first.claim.claim_id]):
                    earlier = self._claims[old]
                    if old != new and earlier.item_id != later.item_id \
                            and self._event_time(earlier) < self._event_time(later) \
                            and frozenset((new, old)) not in self._suppressed:  # never its own item or moment
                        related.add(frozenset((new, old)))
                        self._relations.append(Edge(new, "SUPERSEDES", old, why, at))
            else:
                self._relations.append(Edge(later.claim.claim_id, EDGE_FOR[label], first.claim.claim_id, why, at))

        for a, b in self._merges:  # Ideas the user merged
            if a in parent and b in parent:
                parent[find(a)] = find(b)

        groups: dict[str, list[str]] = {}
        for cid in parent:
            groups.setdefault(find(cid), []).append(cid)
        return groups
