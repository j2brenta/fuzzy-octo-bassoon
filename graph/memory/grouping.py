"""Ideas, and the clusters, topics and links built over them."""
import hashlib

from graph import cluster, embed
from graph.store import (ClusterNode, Edge, IdeaNode, Link, TopicNode)
from graph.memory.common import (DURABILITY_RANK, LINK_RELATIONS, cluster_fingerprint)


class Grouping:
    """Ideas after each refresh; clusters, topics and links only when read (see Placing._groups)."""

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
