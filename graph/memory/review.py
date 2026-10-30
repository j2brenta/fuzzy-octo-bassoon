"""The hourly review: what it applies at once, and what it proposes to the user."""
from datetime import datetime

from graph import cluster, embed, proposals as proposal_stage
from graph.names import core as name_core, same_person as _same_person, split_names, surname_keys
from graph.relate import jaccard as _jaccard, words as _words
from graph.store import ClaimNode, Edge, PersonNode, ReviewReport
from graph.memory.common import (CONFIRM_DURABILITY, REVIEW_CLAIM_MATCH, REVIEW_DUPLICATE_SHARE, REVIEW_MIN_CLAIMS,
                                 _name_rank, _profile_pages, person_id)


class Review:
    """review() applies item duplicates and firmed Ideas, and proposes topics, merges, splits and relations."""

    def review(self, at: datetime, namer=None, confirm=None) -> ReviewReport:
        """The hourly review. Applied without asking: item duplicates and firming up Ideas. Proposed to the user:
        topics, Idea and people merges, and relations that replace or contradict durable claims.
        `namer(statements) -> title` names a topic (default: shared words); `confirm(a, b) -> bool` checks a
        merge of two Idea statements (default: similarity alone). Reports only what is new."""
        report = ReviewReport(at)
        report.proposals = self._propose(at, namer, confirm)
        visible = sorted((i for i in self._items.values() if not i.hidden and i.duplicate_of is None),
                         key=lambda i: (i.source.event_time, i.item_id))
        claims: dict[str, list[ClaimNode]] = {}
        for node in self._claims.values():
            claims.setdefault(node.item_id, []).append(node)
        changed = {frozenset((e.src, e.dst)) for e in self._relations if e.type in {"SUPERSEDES", "CONTRADICTS"}}
        same = {frozenset((e.src, e.dst)) for e in self._relations if e.type in {"SUPPORTS", "DUPLICATES"}}

        def share(a: str, b: str) -> tuple[int, int]:
            mine, theirs = claims.get(a, []), claims.get(b, [])
            matched = sum(any(frozenset((c.claim.claim_id, d.claim.claim_id)) not in changed and (
                frozenset((c.claim.claim_id, d.claim.claim_id)) in same
                or _jaccard(set(_words(c.claim.statement)), set(_words(d.claim.statement))) >= REVIEW_CLAIM_MATCH)
                for d in theirs) for c in mine)
            return matched, len(mine)

        duplicates: set[str] = set()
        for n, first in enumerate(visible):
            for later in visible[n + 1:]:
                if first.item_id in duplicates or later.item_id in duplicates:
                    continue
                for dup, other in ((later, first), (first, later)):  # the later one, if both are covered
                    matched, total = share(dup.item_id, other.item_id)
                    if total >= REVIEW_MIN_CLAIMS and matched >= REVIEW_DUPLICATE_SHARE * total:
                        duplicates.add(dup.item_id)
                        report.item_duplicates.append(Edge(dup.item_id, "DUPLICATES", other.item_id,
                                                           f"{matched} of {total} claims match the other item."))
                        break

        for idea in sorted(self._ideas.values(), key=lambda i: i.idea_id):
            items = [self._items[i] for i in {self._claims[cid].item_id for cid in idea.members}]
            authors = {i.author_id for i in items if not i.hidden and i.duplicate_of is None
                       and i.item_id not in duplicates and i.author_id}
            if idea.status == "provisional" and len(authors) >= 2:
                report.firmed.append(idea.idea_id)
        return report

    def _propose(self, at: datetime, namer, confirm) -> list:
        self._groups()
        known = {pid for pid, st in self.proposals.items() if st.status != "stale"}
        pending_topic_claims = {cid for st in self.proposals.values()
                                if st.proposal.kind == "topic" and st.status == "proposed"
                                for cid in st.proposal.payload["claims"]}
        out = []

        def add(kind, payload, why):
            p = proposal_stage.make(self, kind, payload, why, at)
            if p.proposal_id not in known and p.fingerprint is not None:
                known.add(p.proposal_id)
                out.append(p)

        ideas = sorted(self._ideas.values(), key=lambda i: (i.valid_at, i.idea_id))
        vectors = dict(zip((i.idea_id for i in ideas), self._idea_vectors([i.idea_id for i in ideas])))
        related = {frozenset((self._claims[e.src].idea_id, self._claims[e.dst].idea_id))
                   for e in self._relations if e.type in {"CONTRADICTS", "ELABORATES"}
                   and e.src in self._claims and e.dst in self._claims}

        # Ideas that say the same thing in other words: the ingest relator missed them.
        for n, m, similar in cluster.pairs_above(list(vectors.values()), embed.thresholds().idea_merge):
            a, b = ideas[n], ideas[m]
            if frozenset((a.idea_id, b.idea_id)) not in related and (confirm is None or confirm(a.title, b.title)):
                add("merge_ideas", {"claims": [a.canonical_claim_id, b.canonical_claim_id]},
                    f"The two statements are close (similarity {similar:.2f}) and nothing relates them yet.")

        # Topics from clusters (and single Ideas) no topic holds yet. Groups never split a cluster, so clusters from
        # two articles that sit close become one topic above both.
        in_topics = set().union(*(t.ideas | t.attached for t in self._topics.values())) if self._topics else set()
        loose = [i.idea_id for i in ideas if i.idea_id not in in_topics]
        cluster_of = {i: c for c in self._clusters.values() for i in c.ideas}
        for group in cluster.components(loose, [vectors[i] for i in loose],
                                        joined=[sorted(c.ideas) for c in self._clusters.values()]):
            members = [self._ideas[i] for i in group]
            claims = sorted(i.canonical_claim_id for i in members)
            posts = set().union(*(self._idea_items(i) for i in members))
            if len(members) < cluster.TOPIC_MIN_IDEAS or len(posts) < cluster.TOPIC_MIN_ITEMS \
                    or pending_topic_claims & set(claims):
                continue
            # One statement from each cluster first, so a namer that reads only the first few sees every part.
            firsts = list(dict.fromkeys(cluster_of[i].cluster_id for i in group if i in cluster_of))
            lead = [min(sorted(self._clusters[c].ideas & set(group))) for c in firsts]
            statements = [self._ideas[i].title for i in lead + [i for i in group if i not in lead]]
            parts = f" in {len(firsts)} clusters" if firsts else ""
            add("topic", {"claims": claims, "title": (namer or cluster.title)(statements)},
                f"{len(members)} Ideas{parts} from {len(posts)} posts sit close together.")

        # People who are probably one person: compatible names (graph/names.py: the same surname in either script,
        # first names that match, an affiliation in brackets set aside) and something in common: the same name
        # apart from a bracket, a post, linked posts (a page and the post that shared it), an organization, or the
        # page their profiles cite. Only namesakes are compared, so a new person meets the existing ones directly;
        # a pair already proposed or decided keeps its proposal id and is not asked again.
        people = sorted(self._people.values(), key=lambda p: p.person_id)
        orgs_of: dict[str, set[str]] = {}
        for tie in self._affiliations:
            orgs_of.setdefault(tie.person_id, set()).add(tie.org_id)
        namesakes: dict[str, list[PersonNode]] = {}
        for p in people:
            for key in surname_keys(p.name):
                namesakes.setdefault(key, []).append(p)
        pairs = sorted({(a.person_id, b.person_id) for group in namesakes.values()
                        for n, a in enumerate(group) for b in group[n + 1:]})
        for pa, pb in pairs:
            a, b = self._people[pa], self._people[pb]
            if (a.identity_url or b.identity_url) and a.identity_url != b.identity_url:
                continue  # A name-based proposal cannot undo reviewed identity separation.
            if not _same_person(a.name, b.name):
                continue
            (name_a, bracket_a), (name_b, bracket_b) = name_core(a.name), name_core(b.name)
            posts_a, posts_b = self._posts_of(a), self._posts_of(b)
            # "John Smith (Google)" and "John Smith (MIT)": the brackets tell two people apart, so only something
            # they share can propose them as one.
            clash = bracket_a and bracket_b and _words(bracket_a) != _words(bracket_b)
            if _words(name_a) == _words(name_b) and not clash:
                aside = [f"“{x}”" for x in (bracket_a, bracket_b) if x]
                why = "have the same name" + (f" apart from {' and '.join(aside)}" if aside else "")
            elif posts_a & posts_b:
                why = "share a surname and appear together"
            elif self._links_to(posts_a, posts_b) or self._links_to(posts_b, posts_a):
                why = "share a surname and appear in a post and the page it links to"
            elif orgs_of.get(a.person_id, set()) & orgs_of.get(b.person_id, set()):  # enrichment ties too
                why = "share a surname and an organization"
            elif _profile_pages(a) & _profile_pages(b):
                why = "share a surname and their profiles cite the same page"
            else:
                continue
            absorbed, kept = sorted((a, b), key=_name_rank)
            add("merge_people", {"people": [absorbed.person_id, kept.person_id]},
                f"{absorbed.name} and {kept.name} {why}.")

        # A record that names several people at once ("Abhijit Banerjee and Esther Duflo (MIT)"): one each.
        for p in people:
            names = split_names(p.name)
            if names and not p.identity_url:
                here = [n for n in names if self._aliases.get(person_id(n), person_id(n)) in self._people]
                also = f"; {' and '.join(here)} {'is' if len(here) == 1 else 'are'} already here" if here else ""
                add("split_person", {"person": p.person_id, "name": p.name, "names": names},
                    f"“{p.name}” names {len(names)} people: {', '.join(names)}{also}.")

        # Replacing or contradicting a durable claim is live already; the user confirms or rejects it.
        for e in sorted(self._relations, key=lambda e: (e.src, e.dst)):
            if e.type in {"SUPERSEDES", "CONTRADICTS"} and e.dst in self._claims \
                    and self._claims[e.dst].durability in CONFIRM_DURABILITY:
                add("relation", {"claims": [e.src, e.dst], "type": e.type}, e.rationale or f"{e.type} by the relator.")
        return out
