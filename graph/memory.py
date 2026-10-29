"""In-memory GraphStore, replayed from processed extraction records and their relation verdicts.

Used until the Neo4j store lands and kept afterwards as the test double. Relations come from verdicts
(graph/relate.py): stored ones from relations/<item_id>.json, or lexical ones computed on the fly. Everything the
verdicts imply (relation edges, Ideas, profiles) and everything scoring decides is recomputed after each apply,
so the result does not depend on the order items arrive in.
"""
import hashlib
import re
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from typing import Iterable

from graph import cluster, embed, proposals as proposal_stage, relate
from engine.entities import eponym_only
from graph.names import core as name_core, display_name, same_person as _same_person, surname_keys
from graph.attribution import author_name, is_named_person
from graph.relate import Candidate, jaccard as _jaccard, words as _words
from graph.store import (Affiliation, ClaimNode, ClusterNode, Edge, IdeaNode, ItemNode, Link, OrgNode, PersonNode,
                         Placement, ReviewReport, SourceNode, TopicNode)
from refresher import scoring
from refresher.schemas import Attribution, Extraction, Override, Relation, Verdict

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
# Claim relations that link clusters and topics. In practice ELABORATES: the others join their claims into one Idea.
LINK_RELATIONS = {"SUPPORTS", "SUPERSEDES", "CONTRADICTS", "ELABORATES"}


def cluster_fingerprint(ideas) -> str:
    """Which Ideas a cluster holds, by their canonical claims: a stored name applies while this matches."""
    return hashlib.blake2b("\n".join(sorted(i.canonical_claim_id for i in ideas)).encode(), digest_size=8).hexdigest()


def person_id(name: str) -> str:
    # "Last; First" is the same person as "First Last".
    name = display_name(name)
    # Accents fold to ASCII; a name with no Latin letters (Cyrillic) keeps its own words, else all such names share "person:".
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "person:" + "-".join(WORD.findall(folded.casefold()) or WORD.findall(name.casefold()))


def _eponym(name: str | None, text: str) -> bool:
    """A name written only inside a law or theorem in this text ("третьего закона Ньютона"): no person here. A word
    may carry a case ending, so "Ньютон" finds "Ньютона"; a name not found at all is kept."""
    if not name or not name.strip():
        return False
    return eponym_only(text, (m.span() for m in re.finditer(r"\b" + re.escape(name.strip()) + r"\w*", text)))


def _name_rank(p: PersonNode):
    """Which name a merge keeps, last: no affiliation in brackets ("Herman Daly (University of Maryland)"), more
    words, then no stray punctuation ("Krestnikov; Konstantin"), then the longer (a full first name over a short
    form), then the id."""
    name, bracket = name_core(p.name)
    return (bracket is None, len(_words(name)), -len(re.findall(r"[^\w\s.'-]", p.name)), len(p.name), p.person_id)


def _profile_pages(p: PersonNode) -> set[str]:
    """The pages enrichment cited for who they are: their role, affiliation and domains."""
    return {f["url"] for f in [p.role, p.affiliation, *p.domains] if isinstance(f, dict) and f.get("url")}


def org_id(name: str) -> str:
    return "org:" + "-".join(_words(name))


def _id_hash(item_id: str) -> int:
    return int.from_bytes(hashlib.blake2b(item_id.encode(), digest_size=8).digest(), "big")


def _shingles(text: str, n: int = 3) -> set[tuple[str, ...]]:
    w = _words(text)
    return {tuple(w[i:i + n]) for i in range(max(1, len(w) - n + 1))}


# Telegram Desktop "Copy" header, kept in raw text by capture: "Kirill R, [7 Oct 2026 at 12:13:38]:"
_TG_HEADER = re.compile(r"^.+, \[\d{1,2} \w{3} \d{4} at [\d:]+\]:$")


# Headers ingestion writes above text it read from a picture or PDF page: never a title.
_READING_HEADERS = ("Visible text (automatic OCR):", "Visual description (model-generated):")
_TITLE_FIELD = re.compile(r"^Title:\s*(.+)$")  # arXiv and similar pages state it under a breadcrumb


def _title(text: str, fallback: str) -> str:
    lines = [line.strip().lstrip("#").strip() for line in text.splitlines()]
    lines = [line for line in lines if line and not _TG_HEADER.match(line) and line not in _READING_HEADERS]
    for line in lines[:5]:
        stated = _TITLE_FIELD.match(line)
        if stated:
            return stated.group(1).strip()[:80]
    return lines[0][:80] if lines else fallback


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
        self._orgs: dict[str, OrgNode] = {}
        self._affiliations: list[Affiliation] = []  # stated in items; enrichment ones are added after replay
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
        self._clusters: dict[str, ClusterNode] = {}
        self._cluster_key = None  # what the clusters were computed from; unchanged Ideas skip the work
        self._links: list[Link] = []
        self._cluster_names: dict[str, str] = {}  # cluster fingerprint -> name a model gave it
        self._themes: dict[str, dict] = {}  # canonical claim id -> where a model placed that Idea (use_themes)
        self._grouped = True  # clusters, topics and links match the Ideas; built on first read (_groups)
        self._vectors: dict[tuple[str, str], list[float]] = {}  # (embedder, Idea title) -> vector
        self._aliases: dict[str, str] = {}  # absorbed person id -> kept person id
        self.proposals: dict = {}  # proposal id -> ProposalState, filled by vault.replay
        self.held_back: dict[str, str] = {}  # item id -> reason
        # SAME verdicts of held-back claims: a verdict judged with unreviewed items in the pool may target one,
        # and a strict rebuild follows this to the placed claim it repeats.
        self._held_same: dict[str, str] = {}
        self._shingle_cache: dict[str, set] = {}  # item id -> word 3-grams of its text, for the repost check
        self._deferred = self._dirty = False  # see deferred()
        self._placed_hash = 0  # XOR of hashes of placed item ids: an order-free fingerprint of what is placed

    # --- writes ---

    def apply(self, extraction: Extraction, text: str = "", relation: Relation | None = None, *,
              attribution_reviews: dict | None = None) -> Placement:
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
                        hidden=skipped, linked_from=(ex.parent or {}).get("item_id"))
        self._items[ex.item_id] = item
        self._placed_hash ^= _id_hash(ex.item_id)
        self._entities[ex.item_id] = frozenset(e.name.casefold() for e in ex.entities if e.kind != "person" or is_named_person(e.name))
        new_edges: list[Edge] = []

        if skipped:
            # A stub brings no source, people or mentions; the author id only decides independence.
            self.held_back[ex.item_id] = "skipped by triage"
            item.author_id = person_id(src.author_hint.name) if author_name(src) else None
        else:
            key = f"source:{src.channel}:{src.source_name or src.source_id}"
            self._sources.setdefault(key, SourceNode(key, src.source_name or src.source_id, src.channel)).items.add(ex.item_id)
            item.source_key = key
            new_edges.append(Edge(ex.item_id, "PUBLISHED_IN", key))

            if author_name(src):
                author = self._person(src.author_hint.name)
                if src.author_hint.handle:
                    author.handles.add(src.author_hint.handle)
                author.authored.add(ex.item_id)
                item.author_id = author.person_id
                new_edges.append(Edge(ex.item_id, "AUTHORED_BY", author.person_id))
            ids = {}  # entity id -> person or org id, for the affiliations
            for ent in ex.entities:
                if ent.kind == "person" and _eponym(ent.name, text):
                    continue  # records from before extraction left these out
                if ent.kind == "person" and is_named_person(ent.name) and is_named_person(ent.base_name or ent.name):
                    # The nominative form, so "Кости Крестникова" here and "Костя Крестников" elsewhere are one id.
                    name = ent.base_name or ent.name
                    pid = ids[ent.entity_id] = person_id(name)
                    if pid == item.author_id:
                        continue
                    self._person(name).mentioned_in.add(ex.item_id)
                    new_edges.append(Edge(ex.item_id, "MENTIONS", pid))
                elif ent.kind == "organization":
                    org = self._org(ent.base_name or ent.name)
                    ids[ent.entity_id] = org.org_id
                    org.mentioned_in.add(ex.item_id)
                    if ent.org_type:
                        org.types[ent.org_type] = org.types.get(ent.org_type, 0) + 1
                    new_edges.append(Edge(ex.item_id, "MENTIONS", org.org_id))
            for tie in ex.affiliations:
                if tie.person in ids and tie.organization in ids:
                    self._affiliations.append(Affiliation(ids[tie.person], ids[tie.organization], tie.relation, ex.item_id))

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
        base_names = {e.name: e.base_name or e.name for e in ex.entities}
        for verdict in relation.verdicts:
            if verdict.subject == 'person' and (not is_named_person(verdict.subject_name)
                                                or _eponym(verdict.subject_name, text)):
                verdict = verdict.model_copy(update={'subject':'world','subject_name':None})
            elif verdict.subject != 'world':
                verdict = verdict.model_copy(update={'subject_name':base_names.get(verdict.subject_name, verdict.subject_name)})
            self._verdicts[verdict.claim_id] = verdict
        from graph.attribution import validate as validate_attribution
        for claim in ex.claims:
            attribution = self._verdicts.get(claim.claim_id)
            attribution = attribution.attribution if attribution else None
            try:
                attribution = validate_attribution(attribution, src, ex.entities, claim, text)
            except ValueError:
                attribution = Attribution(kind="unknown")  # invalid provenance cannot fall back to writer credit
            review = (attribution_reviews or {}).get(claim.claim_id)
            if review:
                attribution = validate_attribution(review['attribution'], src, ex.entities, claim, text)
            self._claims[claim.claim_id] = ClaimNode(claim, ex.item_id, claim.durability_class,
                                                     claim.half_life_days, src.event_time, attribution=attribution,
                                                     speaker_name=base_names.get(attribution.person_name, attribution.person_name) if attribution else None,
                                                     attribution_reviewed=bool(review),
                                                     identity_url=review['identity_url'] if review else None,
                                                     evidence_origin=review['origin_url'] if review else None)
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
        self._grouped = False  # placement does not read clusters, topics or links: build them when asked

    def _groups(self):
        if self._dirty:
            self._refresh()
        if not self._grouped:
            self._grouped = True
            self._build_clusters()
            self._build_topics()
            self._build_links()

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

    def _idea_vectors(self, idea_ids: list[str]) -> list[list[float]]:
        """Vectors of the Ideas' titles, kept across refreshes so a new item embeds only its new Ideas."""
        name = embed.current().name
        missing = list(dict.fromkeys(t for t in (self._ideas[i].title for i in idea_ids)
                                     if (name, t) not in self._vectors))
        self._vectors.update({(name, t): v for t, v in zip(missing, embed.vectors(missing))})
        return [self._vectors[(name, self._ideas[i].title)] for i in idea_ids]

    def _idea_items(self, idea: IdeaNode) -> set[str]:
        return {self._claims[cid].item_id for cid in idea.members if not self._items[self._claims[cid].item_id].hidden}

    def _idea_sources(self, idea: IdeaNode) -> set[str]:
        return {self._items[self._claims[cid].item_id].source_key for cid in idea.members
                if not self._items[self._claims[cid].item_id].hidden} - {None}

    def _build_clusters(self):
        """Tight groups of Ideas from any number of sources (cluster_link), recomputed with the Ideas and never
        decided. Ids follow the earliest claim, so a cluster keeps its id while it grows. Ideas no cluster holds then
        go where a model placed them (use_themes): into a cluster, or into a named theme of their own."""
        ideas = sorted(self._ideas)
        key = (embed.current().name, embed.thresholds().cluster_link,
               tuple((i, self._ideas[i].title, frozenset(self._idea_sources(self._ideas[i]))) for i in ideas))
        if key == self._cluster_key:
            return
        self._cluster_key, self._clusters = key, {}
        vectors = dict(zip(ideas, self._idea_vectors(ideas)))
        for group in cluster.components(ideas, list(vectors.values()), link=embed.thresholds().cluster_link):
            if len(group) < cluster.CLUSTER_MIN_IDEAS:
                continue
            members = [self._ideas[i] for i in group]
            cid = "cluster:" + min(m.canonical_claim_id for m in members)[:16]
            centroid = cluster.centroid([vectors[i] for i in group])
            fingerprint = cluster_fingerprint(members)
            # A name a model gave this exact membership, else its most central Idea, shortened.
            name = self._cluster_names.get(fingerprint) or cluster.shorten(
                self._ideas[cluster.central(group, [vectors[i] for i in group], centroid)].title)
            self._clusters[cid] = ClusterNode(cid, name, set(group),
                                              set().union(*(self._idea_sources(m) for m in members)),
                                              centroid, fingerprint,
                                              set().union(*(self._idea_items(m) for m in members)))
        # The model's placements, by canonical claim; embedding wins: an Idea a cluster took is not placed again.
        by_claim = {self._ideas[i].canonical_claim_id: i for i in ideas}
        clustered = {i for c in self._clusters.values() for i in c.ideas}
        themes: dict[str, list[str]] = {}
        for claim_id, placed in sorted(self._themes.items()):
            idea_id = by_claim.get(claim_id)
            if idea_id is None or idea_id in clustered:
                continue
            target = self._clusters.get(placed.get("cluster") or "")
            if target is not None:
                idea = self._ideas[idea_id]
                target.ideas.add(idea_id)
                target.assigned.add(idea_id)
                target.items |= self._idea_items(idea)
                target.sources |= self._idea_sources(idea)
            elif placed.get("theme"):
                themes.setdefault(placed["theme"], []).append(idea_id)
        for name, group in sorted(themes.items()):
            members = [self._ideas[i] for i in group]
            cid = "cluster:theme-" + hashlib.blake2b(name.casefold().encode(), digest_size=6).hexdigest()
            self._clusters[cid] = ClusterNode(cid, name, set(group),
                                              set().union(*(self._idea_sources(m) for m in members)),
                                              cluster.centroid([vectors[i] for i in group]), "",
                                              set().union(*(self._idea_items(m) for m in members)), set(group))

    def use_themes(self, placements: dict[str, dict]) -> None:
        """Where a model placed Ideas no cluster holds: canonical claim id -> {"cluster": id or None, "theme": name}."""
        self._themes = dict(placements)
        self._cluster_key, self._grouped = None, False

    def use_cluster_names(self, names: dict[str, str]) -> None:
        """Names a model gave clusters, by fingerprint; clusters are named again on the next read."""
        self._cluster_names = dict(names)
        self._cluster_key, self._grouped = None, False

    def _build_topics(self):
        """Accepted topics, found again through the claims they were proposed with. A cluster mostly inside a topic
        brings its other Ideas, and any Idea close to a topic's centroid joins it (cluster.attach_all): both
        provisionally, with no model call. An Idea can sit in up to cluster.MAX_TOPICS topics."""
        self._topics = {}
        for topic_id, (title, claims) in sorted(self._topic_specs.items()):
            ideas = {self._claims[cid].idea_id for cid in claims if cid in self._claims and self._claims[cid].idea_id}
            if ideas:
                self._topics[topic_id] = TopicNode(topic_id, title, ideas)
        if not self._topics:
            return
        for c in self._clusters.values():
            for t in self._topics.values():
                if 2 * len(c.ideas & t.ideas) >= len(c.ideas):
                    t.clusters.add(c.cluster_id)
                    t.attached |= c.ideas - t.ideas
        centroids = {tid: cluster.centroid(self._idea_vectors(sorted(t.ideas))) for tid, t in self._topics.items()}
        ideas = sorted(self._ideas)
        for idea_id, vector in zip(ideas, self._idea_vectors(ideas)):
            held = {tid for tid, t in self._topics.items() if idea_id in t.ideas or idea_id in t.attached}
            room = cluster.MAX_TOPICS - len(held)
            if room > 0:
                for tid in cluster.attach_all(vector, {t: c for t, c in centroids.items() if t not in held},
                                              limit=room):
                    self._topics[tid].attached.add(idea_id)

    def _build_links(self):
        """Links between clusters and between topics: claim relations whose ends sit one in each (not in both),
        Ideas they share, and centroids at topic_neighbour or closer. Recomputed with the graph."""
        self._links = []
        idea_of = {cid: n.idea_id for cid, n in self._claims.items() if n.idea_id}
        crossing = [e for e in self._relations
                    if e.type in LINK_RELATIONS and e.src in idea_of and e.dst in idea_of]
        topic_centroids = {tid: cluster.centroid(self._idea_vectors(sorted(t.ideas | t.attached)))
                           for tid, t in self._topics.items()}
        levels = (("cluster", {c.cluster_id: c.ideas for c in self._clusters.values()},
                   {c.cluster_id: c.centroid for c in self._clusters.values()}),
                  ("topic", {t.topic_id: t.ideas | t.attached for t in self._topics.values()}, topic_centroids))
        for level, groups, centroids in levels:
            ids = sorted(groups)
            member_of: dict[str, set[str]] = {}
            for gid in ids:
                for idea_id in groups[gid]:
                    member_of.setdefault(idea_id, set()).add(gid)
            relations: dict[tuple[str, str], set] = {}
            for e in crossing:
                a, b = idea_of[e.src], idea_of[e.dst]
                for ga in member_of.get(a, ()):
                    for gb in member_of.get(b, ()):
                        if ga != gb and b not in groups[ga] and a not in groups[gb]:
                            relations.setdefault(tuple(sorted((ga, gb))), set()).add((e.src, e.type, e.dst))
            similar = {(ids[i], ids[j]): sim for i, j, sim in
                       cluster.pairs_above([centroids[g] for g in ids], embed.thresholds().topic_neighbour)}
            overlapping = {(a, b) for n, a in enumerate(ids) for b in ids[n + 1:]
                           if groups[a] & groups[b]} if level == "topic" else set()
            pairs = set(relations) | set(similar) | overlapping
            for a, b in pairs:
                sim = similar.get((a, b), embed.cosine(centroids[a], centroids[b]))
                rel, shared = tuple(sorted(relations.get((a, b), ()))), tuple(sorted(groups[a] & groups[b]))
                self._links.append(Link(level, a, b, round(len(rel) + len(shared) + sim, 3), round(sim, 3),
                                        rel, shared))
        self._links.sort(key=lambda link: (link.level, -link.weight, link.a, link.b))

    def _recount_duplicates(self):
        dup_claims = {e.src for e in self._relations if e.type == "DUPLICATES"}
        for node in self._claims.values():
            reposted = self._items[node.item_id].duplicate_of is not None
            node.duplicate_rank = 1 if reposted or node.claim.claim_id in dup_claims else 0

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
            if _words(name_a) == _words(name_b):
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

    def experts(self, at: datetime, subject_id: str | None = None, *,
                idea_ids: Iterable[str] | None = None, subject_title: str | None = None) -> list[dict]:
        """Importance in this collection, scoped to an accepted topic or provisional cluster."""
        from refresher.people_scoring import score_people
        return score_people(self, at, subject_id, idea_ids=idea_ids, subject_title=subject_title)
