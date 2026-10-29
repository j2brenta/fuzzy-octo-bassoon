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
    assert "## Start here" in first and "## Threads" in first and "## The current view" in first and "[[" in first
    assert first.index("## Start here") < first.index("## Threads") < first.index("## The current view")
    assert "[[Hallucination detection (dossier)]]" in (v / "wiki" / "topics" / "Hallucination detection (topic).md").read_text()
    assert "[[Hallucination detection (dossier)]]" in (v / "wiki" / "index.md").read_text()
    vault.export(vault.replay(v, include_unreviewed=True), v, AT)
    assert note.read_text() == first
    later = vault.replay(v, include_unreviewed=True)
    vault.export(later, v, AT + timedelta(days=400))  # a year on, the news claims have faded
    assert "fading" in note.read_text() or "noise" in note.read_text()


# --- the reading map: sources first ---

def _scope_all(store, title="subject"):
    ideas = sorted({c.idea_id for c in store.claims() if c.idea_id})
    return dossier.Scope("query", title, title, ideas, {i: 1.0 for i in ideas})


def _map_store():
    """A survey covering three Ideas, a post repeating one of them, and a note adding one more."""
    store = MemoryStore()
    store.apply(item("survey", 10, [("a1", "Semantic entropy flags made-up answers."),
                                    ("a2", "Token probability is a weak hallucination signal."),
                                    ("a3", "Entropy probes need one forward pass.")], author="Ann Lee",
                     entities=[("Bo Chen", "person")]), "", verdicts("survey", *[(c, "NONE", None) for c in ("a1", "a2", "a3")]))
    store.apply(item("echo", 11, [("b1", "Semantic entropy flags made-up answers.")], author="Bo Chen"), "",
                verdicts("echo", ("b1", "SAME", "a1")))
    store.apply(item("note", 12, [("c1", "Retrieval grounding lowers hallucination rates.")], author="Cy Ito"), "",
                verdicts("note", ("c1", "NONE", None)))
    return store


def test_sources_rank_by_what_they_add_and_the_first_is_the_key_one():
    store = _map_store()
    d = dossier.build(store, _scope_all(store), AT)
    docs = {x["id"]: x for x in d["documents"]}
    assert [x["id"] for x in d["documents"]] == ["survey", "note", "echo"]
    assert [x["rank"] for x in d["documents"]] == [1, 2, 3]
    assert docs["survey"]["key"] and not docs["note"]["key"]  # the note adds one Idea of four: under half as much
    assert docs["echo"]["gain"] == 0 and docs["echo"]["weight"] > 0  # all it says, the survey said first
    assert docs["survey"]["summary"] == "lead" and docs["survey"]["hook"] and docs["survey"]["minutes"] >= 1
    assert sorted(i for x in d["documents"] for i in x["ideas"]) == sorted(r["id"] for r in d["ideas"] for _ in r["items"])
    assert sum(len(t["documents"]) for t in d["threads"]) == len(d["documents"])


def test_a_repost_folds_into_its_original():
    store = _map_store()
    store.item("echo").duplicate_of = "survey"
    d = dossier.build(store, _scope_all(store), AT)
    assert [x["id"] for x in d["documents"]] == ["survey", "note"]
    assert d["documents"][0]["reposts"] == ["echo"]


def test_a_correction_updates_the_source_it_corrects_and_a_conflict_marks_both():
    store = MemoryStore()
    store.apply(item("old", 10, [("a1", "The library supports Python 3.8 and later.")], author="Ann Lee"), "",
                verdicts("old", ("a1", "NONE", None)))
    store.apply(item("new", 20, [("b1", "The library now requires Python 3.11 or later.")], author="Bo Chen"), "",
                verdicts("new", ("b1", "UPDATES", "a1")))
    docs = {x["id"]: x for x in dossier.build(store, _scope_all(store), AT)["documents"]}
    assert [(b["kind"], b["item"]) for b in docs["new"]["badges"]] == [("updates", "old")]
    assert [(b["kind"], b["item"]) for b in docs["old"]["badges"]] == [("outdated_by", "new")]

    store = MemoryStore()
    store.apply(item("a", 20, [("a1", "The conference keynote is on Tuesday.")], author="Ann Lee"), "",
                verdicts("a", ("a1", "NONE", None)))
    store.apply(item("b", 21, [("b1", "The conference keynote is on Wednesday.")], author="Bo Chen"), "",
                verdicts("b", ("b1", "CONFLICTS", "a1")))
    d = dossier.build(store, _scope_all(store), AT)
    docs = {x["id"]: x for x in d["documents"]}
    assert [(b["kind"], b["item"]) for b in docs["a"]["badges"]] == [("disagrees", "b")]
    assert [(b["kind"], b["item"]) for b in docs["b"]["badges"]] == [("disagrees", "a")]
    assert [(e["kind"], e["a"], e["b"]) for e in d["people"]["edges"]] == \
        [("disagrees", "person:ann-lee", "person:bo-chen")]


def test_a_cluster_is_one_thread_and_a_topic_threads_by_cluster(tmp_path):
    store, topic = accepted_topic(topical_vault(tmp_path), ["h1", "h2", "h3", "h4", "m1", "m2"])
    d = dossier.build(store, dossier.resolve(store, topic.topic_id), AT)
    clusters = {c.cluster_id for c in store.clusters()}
    assert {t["id"] for t in d["threads"]} <= clusters | {"other"}
    assert sorted(i for t in d["threads"] for i in t["documents"]) == sorted(x["id"] for x in d["documents"])
    first = {t["id"]: min(docs.index(i) for i in t["documents"]) for docs in [[x["id"] for x in d["documents"]]]
             for t in d["threads"]}
    assert list(first.values()) == sorted(first.values())  # the thread of the best source comes first
    cluster = next(iter(store.clusters()))
    one = dossier.build(store, dossier.resolve(store, cluster.cluster_id), AT)
    assert [t["id"] for t in one["threads"] if t["ring"] == "core"] == [cluster.cluster_id]


def test_people_rank_by_what_they_wrote_with_their_organizations_and_ties():
    store = _map_store()
    for name in ("Di Roy", "Ed Fox", "Fay Wu"):
        store.apply(item(name.split()[0].lower(), 13, [(name[:2], f"{name} reports hallucination rates fall with retrieval.")],
                         author=name), "", verdicts(name.split()[0].lower(), (name[:2], "SAME", "c1")))
    store.add_enrichment("person:ann-lee", {"value": "Aldmoor University", "url": "https://aldmoor.example/ann"})
    store.person("person:ann-lee").role = {"value": "Associate professor", "url": "https://aldmoor.example/ann"}
    store.person("person:ann-lee").domains = [{"value": "uncertainty estimation", "url": "https://aldmoor.example/ann"}]
    d = dossier.build(store, _scope_all(store), AT)
    rows = d["people"]["people"]
    assert rows[0]["id"] == "person:ann-lee" and rows[0]["reason"].startswith("wrote ★")
    assert rows[0]["role"]["value"] == "Associate professor" and rows[0]["domains"][0]["value"] == "uncertainty estimation"
    assert [p["top"] for p in rows].count(True) == dossier.TOP_PEOPLE < len(rows)
    org = d["people"]["orgs"][0]
    assert org["name"] == "Aldmoor University" and org["main"] == ["person:ann-lee"] and rows[0]["org"] == org["id"]
    assert rows[0]["ties"][0]["label"] == "affiliated with" and rows[0]["ties"][0]["item"] is None
    bo = next(p for p in rows if p["id"] == "person:bo-chen")
    assert bo["wrote"] == ["echo"] and bo["named"] == ["survey"]
    assert {"a": "person:ann-lee", "b": "person:bo-chen", "kind": "same_document", "documents": ["survey"]} \
        in d["people"]["edges"]


def test_the_reader_gets_the_text_with_each_claim_and_its_idea():
    store = trio()
    src = dossier.document(store, "correction")
    assert src["text"].startswith("Correction:") and src["translation"] == []
    assert {s["claim"] for s in src["spans"]} == {"c16", "cprev"} and all(s["idea"] for s in src["spans"])
    assert dossier.document(store, "nope") is None
    assert dossier.document(store, "original") is None  # a triage-skipped stub has no page


def test_a_stored_summary_replaces_the_lead_sentences():
    store = _map_store()
    store.summaries = {"survey": {"hook": "A survey of uncertainty signals.", "gist": "It compares three signals."}}
    docs = {x["id"]: x for x in dossier.build(store, _scope_all(store), AT)["documents"]}
    assert (docs["survey"]["hook"], docs["survey"]["gist"], docs["survey"]["summary"]) == \
        ("A survey of uncertainty signals.", "It compares three signals.", "model")
    assert docs["note"]["summary"] == "lead"


def test_every_source_is_in_a_ring_and_the_rings_come_in_order(tmp_path):
    store, topic = accepted_topic(topical_vault(tmp_path), ["h1", "h2", "h3", "h4", "m1", "m2"])
    d = dossier.build(store, dossier.resolve(store, topic.topic_id), AT)
    docs = {x["id"]: x for x in d["documents"]}
    assert set(docs) == {i.item_id for i in store.items()}  # nothing vanishes: the GPU posts are there, outside
    assert {i for i, x in docs.items() if x["ring"] == "core"} == {f"item-{c}" for c in ("h1", "h2", "h3", "h4", "m1", "m2")}
    assert d["rings"] == {ring: sum(x["ring"] == ring for x in d["documents"]) for ring in dossier.RINGS}
    assert [dossier.RINGS.index(x["ring"]) for x in d["documents"]] == \
        sorted(dossier.RINGS.index(x["ring"]) for x in d["documents"])
    assert docs["item-g1"]["ideas"] == [] and docs["item-g1"]["other_ideas"] and docs["item-g1"]["new_ideas"] == 1
    core = [x for x in d["documents"] if x["ring"] == "core"]
    assert core[0]["key"] and core[0]["deepest"] and sum(x["deepest"] for x in core) == len({x["thread"] for x in core})
    assert not any(x["key"] for x in d["documents"] if x["ring"] != "core")


def test_a_source_relating_to_the_core_is_adjacent():
    store = MemoryStore()
    store.apply(item("a", 10, [("a1", "Semantic entropy flags made-up answers.")], author="Ann Lee"), "",
                verdicts("a", ("a1", "NONE", None)))
    store.apply(item("b", 11, [("b1", "Cooking pasta needs salted water.")], author="Bo Chen"), "",
                verdicts("b", ("b1", "NONE", None)))
    store.apply(item("c", 12, [("c1", "Benchmarks put its recall at 0.79 on biography questions.")],
                     author="Cy Ito"), "", verdicts("c", ("c1", "ADDS_DETAIL", "a1")))  # its own Idea, related
    a1 = next(c.idea_id for c in store.claims() if c.claim.claim_id == "a1")
    d = dossier.build(store, dossier.Scope("query", "entropy", "entropy", [a1], {a1: 1.0}), AT)
    rings = {x["id"]: x["ring"] for x in d["documents"]}
    assert rings["a"] == "core" and rings["b"] == "periphery"
    assert rings["c"] == "adjacent"
    assert [x["id"] for x in d["documents"]] == ["a", "c", "b"]
