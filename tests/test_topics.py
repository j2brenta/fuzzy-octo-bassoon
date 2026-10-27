"""Clusters, topics made of clusters, overlapping membership and links, with vectors chosen by hand."""
import math

import pytest

from graph import cluster, embed
from refresher import vault
from tests.test_proposals import AT, by_kind, review, token, write
from tests.test_relate import item, verdicts

DIMENSIONS = 12


def unit(*pairs):
    """A unit vector from (axis, weight) pairs."""
    v = [0.0] * DIMENSIONS
    for axis, weight in pairs:
        v[axis] += weight
    norm = math.sqrt(sum(x * x for x in v))
    return [x / norm for x in v]


# Article A: two sub-themes, {a1, a2} and {a3, a4, a5}. Article B: one, {b1, b2}, near A's first at 0.64 (a1-b1):
# above topic_link (0.6), below cluster_link (0.7). Statements are single distinct words, so no lexical relations.
VECTORS = {
    "alfa": unit((0, 1)), "amber": unit((0, 1), (4, 0.6)),
    "anvil": unit((2, 1)), "aspen": unit((2, 1), (6, 0.6)), "atlas": unit((2, 1), (7, 0.6)),
    "bravo": unit((0, 1), (1, 1.2)), "basil": unit((0, 1), (1, 1.2), (5, 0.6)),
    "cedar": unit((0, 1), (1, 1.2), (8, 0.6)),
}
ARTICLE_A = item("article-a", 10, [("a1", "alfa"), ("a2", "amber"), ("a3", "anvil"), ("a4", "aspen"), ("a5", "atlas")])
ARTICLE_B = item("article-b", 12, [("b1", "bravo"), ("b2", "basil")], author="Bo Chen")
ARTICLE_C = item("article-c", 14, [("c1", "cedar")], author="Cy Ito")


class Axes:
    """An embedder that returns the vectors above and records what it was asked to embed."""
    name = "axes"
    thresholds = embed.QWEN3

    def __init__(self):
        self.asked: list[str] = []

    def embed(self, texts):
        self.asked += texts
        return [VECTORS.get(t, unit((DIMENSIONS - 1, 1))) for t in texts]


@pytest.fixture
def axes():
    embedder = Axes()
    embed.use(embedder)
    return embedder


def ideas_of(store, *claim_ids):
    return {c.idea_id for c in store.claims() if c.claim.claim_id in claim_ids}


def test_one_article_splits_into_clusters_and_proposes_no_topic(tmp_path, axes):
    v = write(tmp_path, ARTICLE_A)
    store = vault.replay(v)
    assert sorted(sorted(c.ideas) for c in store.clusters()) == sorted(
        [sorted(ideas_of(store, "a1", "a2")), sorted(ideas_of(store, "a3", "a4", "a5"))])
    assert all(c.sources == {"source:telegram:article-a"} for c in store.clusters())
    assert "topic" not in by_kind(review(v))  # one source: clusters, not a topic


def test_clusters_from_two_articles_form_one_topic_above_both(tmp_path, axes):
    v = write(tmp_path, ARTICLE_A, ARTICLE_B)
    store = vault.replay(v)
    assert len(store.clusters()) == 3
    seen = []
    [topic] = by_kind(review(v, namer=lambda statements: seen.append(statements) or "Training"))["topic"]
    assert topic.payload == {"claims": ["a1", "a2", "b1", "b2"], "title": "Training"}
    assert "in 2 clusters from 2 sources" in topic.rationale
    # The namer reads one statement from each cluster first.
    assert {seen[0][0], seen[0][1]} in ({"alfa", "bravo"}, {"alfa", "basil"}, {"amber", "bravo"}, {"amber", "basil"})


def test_an_accepted_topic_is_found_again_and_a_cluster_inside_it_brings_new_ideas(tmp_path, axes):
    v = write(tmp_path, ARTICLE_A, ARTICLE_B)
    [p] = by_kind(review(v))["topic"]
    vault.decide(v, p.proposal_id, "accept", token(v, p.proposal_id), at=AT)
    write(v, ARTICLE_C)  # c1 joins B's cluster at 0.93
    store = vault.replay(v)
    [topic] = store.topics()
    assert topic.ideas == ideas_of(store, "a1", "a2", "b1", "b2")
    assert ideas_of(store, "c1") <= topic.attached
    assert len(topic.clusters) == 2
    assert "topic" not in by_kind(review(v))  # what the topic holds is not proposed again


def test_an_idea_close_to_two_topics_sits_in_both(tmp_path, axes):
    v = write(tmp_path, ARTICLE_A, ARTICLE_B)
    first = [VECTORS[t] for t in ("alfa", "amber", "bravo", "basil")]
    second = [VECTORS[t] for t in ("anvil", "aspen", "atlas")]
    VECTORS["delta"] = cluster.centroid([cluster.centroid(first), cluster.centroid(second)])
    try:
        for claims, title in ((["a1", "a2", "b1", "b2"], "Training"), (["a3", "a4", "a5"], "Teenagers")):
            p = test_proposals_make(v, claims, title)
            vault.decide(v, p.proposal_id, "accept", token(v, p.proposal_id), at=AT)
        write(v, item("article-d", 16, [("d1", "delta")], author="Di Roy"))
        store = vault.replay(v)
        d1 = ideas_of(store, "d1")
        assert all(d1 <= t.attached for t in store.topics())
        topics = {t.title: t.topic_id for t in store.topics()}
        [link] = store.links("topic")
        assert {link.a, link.b} == set(topics.values()) and link.shared == tuple(sorted(d1))
    finally:
        del VECTORS["delta"]


def test_proposals_make(v, claims, title):
    """Helper (not a test on its own when called with arguments): store a topic proposal for these claims."""
    from graph import proposals
    store = vault.replay(v, include_unreviewed=True)
    p = proposals.make(store, "topic", {"claims": claims, "title": title}, "test", AT)
    vault.save_proposal(v, p)
    return p


test_proposals_make.__test__ = False


def test_links_carry_crossing_relations_and_close_centroids(tmp_path, axes):
    v = write(tmp_path, ARTICLE_A, ARTICLE_B)
    vault.save_relation(v, verdicts("article-b", ("b1", "ADDS_DETAIL", "a3"), ("b2", "NONE", None)))
    store = vault.replay(v)
    cluster_of = {i: c.cluster_id for c in store.clusters() for i in c.ideas}
    [b1], [a1], [a3] = (ideas_of(store, cid) for cid in ("b1", "a1", "a3"))
    links = {frozenset((link.a, link.b)): link for link in store.links("cluster")}
    across = links[frozenset((cluster_of[b1], cluster_of[a3]))]
    assert across.relations == (("b1", "ELABORATES", "a3"),)
    assert across.weight >= 1
    near = links[frozenset((cluster_of[b1], cluster_of[a1]))]  # no relation, centroids at 0.61
    assert near.relations == () and near.similarity >= embed.QWEN3.topic_neighbour
    assert frozenset((cluster_of[a1], cluster_of[a3])) not in links  # nothing in common


def test_a_refresh_without_new_ideas_embeds_nothing(tmp_path, axes):
    v = write(tmp_path, ARTICLE_A, ARTICLE_B)
    store = vault.replay(v)
    list(store.clusters())
    axes.asked.clear()
    store._refresh()
    list(store.clusters())
    assert axes.asked == []
    known = set(store._vectors)
    write(v, ARTICLE_C)
    store.apply(*vault.load_extractions(v)[-1])
    list(store.clusters())
    assert set(store._vectors) - known == {("axes", "cedar")}  # only the new Idea's title


def test_attach_all_returns_every_close_topic_closest_first_up_to_the_limit():
    centroids = {"t1": unit((0, 1)), "t2": unit((0, 1), (1, 0.5)), "t3": unit((1, 1))}
    assert cluster.attach_all(unit((0, 1), (1, 0.2)), centroids, threshold=0.6) == ["t1", "t2"]
    assert cluster.attach_all(unit((0, 1), (1, 0.2)), centroids, threshold=0.6, limit=1) == ["t1"]
    assert cluster.attach(unit((2, 1)), centroids, threshold=0.6) is None


def test_components_never_split_a_joined_group():
    vectors = [unit((0, 1)), unit((1, 1)), unit((2, 1))]
    assert cluster.components(["x", "y", "z"], vectors, link=0.5) == [["x"], ["y"], ["z"]]
    assert cluster.components(["x", "y", "z"], vectors, link=0.5, joined=[["x", "z"]]) == [["x", "z"], ["y"]]


def test_map_wiki_and_health_show_clusters_topics_and_links(tmp_path, axes):
    from refresher import graphview, km
    v = write(tmp_path, ARTICLE_A, ARTICLE_B)
    [p] = by_kind(review(v))["topic"]
    vault.decide(v, p.proposal_id, "accept", token(v, p.proposal_id), at=AT)
    store = vault.replay(v)
    data = graphview.map_data(store, AT)
    assert data["counts"]["clusters"] == 3 and len(data["clusters"]) == 3
    [topic] = data["topics"]
    assert len(topic["clusters"]) == 2
    assert {"cluster", "topic"} >= {link["level"] for link in data["links"]} and data["links"]
    assert all(s["cluster"] for s in data["stories"])

    first = {path: path.read_bytes() for path in vault.export(store, v, AT)}
    again = {path: path.read_bytes() for path in vault.export(vault.replay(v), v, AT)}
    assert first == again
    [note] = [path for path in first if path.parent.name == "topics"]
    text = note.read_text()
    assert "## Clusters" in text and "[[cluster-" in text
    idea_note = (v / "wiki" / "ideas" / "idea-a1.md").read_text()
    assert "## In" in idea_note and "(topic)]]" in idea_note
    assert len([path for path in first if path.parent.name == "clusters"]) == 3
    health = km.render(km.run(store, AT), store)
    assert "3 clusters (1 from one source, waiting for a second)" in health
