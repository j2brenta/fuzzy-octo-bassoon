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
