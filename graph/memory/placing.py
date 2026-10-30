"""Placing an item: what it brings to the graph, and when the graph is recomputed from it."""
from contextlib import contextmanager

from graph import relate
from graph.attribution import author_name, is_named_person
from graph.relate import jaccard as _jaccard
from graph.store import Affiliation, ClaimNode, Edge, ItemNode, Placement, SourceNode
from refresher.schemas import Attribution, Extraction, Relation
from graph.memory.common import RELATION_TYPES, REPOST_SIMILARITY, _eponym, _id_hash, _shingles, _title, person_id


class Placing:
    """apply() places one extraction; the graph everything implies is recomputed after it, or once at the end
    of deferred()."""

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
            item.author_id = self.canonical_person(person_id(src.author_hint.name)) if author_name(src) else None
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
                    pid = ids[ent.entity_id] = self.canonical_person(person_id(name))  # a merged-away name: the kept one
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
        self._split_people()
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
