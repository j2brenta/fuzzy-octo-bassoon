import json

import pytest

from graph.memory import MemoryStore
from refresher import theses
from tests.test_relate import item, verdicts


class Fake:
    model_name = "fake"

    def __init__(self, data):
        self.data, self.calls, self.seen = data, 0, None

    def complete_json(self, system_prompt, content):
        self.calls += 1
        self.seen = json.loads(content)
        assert "untrusted" in system_prompt and "English" in system_prompt
        return type("Reply", (), {"data": self.data})()


def store_and_vault(tmp_path, digest="h1"):
    store = MemoryStore()
    store.apply(item("paper", 10, [("p1", "Semantic entropy detects confabulations."),
                                   ("p2", "It samples several answers per question.")], author="Ann Lee"),
                "Semantic entropy detects confabulations. It samples several answers.", verdicts("paper"))
    store.apply(item("bare", 11, [], author=None), "A post with no claims.", verdicts("bare"))
    store.extras = {"paper": {"links": ["https://arxiv.org/abs/1"]}}
    (tmp_path / "raw").mkdir(exist_ok=True)
    for i in ("paper", "bare"):
        (tmp_path / "raw" / f"{i}.json").write_text(json.dumps({"text": "x", "content_hash": digest}))
    return store


def test_theses_rest_on_known_claims_and_are_written_once(tmp_path):
    store = store_and_vault(tmp_path)
    model = Fake({"theses": [
        {"statement": " Sampling answers flags confabulations. ", "claims": ["c2", "c1", "c9", "c1"],
         "stance": "original", "cites": ["l1", "Farquhar et al."], "durability": "reference"},
        {"statement": "", "claims": []},  # empty: dropped
        {"statement": "A second point.", "claims": [], "stance": "opinion"}]})  # unknown stance: summary
    record = theses.theses_for(tmp_path, store, "paper", model)
    first, second = record["theses"]
    assert first["id"] == "paper:t1" and first["statement"] == "Sampling answers flags confabulations."
    assert first["claims"] == ["p2", "p1"]  # c9 is not a claim of this item; duplicates collapse
    assert first["cites"] == [{"url": "https://arxiv.org/abs/1"}, {"name": "Farquhar et al."}]
    assert second["stance"] == "summary" and second["durability"] is None
    assert [c["id"] for c in model.seen["claims"]] == ["c1", "c2"] and model.seen["links"][0]["id"] == "l1"
    assert theses.theses_for(tmp_path, store, "paper", model) == record and model.calls == 1  # stored, reused
    assert theses.missing(tmp_path, store, ["paper", "bare"], model) == ["bare"]
    (tmp_path / "raw" / "paper.json").write_text(json.dumps({"text": "x", "content_hash": "h2"}))
    assert theses.missing(tmp_path, store, ["paper"], model) == ["paper"]  # a new text writes them again
    with pytest.raises(theses.ModelOutputError):
        theses.theses_for(tmp_path, store, "paper", Fake({"theses": [{"statement": " "}]}), force=True)


def test_without_a_record_a_source_has_its_title_as_thesis_and_a_repost_has_none(tmp_path):
    store = store_and_vault(tmp_path)
    store.apply(item("copy", 12, [("k1", "Semantic entropy detects confabulations.")], author=None),
                "Forwarded.", verdicts("copy", ("k1", "SAME", "p1")))
    store.item("copy").duplicate_of = "paper"
    store.theses = {}
    out = theses.of_store(store)
    assert set(out) == {"paper", "bare"}
    assert out["paper"][0]["claims"] == sorted(["p1", "p2"]) or set(out["paper"][0]["claims"]) == {"p1", "p2"}
    assert out["paper"][0]["stance"] == "original" and out["bare"][0]["stance"] == "summary"
    assert not out["paper"][0]["stored"]
    store.theses = {"paper": {"theses": [{"id": "paper:t1", "statement": "S", "claims": ["p1", "gone"],
                                          "stance": "original", "cites": [], "durability": "reference"}]}}
    assert theses.of_store(store)["paper"][0]["claims"] == ["p1"]  # a claim re-extraction removed is dropped
