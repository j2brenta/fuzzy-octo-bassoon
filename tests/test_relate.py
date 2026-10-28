import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from engine.llm import ModelOutputError, ModelReply
from graph import relate
from graph.memory import MemoryStore, person_id
from refresher import vault
from refresher.cli import main
from refresher.schemas import Extraction, Relation, Verdict

FIXTURES = Path(__file__).parent / "fixtures"


def item(item_id, day, claims, *, author="Demo Organizer", keep=True, durability="news", entities=()):
    """A small processed record: claims is a list of (claim_id, statement)."""
    return Extraction.model_validate({
        "stage": "processed_extraction", "item_id": item_id,
        "source": {"channel": "telegram", "source_id": f"seed:{item_id}", "source_name": item_id,
                   "captured_at": datetime(2026, 10, 19, tzinfo=timezone.utc).isoformat(),
                   "published_at": datetime(2026, 10, day, 9, tzinfo=timezone.utc).isoformat(),
                   "author_hint": {"name": author} if author else None},
        "claims": [{"claim_id": cid, "statement": text, "kind": "announcement", "durability_class": durability,
                    "half_life_days": 30 if durability == "news" else 180, "confidence": 0.9,
                    "evidence": {"item_id": item_id, "section_id": f"{item_id}:s1", "start_char": 0, "end_char": 1}}
                   for cid, text in claims],
        "entities": [{"entity_id": f"e{n}", "name": name, "kind": kind} for n, (name, kind) in enumerate(entities)],
        "triage": {"keep": keep, "kind": "announcement", "durability_class": durability},
        "quality_status": "accepted", "quality_gate_passed": True,
    })


def verdicts(item_id, *rows):
    """Canned verdicts: (claim_id, label, target_id[, subject, subject_name]); targets become the only candidate."""
    out = [Verdict(claim_id=r[0], label=r[1], target_id=r[2], rationale="canned",
                   **({"subject": r[3], "subject_name": r[4]} if len(r) > 3 else {})) for r in rows]
    return Relation(item_id=item_id, relator="canned", prompt_version="test",
                    candidates={v.claim_id: [v.target_id] if v.target_id else [] for v in out}, verdicts=out)


ORIGINAL = item("original", 15, [("o14", "The demo workshop starts at 14:00 UTC on 25 October 2026.")], keep=False)
REPOST = item("repost", 15, [("r14", "The demo workshop starts at 14:00 UTC on 25 October 2026.")], author=None)
CORRECTION = item("correction", 16, [("c16", "The demo workshop starts at 16:00 UTC on 25 October 2026."),
                                     ("cprev", "The demo workshop was previously announced to start at 14:00 UTC.")])
TEXT = {"original": "Synthetic test fixture: The demo workshop starts at 14:00 UTC on 25 October 2026.",
        "repost": "Forwarded: Synthetic test fixture: The demo workshop starts at 14:00 UTC on 25 October 2026.",
        "correction": "Correction: the demo workshop starts at 16:00 UTC on 25 October 2026, "
                      "replacing the previously announced 14:00 UTC time."}
TRIO_VERDICTS = {"original": verdicts("original", ("o14", "NONE", None)),
                 "repost": verdicts("repost", ("r14", "SAME", "o14")),
                 "correction": verdicts("correction", ("c16", "UPDATES", "o14"), ("cprev", "SAME", "o14"))}


def trio(order=("correction", "repost", "original")):
    store = MemoryStore()
    records = {"original": ORIGINAL, "repost": REPOST, "correction": CORRECTION}
    for name in order:
        store.apply(records[name], TEXT[name], TRIO_VERDICTS[name])
    return store


# --- Phase 3: the relation stage ---

class Canned:
    name, prompt_version, model_name = "canned", "test", None

    def __init__(self, labels):
        self.labels = labels  # claim id -> label against the first candidate

    def relate(self, item, claims, candidates):
        out = []
        for c in claims:
            label = self.labels.get(c.claim_id, "NONE")
            target = candidates[c.claim_id][0].claim_id if label != "NONE" else None
            out.append(Verdict(claim_id=c.claim_id, label=label, target_id=target))
        return out


def test_judge_records_candidates_and_round_trips_through_the_vault(tmp_path):
    store = MemoryStore()
    store.apply(ORIGINAL, TEXT["original"])
    relation = relate.judge(CORRECTION, TEXT["correction"], store.candidate_pool("correction"),
                            Canned({"c16": "UPDATES"}))
    assert relation.candidates["c16"][0] == "o14"  # the stub's claim is a candidate
    assert relation.verdicts[0].target_id == "o14" and relation.relator == "canned"
    vault.save_relation(tmp_path, relation)
    assert vault.load_relation(tmp_path, CORRECTION) == relation


def test_stale_verdicts_are_ignored(tmp_path):
    vault.save_relation(tmp_path, verdicts("correction", ("c16", "NONE", None)))  # cprev missing
    assert vault.load_relation(tmp_path, CORRECTION) is None


@pytest.mark.parametrize("fields", [
    {"label": "NONE", "target_id": "x"},
    {"label": "SAME", "target_id": None},
    {"label": "NONE", "subject": "person"},
    {"label": "NONE", "subject_name": "Ada"},
])
def test_verdict_schema_rejects_inconsistent_fields(fields):
    with pytest.raises(ValidationError):
        Verdict(claim_id="c", **fields)


def test_relation_rejects_a_target_that_was_not_a_candidate():
    with pytest.raises(ValidationError, match="not a candidate"):
        Relation(item_id="i", relator="model", prompt_version="v", candidates={"c": ["a", "b"]},
                 verdicts=[Verdict(claim_id="c", label="SAME", target_id="z")])


class FakeModel:
    provider = model_provider = "fake"
    model_name = "fake-super"

    def __init__(self, data):
        self.data, self.calls = data, []

    def complete_json(self, system_prompt, content):
        self.calls.append(json.loads(content))
        return ModelReply(self.data)


def model_judge(data):
    store = MemoryStore()
    store.apply(ORIGINAL, TEXT["original"])
    model = FakeModel(data)
    return relate.judge(CORRECTION, TEXT["correction"], store.candidate_pool("correction"),
                        relate.ModelRelator(model)), model


def test_model_relator_makes_one_call_with_short_ids_and_maps_them_back():
    relation, model = model_judge({"verdicts": [
        {"claim": "c1", "subject": "world", "label": "UPDATES", "target": "k1", "rationale": "new time"},
        {"claim": "c2", "subject": "world", "label": "NONE", "target": "k1", "rationale": "a reference"}]})
    assert len(model.calls) == 1 and "previously announced" in "\n".join(p["text"] for p in model.calls[0]["passages"])
    assert relation.model == "fake-super" and relation.prompt_version == relate.PROMPT_VERSION
    by_claim = {v.claim_id: v for v in relation.verdicts}
    assert (by_claim["c16"].label, by_claim["c16"].target_id) == ("UPDATES", "o14")
    assert by_claim["cprev"].target_id is None  # a target given with NONE is dropped


def test_model_relator_rejects_a_target_outside_the_candidates():
    with pytest.raises(ModelOutputError, match="not a candidate"):
        model_judge({"verdicts": [{"claim": "c1", "label": "SAME", "target": "k9"}]})


def test_model_relator_defaults_a_missing_verdict_to_none():
    relation, _ = model_judge({"verdicts": [{"claim": "c1", "label": "UPDATES", "target": "k1"}]})
    assert [v.label for v in relation.verdicts] == ["UPDATES", "NONE"]


def test_cli_relate_writes_verdicts_once(tmp_path, capsys):
    shutil.copytree(FIXTURES / "vault", tmp_path / "vault")
    assert main(["relate", "--vault", str(tmp_path / "vault"), "--lexical"]) == 0
    files = list((tmp_path / "vault" / "relations").glob("*.json"))
    assert files and all(Relation.model_validate_json(f.read_text()).relator == "lexical" for f in files)
    assert main(["relate", "--vault", str(tmp_path / "vault"), "--lexical"]) == 0
    assert capsys.readouterr().out.strip().endswith("judged 0 items, 0 failed")
    # Stored lexical verdicts replay to the same graph as lexical verdicts computed on the fly.
    stored = vault.replay(tmp_path / "vault", [], include_unreviewed=True)
    shutil.rmtree(tmp_path / "vault" / "relations")
    fresh = vault.replay(tmp_path / "vault", [], include_unreviewed=True)
    assert set(stored.edges()) == set(fresh.edges())


# --- Phase 4: Ideas and profiles ---

@pytest.mark.parametrize("order", [("correction", "repost", "original"), ("original", "repost", "correction")])
def test_demo_trio_forms_one_idea_with_the_correction_current(order):
    store = trio(order)
    [idea] = store.ideas()
    assert idea.members == {"o14", "r14", "c16", "cprev"}
    assert idea.canonical_claim_id == "c16" and idea.title.startswith("The demo workshop starts at 16:00")
    claims = {c.claim.claim_id: c for c in store.claims(hidden=True)}
    assert claims["c16"].superseded_by is None and claims["o14"].superseded_by == "c16"
    assert claims["r14"].superseded_by == "c16"  # the update replaces the repost's sighting too
    assert [c.claim.claim_id for c in store.chain("o14")] == ["o14", "c16"]
    assert "original" not in {i.item_id for i in store.items()} and store.held_back["original"] == "skipped by triage"
    assert store.item("repost").duplicate_of == "original"


def test_previously_claim_creates_no_supports_edge():
    store = trio()
    assert not [e for e in store.edges() if e.type == "SUPPORTS"]
    assert any(e.type == "DUPLICATES" and e.src == "cprev" for e in store.edges())
    assert store.claims_for(["workshop"])  # the trio is reachable by the refresher's query


def test_person_facts_join_profiles_and_create_no_ideas():
    bio = item("bio", 10, [("ada1", "Ada Lovelace wrote the first published program."),
                           ("w1", "Analytical engines could compose music.")],
               author="Someone", entities=[("Ada Lovelace", "person")])
    store = MemoryStore()
    store.apply(bio, "", verdicts("bio", ("ada1", "NONE", None, "person", "Ada Lovelace"), ("w1", "NONE", None)))
    ada = store.person(person_id("Ada Lovelace"))
    assert ada.facts == {"ada1"} and store.claims_for(["program"], hops=0)[0].subject_id == ada.person_id
    assert [i.members for i in store.ideas()] == [{"w1"}]
    assert any(e.type == "ABOUT" and e.src == "ada1" for e in store.edges())


def test_cyrillic_names_keep_their_own_person_ids():
    # They used to fold to "person:", which merged every such name into one profile.
    assert person_id("Дед Мороз") == "person:дед-мороз" and person_id("Кости Крестникова") == "person:кости-крестникова"
    assert person_id("José García") == "person:jose-garcia"


def test_lexical_subject_routes_a_claim_that_opens_with_a_person():
    bio = item("bio", 10, [("ada1", "Ada Lovelace wrote the first published program.")],
               entities=[("Ada Lovelace", "person")])
    store = MemoryStore()
    store.apply(bio, "")
    assert store.verdict("ada1").subject_name == "Ada Lovelace" and not list(store.ideas())


def test_world_labels_map_to_edges_and_ideas():
    base = item("base", 10, [("b1", "Model X has a 128k context window.")], author="Alice", durability="reference")
    later = item("later", 12, [("s1", "Model X has a 128k token context window."),
                               ("d1", "Model X's 128k window uses sliding attention."),
                               ("x1", "Model X has a 32k context window.")], author="Bob", durability="foundation")
    store = MemoryStore()
    store.apply(base, "")
    store.apply(later, "", verdicts("later", ("s1", "SAME", "b1"), ("d1", "ADDS_DETAIL", "b1"),
                                    ("x1", "CONFLICTS", "b1")))
    kinds = {(e.src, e.type, e.dst) for e in store.edges() if e.type in {"SUPPORTS", "ELABORATES", "CONTRADICTS"}}
    assert kinds == {("s1", "SUPPORTS", "b1"), ("d1", "ELABORATES", "b1"), ("x1", "CONTRADICTS", "b1")}
    ideas = sorted((sorted(i.members) for i in store.ideas()))
    assert ideas == [["b1", "s1", "x1"], ["d1"]]
    shared = store.idea(store.claims_for(["32k"], hops=0)[0].idea_id)
    assert shared.durability == "foundation" and shared.canonical_claim_id == "b1"
    b1 = next(c for c in store.claims() if c.claim.claim_id == "b1")
    assert b1.confirmations == 1 and store.person(person_id("Bob")).disagreements == 1
    assert store.person(person_id("Bob")).contradictions == 0


# --- Phase 5: grouping and relation precision in the eval ---

def test_eval_scores_groupings_pairwise_and_relation_precision():
    from refresher import evaluate
    store = trio()
    expectations = [evaluate.Expect(name, {"source_id": f"seed:{name}"}, {}, rels) for name, rels in
                    (("original", []), ("repost", [("DUPLICATES", "original")]),
                     ("correction", [("SUPERSEDES", "original")]))]
    extractions = {ex.item_id: ex for ex in (ORIGINAL, REPOST, CORRECTION)}
    groupings = [[("original", "starts at 14:00"), ("correction", "starts at 16:00")],
                 [("correction", "previously announced")]]  # kept apart from the workshop time
    checks = {c.name: c for c in evaluate.evaluate(store, expectations, extractions, groupings)}
    assert checks["ideas pairwise recall"].got == 1.0
    assert checks["ideas pairwise precision"].got == 0.33  # "previously" joined the Idea through SAME
    # correction SUPERSEDES repost counts: the repost duplicates the original. The "previously" claim's
    # correction DUPLICATES original is not in the manifest.
    assert checks["relation precision"].got == "0.75 (unexpected: correction DUPLICATES original)"
    assert checks["SUPERSEDES original"].ok and checks["DUPLICATES original"].ok


# --- Phase 6: the minimal review ---

LIST_1 = item("list1", 7, [("l1a", "Jack Lindsey leads Anthropic's model psychiatry team."),
                           ("l1b", "Yarin Gal leads the OATML group at Oxford."),
                           ("l1c", "METR measures how long a task agents can complete autonomously."),
                           ("l1d", "Eugene Yan writes practical pieces on LLM evaluation.")], author=None)
LIST_2 = item("list2", 8, [("l2a", "Jack Lindsey leads the model psychiatry team at Anthropic."),
                           ("l2b", "At Oxford, Yarin Gal leads the OATML group."),
                           ("l2c", "METR measures how long agents can autonomously complete a task.")], author=None)
LIST_TEXT = {"list1": "People to follow: Lindsey, Gal, METR, Yan.", "list2": "A shorter, reworded list of three."}


def lists_vault(tmp_path):
    for ex in (LIST_1, LIST_2):
        (tmp_path / "processed").mkdir(exist_ok=True)
        (tmp_path / "raw").mkdir(exist_ok=True)
        (tmp_path / "processed" / f"{ex.item_id}.json").write_text(ex.model_dump_json())
        (tmp_path / "raw" / f"{ex.item_id}.json").write_text(json.dumps({"text": LIST_TEXT[ex.item_id]}))
    return tmp_path


def test_review_finds_a_reworded_list_and_a_rebuild_reproduces_it(tmp_path, capsys):
    v = lists_vault(tmp_path)
    assert vault.replay(v).item("list2").duplicate_of is None  # the texts differ, so no repost
    assert main(["review", "--vault", str(v), "--at", "2026-10-26"]) == 0
    [report] = vault.load_reviews(v)
    assert [(e.src, e.dst) for e in report.item_duplicates] == [("list2", "list1")]
    rebuilt = vault.replay(v)
    assert rebuilt.item("list2").duplicate_of == "list1"
    assert all(c.duplicate_rank == 1 for c in rebuilt.claims() if c.item_id == "list2")
    assert main(["review", "--vault", str(v), "--at", "2026-10-27"]) == 0
    assert capsys.readouterr().out.strip().endswith("review: nothing new")


def test_review_does_not_call_a_correction_a_duplicate():
    assert trio().review(datetime(2026, 10, 26, tzinfo=timezone.utc)).item_duplicates == []


def test_review_firms_an_idea_with_two_independent_authors_and_replay_keeps_it():
    a = item("a", 10, [("a1", "Model X has a 128k context window.")], author="Alice")
    b = item("b", 11, [("b1", "Model X has a 128k token context window.")], author="Bob")
    c = item("c", 12, [("c1", "Model X has a 128k context window, again.")], author="Alice")
    store = MemoryStore()
    store.apply(a, "")
    store.apply(c, "", verdicts("c", ("c1", "SAME", "a1")))
    assert store.review(datetime(2026, 10, 26, tzinfo=timezone.utc)).firmed == []  # one author twice
    store.apply(b, "", verdicts("b", ("b1", "SAME", "a1")))
    report = store.review(datetime(2026, 10, 26, tzinfo=timezone.utc))
    [idea] = store.ideas()
    assert report.firmed == [idea.idea_id] and idea.status == "provisional"
    store.apply_review(report)
    assert store.idea(idea.idea_id).status == "firm" and store.review(report.at).firmed == []


SEED = Path(__file__).parent.parent / ".local-vault" / "seed"


@pytest.mark.skipif(not (SEED / "processed").is_dir(), reason="seed vault not checked out")
def test_seed_people_list_2_duplicates_list_1_and_survives_review_and_rebuild(tmp_path):
    shutil.copytree(SEED, tmp_path / "seed")
    store = vault.replay(tmp_path / "seed", [], include_unreviewed=True)
    by_message = {i.source.message_id: i for i in store.items()}
    first, second = by_message["supplied-people-list"], by_message["supplied-introspection-list"]
    assert second.duplicate_of == first.item_id
    report = store.review(datetime(2026, 10, 26, tzinfo=timezone.utc))
    assert report.topics == {} and second.item_id not in {e.src for e in report.item_duplicates}
    vault.save_review(tmp_path / "seed", report)
    again = vault.replay(tmp_path / "seed", [], include_unreviewed=True)
    assert again.item(second.item_id).duplicate_of == first.item_id


# --- Phase 7: views and the health report ---

def trio_vault(tmp_path):
    bio = item("bio", 10, [("ada1", "Ada Lovelace wrote the first published program.")], author="Someone",
               entities=[("Ada Lovelace", "person")])
    texts = {**TEXT, "bio": "Ada Lovelace wrote the first published program."}
    for ex in (ORIGINAL, REPOST, CORRECTION, bio):
        for folder in ("processed", "raw"):
            (tmp_path / folder).mkdir(exist_ok=True)
        (tmp_path / "processed" / f"{ex.item_id}.json").write_text(ex.model_dump_json())
        (tmp_path / "raw" / f"{ex.item_id}.json").write_text(json.dumps({"text": texts[ex.item_id]}))
    for relation in TRIO_VERDICTS.values():
        vault.save_relation(tmp_path, relation)
    return tmp_path


def test_idea_note_shows_members_chain_and_sources(tmp_path):
    v = trio_vault(tmp_path)
    store = vault.replay(v)
    vault.export(store, v, datetime(2026, 10, 20, tzinfo=timezone.utc))
    [idea] = [i for i in store.ideas() if "c16" in i.members]
    note = (v / "wiki" / "ideas" / f"{idea.idea_id.replace(':', '-')}.md").read_text()
    assert note.splitlines()[note.splitlines().index("---", 1) + 1] == f"# {idea.title}"
    assert "starts at 16:00 UTC on 25 October 2026. ([[" in note and "; current)" in note
    assert "(a held-back item, 2026-10-15; superseded)" in note
    chain = note.split("## Chain", 1)[1]
    assert chain.index("14:00 UTC on 25 October 2026. (2026-10-15)") < chain.index("16:00 UTC")
    assert "## Sources" in note
    item_note = next((v / "wiki" / "items").glob("*Correction*.md")).read_text()
    assert f"[[{idea.idea_id.replace(':', '-')}]]" in item_note


def test_people_notes_list_profile_facts_with_sources(tmp_path):
    v = trio_vault(tmp_path)
    vault.export(vault.replay(v), v, datetime(2026, 10, 20, tzinfo=timezone.utc))
    note = (v / "wiki" / "people" / "Ada Lovelace.md").read_text()
    assert "## Profile\n\n- Ada Lovelace wrote the first published program. ([[" in note


def test_health_report_counts_ideas_by_status_and_conflict():
    from refresher import km
    store = MemoryStore()
    store.apply(item("base", 10, [("b1", "Model X has a 128k context window.")], author="Alice"), "")
    store.apply(item("later", 12, [("s1", "Model X has a 128k token context window."),
                                   ("x1", "Model X has a 32k context window.")], author="Bob"), "",
                verdicts("later", ("s1", "SAME", "b1"), ("x1", "CONFLICTS", "b1")))
    store.apply_review(store.review(datetime(2026, 10, 26, tzinfo=timezone.utc)))
    report = km.run(store, datetime(2026, 10, 26, tzinfo=timezone.utc))
    assert report.ideas == {"firm": 1} and report.conflicted_ideas == ["Model X has a 128k context window."]
    assert "1 Ideas: 1 firm, 0 provisional, 1 with conflicts." in km.render(report, store)


# --- Phase 8: graph.query, called the way the refresher gathers a topic ---

def gather(store, terms):
    """What the refresher does: claims for the topic, their chains, the people, the items around them."""
    claims = store.claims_for(terms)
    ids = [c.claim.claim_id for c in claims]
    return (ids, {cid: [n.claim.claim_id for n in store.chain(cid)] for cid in ids},
            [p.name for p in store.people_for(ids)],
            {c.item_id: {e.type for e in store.neighbours(c.item_id)} for c in claims})


def test_claims_for_matches_whole_terms_and_follows_ideas_not_items(tmp_path):
    store = vault.replay(trio_vault(tmp_path))
    assert [c.claim.claim_id for c in store.claims_for(["16:00"], hops=0)] == ["c16"]
    ids, *_ = gather(store, ["16:00"])
    assert ids[0] == "c16" and set(ids) == {"c16", "cprev", "r14"}  # Idea mates; o14 is held back
    assert store.claims_for(["workshop starts", "nonexistent"], hops=0)  # one matching term is enough
    assert store.claims_for(["workshop zebra"], hops=0) == []  # every word of a term must match


def test_claims_for_starts_from_a_named_person(tmp_path):
    store = vault.replay(trio_vault(tmp_path))
    assert [c.claim.claim_id for c in store.claims_for(["Ada Lovelace"], hops=1)] == ["ada1"]


def test_chain_runs_oldest_first_through_held_back_originals(tmp_path):
    store = vault.replay(trio_vault(tmp_path))
    _, chains, _, _ = gather(store, ["16:00"])
    assert chains["c16"] == ["o14", "c16"] and chains["r14"] == ["r14", "c16"] and chains["cprev"] == ["cprev"]


def test_people_for_names_authors_and_subjects_most_involved_first(tmp_path):
    store = vault.replay(trio_vault(tmp_path))
    assert [p.name for p in store.people_for(["c16", "cprev", "ada1"])] == ["Demo Organizer", "Ada Lovelace",
                                                                          "Someone"]


def test_neighbours_show_relations_and_provenance_without_spans(tmp_path):
    store = vault.replay(trio_vault(tmp_path))
    *_, around = gather(store, ["16:00"])
    assert {"SUPERSEDES", "PART_OF", "PUBLISHED_IN", "AUTHORED_BY"} <= around["correction"]
    assert "FROM" not in around["correction"] and "DUPLICATES" in around["repost"]


def test_a_verdict_onto_a_held_back_repeat_follows_it_to_the_placed_claim():
    """Model verdicts are judged with unreviewed items in the pool; a strict rebuild holds the repost back, so the
    correction's UPDATES onto the repost's claim must reach the original it repeats."""
    held = REPOST.model_copy(update={"quality_status": "needs_revision", "quality_gate_passed": False})
    store = MemoryStore()
    store.apply(ORIGINAL, TEXT["original"], TRIO_VERDICTS["original"])
    store.apply(held, TEXT["repost"], TRIO_VERDICTS["repost"])
    store.apply(CORRECTION, TEXT["correction"], verdicts("correction", ("c16", "UPDATES", "r14"), ("cprev", "NONE", None)))
    assert "repost" in store.held_back
    assert ("c16", "o14") in {(e.src, e.dst) for e in store.edges() if e.type == "SUPERSEDES"}


# --- relate-v2: abstention and pair context, from Victor's news map ---

CONTEXT = {"subject": "demo workshop", "version": "", "metric": "start time", "dataset": "", "unit": "UTC",
           "conditions": "", "time_attribution": "25 October 2026", "unknowns": ""}


def test_abstention_is_a_none_verdict_with_a_target_and_context_needs_every_field():
    Verdict(claim_id="a", abstention="not_comparable", target_id="b", context=CONTEXT)
    with pytest.raises(ValidationError):
        Verdict(claim_id="a", label="SAME", target_id="b", abstention="not_comparable")
    with pytest.raises(ValidationError):
        Verdict(claim_id="a", abstention="insufficient_evidence")  # no target
    with pytest.raises(ValidationError):
        Verdict(claim_id="a", label="UPDATES", target_id="b", context={"subject": "x"})


def test_model_relator_keeps_abstentions_and_context():
    model = FakeModel({"verdicts": [
        {"claim": "c1", "label": "UPDATES", "target": "k1", "context": {**CONTEXT, "extra": "dropped"},
         "rationale": "New time."},
        {"claim": "c2", "label": "NONE", "abstain": "not_comparable", "target": "k1", "context": CONTEXT,
         "rationale": "Different question."}]})
    relation = relate.judge(CORRECTION, TEXT["correction"], trio().candidate_pool("correction"),
                            relate.ModelRelator(model))
    c16, cprev = {v.claim_id: v for v in relation.verdicts}["c16"], {v.claim_id: v for v in relation.verdicts}["cprev"]
    assert c16.label == "UPDATES" and c16.context == CONTEXT and c16.abstention is None
    assert cprev.label == "NONE" and cprev.abstention == "not_comparable" and cprev.target_id == c16.target_id
    assert relation.prompt_version == relate.PROMPT_VERSION


def test_an_abstention_draws_no_relation_and_is_listed():
    from refresher import graphview, km
    store = MemoryStore()
    store.apply(REPOST, TEXT["repost"], verdicts("repost", ("r14", "NONE", None)))
    abstained = Relation(item_id="correction", relator="model", prompt_version="relate-v2",
                         candidates={"c16": ["r14"], "cprev": ["r14"]}, verdicts=[
                             Verdict(claim_id="c16", label="UPDATES", target_id="r14", context=CONTEXT),
                             Verdict(claim_id="cprev", abstention="insufficient_evidence", target_id="r14",
                                     context=CONTEXT, rationale="Says only that a time was announced.")])
    store.apply(CORRECTION, TEXT["correction"], abstained)
    from graph.memory import RELATION_TYPES
    assert not any("cprev" in (e.src, e.dst) for e in store.edges() if e.type in RELATION_TYPES)
    assert [v.claim_id for v in store.abstentions()] == ["cprev"]
    report = km.render(km.run(store, datetime(2026, 10, 20, tzinfo=timezone.utc)), store)
    assert "Not compared: the relator would not guess" in report and "insufficient evidence" in report
    data = graphview.map_data(store, datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert any(e["kind"] == "ABSTAIN" and e["source"] == "cprev" for e in data["edges"])
    assert next(e for e in data["edges"] if e["kind"] == "SUPERSEDES")["context"] == CONTEXT


def test_a_correction_never_supersedes_a_claim_in_its_own_item():
    """The model may call the correction's 'previously 14:00' claim SAME as the old time; spreading the UPDATES
    across that SAME class must not make the new time supersede its own item's claim."""
    store = MemoryStore()
    store.apply(ORIGINAL, TEXT["original"], TRIO_VERDICTS["original"])
    store.apply(CORRECTION, TEXT["correction"], verdicts("correction", ("c16", "UPDATES", "o14"), ("cprev", "SAME", "o14")))
    supersedes = {(e.src, e.dst) for e in store.edges() if e.type == "SUPERSEDES"}
    assert ("c16", "o14") in supersedes and ("c16", "cprev") not in supersedes


def test_a_verdict_about_an_unnamed_person_is_kept_as_a_world_claim():
    model = FakeModel({"verdicts": [
        {"claim": "c1", "subject": "person", "subject_name": "", "label": "UPDATES", "target": "k1", "rationale": "New time."},
        {"claim": "c2", "label": "SAME", "target": "k1"}]})
    relation = relate.judge(CORRECTION, TEXT["correction"], trio().candidate_pool("correction"),
                            relate.ModelRelator(model))
    c16 = {v.claim_id: v for v in relation.verdicts}["c16"]
    assert c16.subject == "world" and c16.subject_name is None and c16.label == "UPDATES"
    assert "subject unnamed" in c16.rationale


def test_one_invalid_row_costs_that_claim_its_relation_not_the_whole_item(caplog):
    model = FakeModel({"verdicts": [
        {"claim": "c1", "label": "UPDATES", "target": "k1", "rationale": "New time."},
        {"claim": "c2", "label": "MAYBE", "target": "k1"}]})
    relation = relate.judge(CORRECTION, TEXT["correction"], trio().candidate_pool("correction"),
                            relate.ModelRelator(model))
    by_id = {v.claim_id: v for v in relation.verdicts}
    assert by_id["c16"].label == "UPDATES"
    assert by_id["cprev"].label == "NONE" and "did not validate" in by_id["cprev"].rationale
    assert any("did not validate" in r.getMessage() for r in caplog.records)


def test_item_titles_skip_reading_headers_and_prefer_a_stated_title():
    from graph.memory import _title
    assert _title("Visible text (automatic OCR):\nReporting bias\n\nmore", "x") == "Reporting bias"
    assert _title("Computer Science > Computation and Language\nTitle:Truth as a Compression Artifact\nView PDF",
                  "x") == "Truth as a Compression Artifact"
    assert _title("Kirill R, [7 Oct 2026 at 12:13:38]:\n# Про богатство\ntext", "x") == "Про богатство"
    assert _title("\n\n", "fallback") == "fallback"
