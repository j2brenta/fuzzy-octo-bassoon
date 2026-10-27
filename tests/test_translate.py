"""Translations for reading: stored once per item and language, shown beside the original, never judged."""
import json
import os
from unittest.mock import patch

from engine.llm import ModelReply
from refresher import graphview, translate, vault
from tests.test_proposals import AT, write
from tests.test_relate import item

RU = "Владельцы разместили вакансию.\nЗарплата выросла на 5%."
EN = "The owners posted the vacancy.\nThe salary rose by 5%."


class Translator:
    provider, model_provider, model_name = "nebius", "fixture", "fixture/translator"

    def __init__(self, reply=None):
        self.reply = reply or {"language": "ru", "passages": {"1": "The owners posted the vacancy.",
                                                              "2": "The salary rose by 5%."}}
        self.calls = []

    def complete_json(self, system_prompt, content):
        self.calls.append((system_prompt, json.loads(content)))
        return ModelReply(self.reply)


def vault_with(tmp_path, text):
    ex = item("post", 10, [("c1", "The owners posted the vacancy.")])
    ex.claims[0].evidence.start_char, ex.claims[0].evidence.end_char = 0, len(text.splitlines()[0])
    v = write(tmp_path, ex, texts={"post": text})
    raw = json.loads((v / "raw" / "post.json").read_text())
    (v / "raw" / "post.json").write_text(json.dumps({**raw, "item_id": "post", "content_hash": "h1"}))
    return v


def test_passages_are_translated_once_and_reused(tmp_path):
    v, client = vault_with(tmp_path, RU), Translator()
    with patch.dict(os.environ, {"REFRESHER_LANGUAGE": "en"}):
        record = translate.translate_item(v, "post", client)
        again = translate.translate_item(v, "post", client)
    assert record["source_language"] == "ru" and record["passages"]["2"] == "The salary rose by 5%."
    assert again == record and len(client.calls) == 1
    system, content = client.calls[0]
    assert "into English" in system and [p["text"] for p in content["passages"]] == RU.splitlines()


def test_english_text_costs_no_call_and_shows_nothing_extra(tmp_path):
    v, client = vault_with(tmp_path, EN), Translator()
    with patch.dict(os.environ, {"REFRESHER_LANGUAGE": "en"}):
        record = translate.translate_item(v, "post", client)
    assert client.calls == [] and record["passages"] == {} and record["source_language"] == "en"


def test_another_language_translates_again_and_a_missing_passage_is_left_out(tmp_path):
    v = vault_with(tmp_path, RU)
    with patch.dict(os.environ, {"REFRESHER_LANGUAGE": "en"}):
        translate.translate_item(v, "post", Translator())
    german = Translator({"language": "ru", "passages": {"1": "Die Inhaber haben die Stelle ausgeschrieben."}})
    with patch.dict(os.environ, {"REFRESHER_LANGUAGE": "de"}):
        assert translate.missing(v, ["post"], german) == ["post"]
        record = translate.translate_item(v, "post", german)
    assert "into German" in german.calls[0][0] and set(record["passages"]) == {"1"}


def test_the_map_and_the_note_show_the_translation_beside_the_original(tmp_path):
    v = vault_with(tmp_path, RU)
    with patch.dict(os.environ, {"REFRESHER_LANGUAGE": "en"}):
        translate.translate_item(v, "post", Translator())
        store = vault.replay(v)
        [claim] = graphview.map_data(store, AT)["claims"]
        [anchor] = claim["anchors"]
        assert anchor["quote"] == "Владельцы разместили вакансию."  # evidence stays the exact original
        assert anchor["translation"] == "The owners posted the vacancy."
        vault.export(store, v, AT)
    note = next((v / "wiki" / "items").glob("*.md")).read_text()
    assert "## Translation (English, machine, for reading only)" in note
    assert "> The salary rose by 5%." in note and "> Зарплата выросла на 5%." in note
