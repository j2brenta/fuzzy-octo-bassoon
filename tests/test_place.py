import json

from engine.llm import ModelError
from refresher import vault
from refresher.place import place, reply_text
from refresher.schemas import Verdict
from tests.test_relate import CORRECTION, ORIGINAL, REPOST, TEXT

RECORDS = {"original": ORIGINAL, "repost": REPOST, "correction": CORRECTION}


def capture(vault_path, name):
    """Write an item the way ingestion leaves it: processed/ record plus raw/ text."""
    ex = RECORDS[name]
    for folder, data in (("processed", json.loads(ex.model_dump_json())), ("raw", {"text": TEXT[name]})):
        (vault_path / folder).mkdir(parents=True, exist_ok=True)
        (vault_path / folder / f"{ex.item_id}.json").write_text(json.dumps(data), encoding="utf-8")
    return ex.item_id


class Canned:
    """A label per claim against its first candidate; NONE while there is no candidate yet."""
    name, prompt_version, model_name = "canned", "test", None

    def __init__(self, labels):
        self.labels, self.calls = labels, 0

    def relate(self, item, claims, candidates):
        self.calls += 1
        out = []
        for c in claims:
            label = self.labels.get(c.claim_id, "NONE") if candidates[c.claim_id] else "NONE"
            target = candidates[c.claim_id][0].claim_id if label != "NONE" else None
            out.append(Verdict(claim_id=c.claim_id, label=label, target_id=target))
        return out


class Down:
    name, prompt_version, model_name = "model", "test", "fake"

    def relate(self, item, claims, candidates):
        raise ModelError("Service unavailable.")


LABELS = {"r14": "SAME", "c16": "UPDATES"}


def test_trio_replies_say_duplicate_and_supersedes(tmp_path):
    replies = {}
    for name in ("original", "repost", "correction"):
        placement, store, note = place(tmp_path, capture(tmp_path, name), Canned(LABELS))
        replies[name] = reply_text(placement, store, note)
    assert replies["original"].startswith("Held back from the graph: skipped by triage.")
    assert "Duplicate of:" in replies["repost"] and "By: author unknown" in replies["repost"]
    assert "Supersedes «The demo workshop starts at 14:00" in replies["correction"]
    assert "with «The demo workshop starts at 16:00" in replies["correction"]
    assert "Filed under: The demo workshop starts at 16:00" in replies["correction"]
    assert "By: Demo Organizer" in replies["correction"] and "wording only" not in replies["correction"]
    assert (tmp_path / "relations" / "correction.json").exists()
    assert list((tmp_path / "wiki" / "ideas").glob("*.md"))
    assert "place: correction placed" in (tmp_path / "log.md").read_text()


def test_an_earlier_items_placement_shows_what_came_later(tmp_path):
    for name in ("original", "repost", "correction"):
        _, store, _ = place(tmp_path, capture(tmp_path, name), Canned(LABELS))
    repost = store.placement("repost")
    assert any(e.type == "SUPERSEDES" and e.dst == "r14" for e in repost.relations)
    assert "Superseded: «The demo workshop starts at 14:00" in reply_text(repost, store)


def test_a_placed_item_is_not_judged_again(tmp_path):
    relator = Canned(LABELS)
    item_id = capture(tmp_path, "correction")
    place(tmp_path, item_id, relator)
    place(tmp_path, item_id, relator)
    assert relator.calls == 1


def test_a_model_failure_falls_back_to_wording_and_stores_nothing(tmp_path):
    capture(tmp_path, "original")
    placement, store, note = place(tmp_path, capture(tmp_path, "correction"), Down())
    assert "wording only: the model is unavailable" in note
    assert not (tmp_path / "relations" / "correction.json").exists()
    assert placement.status == "placed" and "wording only" in reply_text(placement, store, note)


def test_no_relator_is_lexical_and_says_so(tmp_path):
    placement, store, note = place(tmp_path, capture(tmp_path, "correction"))
    assert note == "Relations matched by wording only" and placement.ideas
    assert vault.load_relation(tmp_path, CORRECTION) is None


def test_an_original_arriving_after_its_correction_gets_the_supersession(tmp_path):
    relator = Canned(LABELS)
    for name in ("correction", "repost", "original"):  # the correction is judged when nothing precedes it
        _, store, _ = place(tmp_path, capture(tmp_path, name), relator)
    supersedes = {(e.src, e.dst) for e in store.edges() if e.type == "SUPERSEDES"}
    assert ("c16", "r14") in supersedes and ("c16", "o14") in supersedes
    stored = vault.load_relation(tmp_path, CORRECTION)
    assert {v.target_id for v in stored.verdicts if v.label == "UPDATES"} & {"o14", "r14"}
    calls = relator.calls
    place(tmp_path, "original", relator)  # nothing new arrived: nothing is judged again
    assert relator.calls == calls
