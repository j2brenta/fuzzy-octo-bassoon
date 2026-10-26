import json
from datetime import datetime, timezone

import pytest

from graph import proposals
from refresher import vault
from tests.test_relate import CORRECTION, ORIGINAL, REPOST, TEXT, TRIO_VERDICTS, item

AT = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
ALPHA = item("alpha", 10, [("a1", "Semantic entropy detects confabulations in language models.")], author="Ann Lee")
BETA = item("beta", 12, [("b1", "Confabulations can be caught by measuring entropy over meanings.")], author="Bo Chen")


def write(path, *records, texts=None):
    for ex in records:
        (path / "processed").mkdir(parents=True, exist_ok=True)
        (path / "raw").mkdir(exist_ok=True)
        (path / "processed" / f"{ex.item_id}.json").write_text(ex.model_dump_json())
        text = (texts or {}).get(ex.item_id, " ".join(c.statement for c in ex.claims))
        (path / "raw" / f"{ex.item_id}.json").write_text(json.dumps({"text": text}))
    return path


def propose(v, kind, payload, rationale="test"):
    store = vault.replay(v, include_unreviewed=True)
    p = proposals.make(store, kind, payload, rationale, AT)
    vault.save_proposal(v, p)
    return p


def token(v, pid):
    return vault.replay(v, include_unreviewed=True).proposals[pid].token


def idea_of(store, cid):
    return next(c.idea_id for c in store.claims() if c.claim.claim_id == cid)


def test_an_accepted_merge_joins_two_ideas_and_replays(tmp_path):
    v = write(tmp_path, ALPHA, BETA)
    before = vault.replay(v)
    assert idea_of(before, "a1") != idea_of(before, "b1")
    p = propose(v, "merge_ideas", {"claims": ["a1", "b1"]})
    assert vault.replay(v).proposals[p.proposal_id].status == "proposed"

    vault.decide(v, p.proposal_id, "accept", token(v, p.proposal_id), "same method", at=AT)
    after = vault.replay(v)
    assert after.proposals[p.proposal_id].status == "accepted"
    assert idea_of(after, "a1") == idea_of(after, "b1")
    assert len(after.ideas()) == 1


def test_a_wrong_or_used_token_is_refused(tmp_path):
    v = write(tmp_path, ALPHA, BETA)
    p = propose(v, "merge_ideas", {"claims": ["a1", "b1"]})
    with pytest.raises(vault.StaleToken):
        vault.decide(v, p.proposal_id, "accept", "not-the-token")
    seen = token(v, p.proposal_id)
    vault.decide(v, p.proposal_id, "reject", seen, at=AT)
    with pytest.raises(vault.StaleToken):  # someone decided after this token was handed out
        vault.decide(v, p.proposal_id, "accept", seen)
    assert len(vault.load_decisions(v)) == 1


def test_a_re_extraction_makes_the_proposal_stale_and_the_merge_does_not_apply(tmp_path):
    v = write(tmp_path, ALPHA, BETA)
    p = propose(v, "merge_ideas", {"claims": ["a1", "b1"]})
    seen = token(v, p.proposal_id)
    vault.decide(v, p.proposal_id, "accept", seen, at=AT)

    changed = BETA.model_copy(deep=True)
    changed.claims[0].statement = "Confabulations are rare in small models."
    write(v, changed)
    store = vault.replay(v)
    state = store.proposals[p.proposal_id]
    assert state.status == "stale" and "changed" in state.reason
    assert idea_of(store, "a1") != idea_of(store, "b1")
    with pytest.raises(vault.StaleToken):
        vault.decide(v, p.proposal_id, "accept", state.token)

    # The next review proposes the same merge again from what the claims say now; the user can accept it again.
    again = propose(v, "merge_ideas", {"claims": ["a1", "b1"]})
    assert again.proposal_id == p.proposal_id
    assert vault.replay(v).proposals[p.proposal_id].status == "proposed"


def test_a_rejected_relation_is_taken_out_of_the_graph(tmp_path):
    v = write(tmp_path, ORIGINAL, REPOST, CORRECTION, texts=TEXT)
    for relation in TRIO_VERDICTS.values():
        vault.save_relation(v, relation)

    def supersedes(store):
        return {(e.src, e.dst) for e in store.edges() if e.type == "SUPERSEDES"}
    assert ("c16", "o14") in supersedes(vault.replay(v, include_unreviewed=True))
    p = propose(v, "relation", {"claims": ["c16", "o14"], "type": "SUPERSEDES"})
    vault.decide(v, p.proposal_id, "reject", token(v, p.proposal_id), "the times are for different sessions", at=AT)
    assert ("c16", "o14") not in supersedes(vault.replay(v, include_unreviewed=True))


def test_an_accepted_topic_finds_its_ideas_through_its_claims(tmp_path):
    v = write(tmp_path, ALPHA, BETA)
    p = propose(v, "topic", {"claims": ["a1", "b1"], "title": "Detecting confabulations"})
    vault.decide(v, p.proposal_id, "accept", token(v, p.proposal_id), at=AT)
    store = vault.replay(v)
    [topic] = store.topics()
    assert topic.title == "Detecting confabulations"
    assert topic.ideas == {idea_of(store, "a1"), idea_of(store, "b1")}


def test_an_accepted_people_merge_moves_authorship(tmp_path):
    gal = item("gal", 11, [("g1", "Entropy over meanings flags made-up answers.")], author="Y. Gal")
    v = write(tmp_path, ALPHA, gal, item("gal2", 13, [("g2", "Uncertainty matters.")], author="Yarin Gal"))
    p = propose(v, "merge_people", {"people": ["person:y-gal", "person:yarin-gal"]})
    vault.decide(v, p.proposal_id, "accept", token(v, p.proposal_id), at=AT)
    store = vault.replay(v)
    people = {p.person_id: p for p in store.people()}
    assert "person:y-gal" not in people
    assert people["person:yarin-gal"].authored == {"gal", "gal2"}
    assert store.item("gal").author_id == "person:yarin-gal"


def test_a_proposal_about_a_held_back_item_is_unplaced_in_a_strict_rebuild(tmp_path):
    pending = BETA.model_copy(update={"quality_status": "needs_revision", "quality_gate_passed": False})
    v = write(tmp_path, ALPHA, pending)
    p = propose(v, "merge_ideas", {"claims": ["a1", "b1"]})
    assert vault.replay(v).proposals[p.proposal_id].status == "unplaced"
    assert vault.replay(v, include_unreviewed=True).proposals[p.proposal_id].status == "proposed"


def test_atomic_write_leaves_no_temporary_files(tmp_path):
    vault.atomic_write(tmp_path / "a" / "b.json", "{}")
    assert [p.name for p in (tmp_path / "a").iterdir()] == ["b.json"]


# --- the review's proposals ---

TOPICAL = {
    "h1": ("Ann Lee", "Semantic entropy detects hallucinations in large language models."),
    "h2": ("Bo Chen", "Hallucination detection improves when language models measure semantic uncertainty."),
    "h3": ("Cy Ito", "Large language models hallucinate less with retrieval grounding."),
    "h4": ("Ann Lee", "Detecting hallucinations with entropy probes is cheap at inference time."),
    "m1": ("Di Roy", "Retrieval grounding reduces hallucination rates in chat assistants."),
    "m2": ("Ed Fox", "Grounding chat assistants with retrieval lowers their hallucination rates."),
    "g1": ("Fay Wu", "H100 GPU rental prices fell below two dollars per hour."),
    "g2": ("Gus Ma", "Cloud GPU rental prices keep falling as H100 supply grows."),
    "g3": ("Fay Wu", "Spot GPU rental prices are lowest on weekends."),
}


def topical_vault(path):
    records = [item(f"item-{cid}", 1 + n, [(cid, text)], author=author)
               for n, (cid, (author, text)) in enumerate(TOPICAL.items())]
    records.append(item("item-gal", 20, [("y1", "Uncertainty estimates flag made-up answers.")], author="Y. Gal",
                        entities=[("Yarin Gal", "person")]))
    return write(path, *records)


def review(v, **hooks):
    with vault.lock(v):
        report = vault.replay(v, include_unreviewed=True).review(AT, **hooks)
        vault.save_review(v, report)
    return report


def by_kind(report):
    out = {}
    for p in report.proposals:
        out.setdefault(p.kind, []).append(p)
    return out


def test_the_review_proposes_topics_merges_and_people(tmp_path):
    v = topical_vault(tmp_path)
    kinds = by_kind(review(v))
    assert [p.payload["claims"] for p in kinds["merge_ideas"]] == [["m1", "m2"]]
    topics = sorted(sorted(p.payload["claims"]) for p in kinds["topic"])
    assert topics == [["g1", "g2", "g3"], ["h1", "h2", "h3", "h4", "m1", "m2"]]
    assert any("Gpu" in p.payload["title"] or "GPU" in p.payload["title"] for p in kinds["topic"])
    assert [p.payload["people"] for p in kinds["merge_people"]] == [["person:y-gal", "person:yarin-gal"]]
    assert review(v).proposals == []  # nothing new the second time


def test_a_namer_and_a_confirmer_are_used(tmp_path):
    v = topical_vault(tmp_path)
    kinds = by_kind(review(v, namer=lambda statements: f"Topic of {len(statements)}", confirm=lambda a, b: False))
    assert "merge_ideas" not in kinds
    assert sorted(p.payload["title"] for p in kinds["topic"]) == ["Topic of 3", "Topic of 6"]


def test_an_accepted_topic_takes_new_close_ideas_provisionally(tmp_path):
    v = topical_vault(tmp_path)
    gpu = next(p for p in review(v).proposals if p.kind == "topic" and "g1" in p.payload["claims"])
    vault.decide(v, gpu.proposal_id, "accept", token(v, gpu.proposal_id), at=AT)
    write(v, item("item-g4", 25, [("g4", "GPU rental prices for H100 nodes dropped again.")], author="Hal Oh"))
    store = vault.replay(v)
    [topic] = store.topics()
    assert len(topic.ideas) == 3 and topic.attached == {idea_of(store, "g4")}
    # The accepted topic's Ideas are not clustered again.
    assert all("g1" not in p.payload["claims"] for p in review(v).proposals if p.kind == "topic")


def test_a_supersession_of_a_reference_claim_asks_for_confirmation(tmp_path):
    old = item("spec", 3, [("s1", "The API rate limit is 100 requests per minute.")], durability="reference")
    new = item("spec2", 9, [("s2", "The API rate limit is 300 requests per minute.")], durability="reference")
    v = write(tmp_path, old, new)
    [p] = [p for p in review(v).proposals if p.kind == "relation"]
    assert p.payload == {"claims": ["s2", "s1"], "type": "SUPERSEDES"}
