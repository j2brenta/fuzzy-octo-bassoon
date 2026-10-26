"""In-memory GraphStore, replayed from processed extraction records and their relation verdicts.

Used until the Neo4j store lands and kept afterwards as the test double. Relations come from verdicts
(graph/relate.py): stored ones from relations/<item_id>.json, or lexical ones computed on the fly. Everything the
verdicts imply (relation edges, Ideas, profiles) and everything scoring decides is recomputed after each apply,
so the result does not depend on the order items arrive in.
"""
import re
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from typing import Iterable

from graph import cluster, embed, proposals as proposal_stage, relate
from graph.relate import Candidate, jaccard as _jaccard, words as _words
from graph.store import (ClaimNode, Edge, IdeaNode, ItemNode, PersonNode, Placement, ReviewReport, SourceNode,
                         TopicNode)
from refresher import scoring
from refresher.schemas import Extraction, Override, Relation, Verdict

REPOST_SIMILARITY = 0.8  # item text, word 3-gram Jaccard
REPOST_CLAIM_SIMILARITY = 0.6  # word-set Jaccard: a repost's claim and its match in the original
REVIEW_CLAIM_MATCH = 0.5  # word-set Jaccard for the review's item-duplicate check
REVIEW_DUPLICATE_SHARE = 0.7  # an item whose claims match one other item this much is a duplicate of it
REVIEW_MIN_CLAIMS = 3  # fewer claims are too few to call an item a duplicate; reposts and relations cover those
CONFIRM_DURABILITY = {"reference", "foundation"}  # a SUPERSEDES or CONTRADICTS onto these asks the user to confirm
WORD = relate.WORD
DURABILITY_RANK = {d: n for n, d in enumerate(("ephemeral", "news", "reference", "foundation"))}
RELATION_TYPES = {"DUPLICATES", "SUPPORTS", "SUPERSEDES", "CONTRADICTS", "ELABORATES"}
EDGE_FOR = {"SAME": "SUPPORTS", "UPDATES": "SUPERSEDES", "CONFLICTS": "CONTRADICTS", "ADDS_DETAIL": "ELABORATES"}


def person_id(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode() or name
    return "person:" + "-".join(WORD.findall(folded.casefold()))


def _same_person(a: str, b: str) -> bool:
    """"Y. Gal" and "Yarin Gal": same last name, and first names equal or one an initial of the other."""
    wa, wb = _words(a), _words(b)
    if len(wa) < 2 or len(wb) < 2 or wa == wb or wa[-1] != wb[-1]:
        return False
    fa, fb = wa[0], wb[0]
    return fa == fb or (min(len(fa), len(fb)) == 1 and fa[0] == fb[0])


def _affiliation(p: PersonNode) -> str | None:
    return p.affiliation["value"].casefold() if p.affiliation else None


def org_id(name: str) -> str:
    return "org:" + "-".join(_words(name))


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
        # Items triage chose not to keep become hidden stubs: out of every listing, but their claims can be
        # superseded, so a correction has something to replace.
        self.include_unreviewed = include_unreviewed
        self._items: dict[str, ItemNode] = {}
        self._claims: dict[str, ClaimNode] = {}
        self._ideas: dict[str, IdeaNode] = {}
        self._people: dict[str, PersonNode] = {}
        self._sources: dict[str, SourceNode] = {}
        self._orgs: dict[str, str] = {}
        self._org_facts: dict[str, set[str]] = {}
        self._entities: dict[str, frozenset[str]] = {}  # item id -> casefolded entity names, for candidates
        self._verdicts: dict[str, Verdict] = {}  # claim id -> verdict
        self._edges: list[Edge] = []  # what an item brings: source, author, mentions, reposts, spans
        self._relations: list[Edge] = []  # what the verdicts imply; recomputed by _resolve
        self._firm: set[str] = set()  # idea ids the review firmed up
        # What accepted (or rejected) proposals say; graph/proposals.py decides which apply.
        self._merges: list[tuple[str, str]] = []  # claim pairs whose Ideas the user merged
        self._suppressed: set[frozenset] = set()  # claim pairs whose relation the user rejected
        self._topic_specs: dict[str, tuple[str, list[str]]] = {}  # topic id -> (title, claim ids)
        self._topics: dict[str, TopicNode] = {}
        self._aliases: dict[str, str] = {}  # absorbed person id -> kept person id
        self.proposals: dict = {}  # proposal id -> ProposalState, filled by vault.replay
        self.held_back: dict[str, str] = {}  # item id -> reason
        # SAME verdicts of held-back claims: a verdict judged with unreviewed items in the pool may target one,
        # and a strict rebuild follows this to the placed claim it repeats.
        self._held_same: dict[str, str] = {}
        self._shingle_cache: dict[str, set] = {}  # item id -> word 3-grams of its text, for the repost check
        self._deferred = self._dirty = False  # see deferred()

    # --- writes ---

    def apply(self, extraction: Extraction, text: str = "", relation: Relation | None = None) -> Placement:
        ex, src = extraction, extraction.source
        if ex.item_id in self._items:
            return Placement(ex.item_id, "already_placed")
        skipped = ex.triage is not None and not ex.triage.keep
        if not skipped and not ex.quality_gate_passed and not self.include_unreviewed:
            self.held_back[ex.item_id] = ex.quality_status
            if relation is not None and relation.matches(ex):
                self._held_same.update({v.claim_id: v.target_id for v in relation.verdicts if v.label == "SAME"})
                self._changed()
            return Placement(ex.item_id, "held_back")

        item = ItemNode(ex.item_id, src, _title(text, src.source_name or ex.item_id), text,
                        durability=ex.triage.durability_class if ex.triage else None,
                        review_status="skipped" if skipped else "accepted" if ex.quality_gate_passed else ex.quality_status,
                        hidden=skipped)
        self._items[ex.item_id] = item
        self._entities[ex.item_id] = frozenset(e.name.casefold() for e in ex.entities)
        new_edges: list[Edge] = []

        if skipped:
            # A stub brings no source, people or mentions; the author id only decides independence.
            self.held_back[ex.item_id] = "skipped by triage"
            item.author_id = person_id(src.author_hint.name) if src.author_hint and src.author_hint.name else None
        else:
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
                    oid = org_id(ent.name)
                    self._orgs.setdefault(oid, ent.name)
                    new_edges.append(Edge(ex.item_id, "MENTIONS", oid))

        # Repost check on the whole item, ordered by event time, not arrival order.
        shingles = self._shingle_cache[ex.item_id] = _shingles(text)
        for other in self._items.values():
            if other.item_id == ex.item_id or not other.text:
                continue
            theirs = self._shingle_cache.get(other.item_id)
            if theirs is None:
                theirs = self._shingle_cache[other.item_id] = _shingles(other.text)
            if _jaccard(shingles, theirs) >= REPOST_SIMILARITY:
                first, later = sorted((item, other), key=lambda i: (i.source.event_time, i.item_id))
                later.duplicate_of = first.duplicate_of or first.item_id
                new_edges.append(Edge(later.item_id, "DUPLICATES", first.item_id,
                                      "Near-identical text; the later one is a repost."))

        if relation is None or not relation.matches(ex):
            relation = relate.judge(ex, text, self.candidate_pool(ex.item_id), relate.LexicalRelator())
        self._verdicts.update({v.claim_id: v for v in relation.verdicts})
        for claim in ex.claims:
            self._claims[claim.claim_id] = ClaimNode(claim, ex.item_id, claim.durability_class,
                                                     claim.half_life_days, src.event_time)
            new_edges += [Edge(claim.claim_id, "FROM", s.section_id) for s in claim.spans]

        self._edges += new_edges
        self._changed()
        return self.placement(ex.item_id)

    def placement(self, item_id: str) -> Placement:
        """Where an item sits in the graph now, items placed after it included: what the bot reply says."""
        item = self._items.get(item_id)
        if item is None:
            return Placement(item_id, "held_back")
        mine = [cid for cid, node in self._claims.items() if node.item_id == item_id]
        relations = [e for e in self._edges if e.type == "DUPLICATES" and item_id in (e.src, e.dst)]
        relations += [e for e in self._relations if (e.src in mine or e.dst in mine) and e.type in RELATION_TYPES]
        ideas = list(dict.fromkeys(self._claims[cid].idea_id for cid in mine if self._claims[cid].idea_id))
        return Placement(item_id, "held_back" if item.hidden else "placed", len(mine), item.author_id,
                         item.duplicate_of, relations, ideas)

    @contextmanager
    def deferred(self):
        """Apply many items and recompute the graph once at the end, not after each one: a rebuild of n items
        otherwise recomputes it n times. The result is the same, because the recomputation does not depend on
        arrival order; a placement returned inside the block may be incomplete. A lexical judgement inside the block
        (an item without stored verdicts) still sees an up-to-date graph: candidate_pool refreshes first."""
        self._deferred = True
        try:
            yield self
        finally:
            self._deferred = False
            if self._dirty:
                self._refresh()

    def _changed(self):
        if self._deferred:
            self._dirty = True
        else:
            self._refresh()

    def _refresh(self):
        self._dirty = False
        groups = self._resolve()
        self._recount_duplicates()
        self._derive()
        self._build_ideas(groups)
        self._build_topics()

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
        self._refresh()

    def _merge_person(self, absorbed: str, kept: str) -> None:
        gone, person = self._people.get(absorbed), self._people.get(kept)
        if gone is None or person is None or absorbed == kept:
            return
        self._aliases[absorbed] = kept
        person.handles |= gone.handles
        person.authored |= gone.authored
        person.mentioned_in |= gone.mentioned_in
        del self._people[absorbed]
        for item in self._items.values():
            if item.author_id == absorbed:
                item.author_id = kept
        self._edges = list(dict.fromkeys(Edge(e.src, e.type, kept if e.dst == absorbed else e.dst, e.rationale, e.at)
                                         for e in self._edges))

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
            author = item.source.author_hint.name if item.source.author_hint else None
            out.append(Candidate(cid, node.claim.statement, node.item_id, item.source.event_time,
                                 item.source.source_name, author, node.idea_id, self._entities[node.item_id]))
        return out

    def _event_time(self, node: ClaimNode):
        return self._items[node.item_id].source.event_time

    def _order(self, node: ClaimNode):
        return self._event_time(node), node.claim.claim_id

    def _independent(self, a: ClaimNode, b: ClaimNode) -> bool:
        """Provenance decides SUPPORTS: two known, different authors, and neither item a repost."""
        ia, ib = self._items[a.item_id], self._items[b.item_id]
        return (ia.author_id is not None and ib.author_id is not None and ia.author_id != ib.author_id
                and ia.duplicate_of is None and ib.duplicate_of is None)

    def _resolve(self) -> dict[str, list[str]]:
        """Turn all verdicts into relation edges, profile facts and Idea groups (root claim id -> members)."""
        self._relations = []
        self._org_facts = {}
        for person in self._people.values():
            person.facts = set()
        world: list[ClaimNode] = []
        for node in sorted(self._claims.values(), key=self._order):
            verdict = self._verdicts.get(node.claim.claim_id)
            hidden = self._items[node.item_id].hidden
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
                    self._orgs.setdefault(node.subject_id, verdict.subject_name)
                    self._org_facts.setdefault(node.subject_id, set()).add(node.claim.claim_id)
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
                    if old != new and self._order(self._claims[old]) < self._order(later) \
                            and frozenset((new, old)) not in self._suppressed:
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

    def _build_ideas(self, groups: dict[str, list[str]]):
        """One Idea per group with a visible member. Canonical: the earliest current independent claim."""
        self._ideas = {}
        for members in groups.values():
            nodes = sorted((self._claims[cid] for cid in members), key=self._order)
            for node in nodes:
                node.idea_id = None
            if all(self._items[n.item_id].hidden for n in nodes):
                continue
            independent = [n for n in nodes if n.duplicate_rank == 0] or nodes
            current = [n for n in independent if n.superseded_by is None]
            canonical = current[0] if current else independent[-1]
            idea = IdeaNode("idea:" + nodes[0].claim.claim_id[:16], canonical.claim.claim_id,
                            canonical.claim.statement,
                            max((n.durability for n in independent), key=DURABILITY_RANK.__getitem__),
                            max(self._event_time(n) for n in nodes), {n.claim.claim_id for n in nodes})
            idea.status = "firm" if idea.idea_id in self._firm else "provisional"
            self._ideas[idea.idea_id] = idea
            for node in nodes:
                node.idea_id = idea.idea_id
                self._relations.append(Edge(node.claim.claim_id, "PART_OF", idea.idea_id))

    def _build_topics(self):
        """Accepted topics, found again through the claims they were proposed with; any other Idea close to a
        topic's centroid joins it provisionally (cluster.attach), with no model call."""
        self._topics = {}
        for topic_id, (title, claims) in sorted(self._topic_specs.items()):
            ideas = {self._claims[cid].idea_id for cid in claims if cid in self._claims and self._claims[cid].idea_id}
            if ideas:
                self._topics[topic_id] = TopicNode(topic_id, title, ideas)
        if not self._topics:
            return
        centroids = {tid: cluster.centroid(embed.vectors([self._ideas[i].title for i in sorted(t.ideas)]))
                     for tid, t in self._topics.items()}
        placed = set().union(*(t.ideas for t in self._topics.values()))
        loose = sorted(i for i in self._ideas if i not in placed)
        for idea_id, vector in zip(loose, embed.vectors([self._ideas[i].title for i in loose])):
            tid = cluster.attach(vector, centroids)
            if tid is not None:
                self._topics[tid].attached.add(idea_id)

    def _recount_duplicates(self):
        dup_claims = {e.src for e in self._relations if e.type == "DUPLICATES"}
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
        for e in self._relations:
            if e.src not in self._claims or e.dst not in self._claims:
                continue
            later, first = self._claims[e.src], self._claims[e.dst]
            authors = {self._items[later.item_id].author_id, self._items[first.item_id].author_id} & self._people.keys()
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
        pid = self._aliases.get(person_id(name), person_id(name))
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

    def items(self, hidden: bool = False):
        return [i for i in self._items.values() if hidden or not i.hidden]

    def claims(self, hidden: bool = False):
        return [c for c in self._claims.values() if hidden or not self._items[c.item_id].hidden]

    def people(self):
        return self._people.values()

    def sources(self):
        return self._sources.values()

    def organizations(self):
        return dict(self._orgs)

    def edges(self):
        return self._edges + self._relations

    def verdict(self, claim_id: str) -> Verdict | None:
        return self._verdicts.get(claim_id)

    def org_facts(self) -> dict[str, set[str]]:
        return {oid: set(ids) for oid, ids in self._org_facts.items()}

    def item(self, item_id: str) -> ItemNode:
        return self._items[item_id]

    def person(self, pid: str) -> PersonNode:
        return self._people[pid]

    def ideas(self):
        return self._ideas.values()

    def idea(self, idea_id: str) -> IdeaNode:
        return self._ideas[idea_id]

    def topics(self):
        return self._topics.values()

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
        vectors = dict(zip((i.idea_id for i in ideas), embed.vectors([i.title for i in ideas])))
        related = {frozenset((self._claims[e.src].idea_id, self._claims[e.dst].idea_id))
                   for e in self._relations if e.type in {"CONTRADICTS", "ELABORATES"}
                   and e.src in self._claims and e.dst in self._claims}

        # Ideas that say the same thing in other words: the ingest relator missed them.
        for n, a in enumerate(ideas):
            for b in ideas[n + 1:]:
                if frozenset((a.idea_id, b.idea_id)) in related:
                    continue
                similar = embed.cosine(vectors[a.idea_id], vectors[b.idea_id])
                if similar >= embed.thresholds().idea_merge and (confirm is None or confirm(a.title, b.title)):
                    add("merge_ideas", {"claims": [a.canonical_claim_id, b.canonical_claim_id]},
                        f"The two statements are close (similarity {similar:.2f}) and nothing relates them yet.")

        # Topics from Ideas no topic holds yet.
        in_topics = set().union(*(t.ideas | t.attached for t in self._topics.values())) if self._topics else set()
        loose = [i for i in ideas if i.idea_id not in in_topics]
        for group in cluster.components([i.idea_id for i in loose], [vectors[i.idea_id] for i in loose]):
            members = [self._ideas[i] for i in group]
            claims = sorted(i.canonical_claim_id for i in members)
            sources = {self._items[self._claims[cid].item_id].source_key for i in members for cid in i.members
                       if not self._items[self._claims[cid].item_id].hidden} - {None}
            if len(members) < cluster.TOPIC_MIN_IDEAS or len(sources) < cluster.TOPIC_MIN_SOURCES \
                    or pending_topic_claims & set(claims):
                continue
            statements = [i.title for i in members]
            add("topic", {"claims": claims, "title": (namer or cluster.title)(statements)},
                f"{len(members)} Ideas from {len(sources)} sources sit close together.")

        # People who are probably one person: same surname, compatible first names, something in common.
        people = sorted(self._people.values(), key=lambda p: p.person_id)
        for n, a in enumerate(people):
            for b in people[n + 1:]:
                if _same_person(a.name, b.name) and ((a.authored | a.mentioned_in) & (b.authored | b.mentioned_in)
                                                     or _affiliation(a) and _affiliation(a) == _affiliation(b)):
                    absorbed, kept = sorted((a, b), key=lambda p: (len(p.name), p.person_id))
                    add("merge_people", {"people": [absorbed.person_id, kept.person_id]},
                        f"{absorbed.name} and {kept.name} share a surname and appear together.")

        # Replacing or contradicting a durable claim is live already; the user confirms or rejects it.
        for e in sorted(self._relations, key=lambda e: (e.src, e.dst)):
            if e.type in {"SUPERSEDES", "CONTRADICTS"} and e.dst in self._claims \
                    and self._claims[e.dst].durability in CONFIRM_DURABILITY:
                add("relation", {"claims": [e.src, e.dst], "type": e.type}, e.rationale or f"{e.type} by the relator.")
        return out

    # --- graph.query: lexical baseline ---

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
