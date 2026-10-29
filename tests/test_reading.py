import json
from datetime import datetime, timedelta, timezone

import pytest

from refresher import reading, summarize

AT = datetime(2026, 10, 28, 9, tzinfo=timezone.utc)


def test_the_latest_mark_wins_and_unread_removes_it(tmp_path):
    reading.mark(tmp_path, "a", "read", AT)
    reading.mark(tmp_path, "b", "skimmed", AT)
    reading.mark(tmp_path, "b", "unread", AT + timedelta(minutes=1))
    reading.mark(tmp_path, "c", "skimmed", AT)
    reading.mark(tmp_path, "c", "read", AT + timedelta(minutes=2))
    log = reading.load(tmp_path)
    assert {k: v["state"] for k, v in log["items"].items()} == {"a": "read", "c": "read"}
    with pytest.raises(ValueError):
        reading.mark(tmp_path, "a", "loved", AT)
    (tmp_path / reading.LOG).open("a").write("not json\n[1]\n")
    assert reading.load(tmp_path)["items"].keys() == {"a", "c"}  # broken lines are skipped
    assert reading.for_scope(log, "topic:x", ["a", "z"], AT)["items"] == {"a": log["items"]["a"]}


def test_visits_in_one_sitting_keep_what_was_new(tmp_path):
    reading.visit(tmp_path, "topic:x", AT)
    reading.visit(tmp_path, "topic:x", AT + timedelta(minutes=20))  # a reload: the same sitting
    log = reading.load(tmp_path)
    assert log["visits"]["topic:x"] == [None, AT.isoformat()]
    assert reading.for_scope(log, "topic:x", [], AT + timedelta(minutes=30))["last_visit"] is None
    assert reading.for_scope(log, "topic:x", [], AT + timedelta(days=1))["last_visit"] == AT.isoformat()
    reading.visit(tmp_path, "topic:x", AT + timedelta(days=1))
    log = reading.load(tmp_path)
    assert reading.for_scope(log, "topic:x", [], AT + timedelta(days=1, minutes=5))["last_visit"] == AT.isoformat()
    assert reading.for_scope(log, "topic:y", [], AT)["last_visit"] is None


class Fake:
    model_name = "fake"

    def __init__(self, data):
        self.data, self.calls = data, 0

    def complete_json(self, system_prompt, content):
        self.calls += 1
        assert "untrusted" in system_prompt and json.loads(content)["source"]
        return type("Reply", (), {"data": self.data})()


def raw(vault, item_id, text, digest="h1"):
    (vault / "raw").mkdir(exist_ok=True)
    (vault / "raw" / f"{item_id}.json").write_text(json.dumps({"text": text, "content_hash": digest}))


def test_a_summary_is_made_once_per_text_and_language(tmp_path):
    raw(tmp_path, "a", "Semantic entropy flags made-up answers. It samples several answers.")
    model = Fake({"hook": "  Sampling answers flags confabulation. ", "gist": "It samples answers and compares meanings."})
    record = summarize.summarize_item(tmp_path, "a", model)
    assert record["hook"] == "Sampling answers flags confabulation." and record["language"] == "en"
    assert summarize.summarize_item(tmp_path, "a", model) == record and model.calls == 1  # stored and reused
    assert summarize.missing(tmp_path, ["a", "b"], model) == ["b"]
    raw(tmp_path, "a", "A new version of the text.", digest="h2")
    assert summarize.missing(tmp_path, ["a"], model) == ["a"]
    assert summarize.load_all(tmp_path)["a"]["hook"] == record["hook"]
    with pytest.raises(summarize.ModelError):
        summarize.summarize_item(tmp_path, "a", Fake({"gist": "no hook"}), force=True)


def test_without_a_summary_the_lead_sentences_stand_in():
    hook, gist = summarize.lead(["Why models lie", "", "Models confabulate when unsure. Sampling shows it. A third one."],
                                "Why models lie")
    assert hook == "Models confabulate when unsure." and gist == "Models confabulate when unsure. Sampling shows it."
    assert summarize.lead([], "Only a title") == ("Only a title", "")
