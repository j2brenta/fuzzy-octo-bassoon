from datetime import timedelta

from graph.memory import MemoryStore
from refresher import dossier, vault
from refresher.schemas import Override
from tests.test_proposals import AT, propose, token, topical_vault, write
from tests.test_relate import item, trio, verdicts


def accepted_topic(v, claims, title="Hallucination detection"):
    p = propose(v, "topic", {"claims": claims, "title": title})
    vault.decide(v, p.proposal_id, "accept", token(v, p.proposal_id), "yes", at=AT)
    store = vault.replay(v, include_unreviewed=True)
    return store, next(t for t in store.topics() if t.title == title)


def test_a_topic_ranks_ideas_more_sources_say_above_ideas_one_source_says(tmp_path):
    v = topical_vault(tmp_path)
    merge = propose(v, "merge_ideas", {"claims": ["m1", "m2"]})  # two authors, one assertion
    vault.decide(v, merge.proposal_id, "accept", token(v, merge.proposal_id), "same", at=AT)
    store, topic = accepted_topic(v, ["h1", "h2", "h3", "h4", "m1", "m2"])
    d = dossier.build(store, dossier.resolve(store, topic.topic_id), AT)
    assert d["scope"] == {"kind": "topic", "id": topic.topic_id, "title": "Hallucination detection"}
    first = d["ideas"][0]
    assert set(first["claims"]) == {"m1", "m2"} and first["sources"] == 2 and first["tier"] == "core"
    assert first["parts"]["support"] > d["ideas"][1]["parts"]["support"]
    assert "2 independent sources" in first["why"]
    assert [r["rank"] for r in d["ideas"]] == list(range(1, len(d["ideas"]) + 1))
    # the GPU claims are another subject: not in this dossier
    assert not {"g1", "g2", "g3"} & {c for r in d["ideas"] for c in r["claims"]}


def test_the_parts_of_importance_are_kept_and_add_up(tmp_path):
    store, topic = accepted_topic(topical_vault(tmp_path), ["h1", "h2", "h3", "h4", "m1", "m2"])
    for r in dossier.build(store, dossier.resolve(store, topic.topic_id), AT)["ideas"]:
        p = r["parts"]
        expected = p["fresh"] * sum(dossier.WEIGHTS[k] * p[k] for k in dossier.WEIGHTS) + p["user"]
        assert abs(r["importance"] - expected) < 0.01
        assert p["central"] == 1.0  # every Idea here was accepted with the topic


def test_a_correction_is_what_changed_and_the_newest_word_is_read_first():
    store = trio()
    d = dossier.build(store, dossier.resolve(store, query="demo workshop"), AT)
    assert [[s["claim"] for s in c["steps"]] for c in d["changed"]] == [["o14", "c16"]]  # told once
    assert d["changed"][0]["steps"][-1]["statement"].endswith("16:00 UTC on 25 October 2026.")
    assert d["reading"][0]["item"] == "correction"
    assert "the newest word on what changed" in d["reading"][0]["reason"]
    assert [r["item"] for r in d["also"]] == ["repost"]


def test_an_idea_that_changed_shows_what_it_said_before_and_ranks_by_the_newer_claim():
    store = MemoryStore()
    old = item("old", 10, [("a1", "The library supports Python 3.8 and later.")], author="Ann Lee")
    new = item("new", 20, [("b1", "The library now requires Python 3.11 or later.")], author="Bo Chen")
    store.apply(old, "", verdicts("old", ("a1", "NONE", None)))
    store.apply(new, "", verdicts("new", ("b1", "UPDATES", "a1")))
    idea = next(c.idea_id for c in store.claims() if c.claim.claim_id == "b1")
    d = dossier.build(store, dossier.Scope("query", "python", "python", [idea], {idea: 1.0}), AT)
    row = d["ideas"][0]
    assert row["current"] == ["b1"] and row["tier"] == "core"
    assert [[s["claim"] for s in steps] for steps in row["was"]] == [["a1"]]
    assert [[s["claim"] for s in c["steps"]] for c in d["changed"]] == [["a1", "b1"]]
    assert row["parts"]["fresh"] > 0.5  # the newer claim's freshness, not the superseded one's zero


def test_your_vote_moves_an_idea():
    store = trio()
    idea = next(c.idea_id for c in store.claims() if c.claim.claim_id == "c16")
    scope = dossier.Scope("query", "workshop", "workshop", [idea], {idea: 1.0})
    before = dossier.build(store, scope, AT)["ideas"][0]
    assert before["tier"] == "core"  # the correction joined the Idea, so the Idea itself is current
    store.apply_override(Override(target="claim", target_id="c16", vote=-1))
    after = dossier.build(store, scope, AT)["ideas"][0]
    assert after["parts"]["user"] == -dossier.VOTE and after["importance"] < before["importance"]
    assert "-1 from you" in after["why"]


def test_a_conflict_is_contested_and_an_expired_ephemeral_claim_is_noise():
    store = MemoryStore()
    store.apply(item("a", 20, [("a1", "The conference keynote is on Tuesday.")], author="Ann Lee"), "",
                verdicts("a", ("a1", "NONE", None)))
    store.apply(item("b", 21, [("b1", "The conference keynote is on Wednesday.")], author="Bo Chen"), "",
                verdicts("b", ("b1", "CONFLICTS", "a1")))
    coffee = item("c", 1, [("c1", "The conference coffee runs out at noon.")], durability="ephemeral")
    coffee.claims[0].half_life_days = 7
    store.apply(coffee, "", verdicts("c", ("c1", "NONE", None)))
    ideas = sorted({c.idea_id for c in store.claims()})
    d = dossier.build(store, dossier.Scope("query", "conference", "conference", ideas, {}), AT)
    assert [(p["a"]["claim"], p["b"]["claim"]) for p in d["contested"]] == [("b1", "a1")]
    assert next(r for r in d["ideas"] if "a1" in r["claims"])["contested"] == 1
    tiers = {r["claims"][0]: r["tier"] for r in d["ideas"]}
    assert tiers["c1"] == "noise" and d["counts"]["noise"] == 1
    assert d["ideas"][-1]["claims"] == ["c1"]  # noise comes last
    assert "c" not in [r["item"] for r in d["reading"]]  # nothing worth reading there


def test_a_phrase_finds_ideas_and_an_unrelated_phrase_finds_nothing(tmp_path):
    store = vault.replay(topical_vault(tmp_path), include_unreviewed=True)
    scope = dossier.resolve(store, query="GPU rental prices")
    claims = {c.claim.claim_id for c in store.claims() if c.idea_id in scope.ideas}
    assert {"g1", "g2"} <= claims and not {"h1", "h2"} & claims
    assert dossier.resolve(store, query="medieval falconry") is None
    assert dossier.resolve(store, query="  ") is None


def test_a_topic_title_typed_as_a_phrase_opens_the_topic(tmp_path):
    store, topic = accepted_topic(topical_vault(tmp_path), ["h1", "h2", "h3"])
    scope = dossier.resolve(store, query="hallucination DETECTION")
    assert (scope.kind, scope.key) == ("topic", topic.topic_id)


def test_rebuild_writes_a_dossier_note_per_topic_and_the_same_bytes_twice(tmp_path):
    v = topical_vault(tmp_path)
    store, topic = accepted_topic(v, ["h1", "h2", "h3", "h4", "m1", "m2"])
    vault.export(store, v, AT)
    note = v / "wiki" / "dossiers" / "Hallucination detection (dossier).md"
    first = note.read_text()
    assert "## The current view" in first and "## Read in order" in first and "[[" in first
    assert "[[Hallucination detection (dossier)]]" in (v / "wiki" / "topics" / "Hallucination detection (topic).md").read_text()
    assert "[[Hallucination detection (dossier)]]" in (v / "wiki" / "index.md").read_text()
    vault.export(vault.replay(v, include_unreviewed=True), v, AT)
    assert note.read_text() == first
    later = vault.replay(v, include_unreviewed=True)
    vault.export(later, v, AT + timedelta(days=400))  # a year on, the news claims have faded
    assert "fading" in note.read_text() or "noise" in note.read_text()
