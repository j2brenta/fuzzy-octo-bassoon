from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from channels.importer import import_corpus, import_file, main, parse_linkedin_paste, parse_telegram_desktop
from refresher.schemas import RawItem

BERLIN = ZoneInfo("Europe/Berlin")
CORPUS = Path(__file__).parents[1] / "eval" / "seed_corpus"


def test_telegram_header_parsed_and_stripped():
    [msg] = parse_telegram_desktop("Kirill R, [7 Oct 2026 at 12:13:38]:\nHello\nworld\n", BERLIN)
    assert msg["forwarded_by"] == {"name": "Kirill R"}
    assert msg["captured_at"] == datetime(2026, 10, 7, 12, 13, 38, tzinfo=BERLIN)
    assert msg["text"] == "Hello\nworld"


def test_telegram_multi_message_paste_splits():
    text = "A, [1 Oct 2026 at 10:00:00]:\none\n\nB, [1 Oct 2026 at 10:05:00]:\ntwo\n"
    msgs = parse_telegram_desktop(text, BERLIN)
    assert [(m["forwarded_by"]["name"], m["text"]) for m in msgs] == [("A", "one"), ("B", "two")]


def test_linkedin_paste_reads_author_and_date():
    [f] = parse_linkedin_paste("Jianfeng Liu\nPublished Oct 2, 2026\n\nBody", BERLIN)
    assert f["author_hint"] == {"name": "Jianfeng Liu"}
    assert f["published_at"] == datetime(2026, 10, 2, tzinfo=BERLIN)


def test_linkedin_pulse_paste_with_title_and_headline():
    text = "# Why AI Excels at Math\nJianfeng Liu\nAI Application Developer | RAG\n\nOctober 7, 2026\nWhy is AI..."
    [f] = parse_linkedin_paste(text, BERLIN)
    assert f["author_hint"] == {"name": "Jianfeng Liu"}
    assert f["published_at"] == datetime(2026, 10, 7, tzinfo=BERLIN)


def test_plain_file_needs_captured_at(tmp_path):
    path = tmp_path / "note.md"
    path.write_text("Agenda\n18:30 talk\n")
    res = import_file(path, tmp_path, {}, {"channel": "telegram"}, BERLIN)
    assert res.status.startswith("error") and not res.items
    res = import_file(path, tmp_path, {"captured_at": "2026-10-19T11:52:00"}, {"channel": "telegram"}, BERLIN)
    assert res.items[0].captured_at.tzinfo is not None and res.items[0].kind == "text"


def test_empty_file_reported(tmp_path):
    path = tmp_path / "empty.md"
    path.write_text("")
    res = import_file(path, tmp_path, {}, {"channel": "telegram"}, BERLIN)
    assert res.status == "TODO: empty" and not res.items


def test_rawitem_rejects_unknown_fields_and_round_trips():
    item = RawItem(channel="telegram", source_id="x", captured_at="2026-10-07T12:00:00+02:00", kind="text", text="t")
    assert RawItem.model_validate_json(item.model_dump_json()) == item
    with pytest.raises(ValidationError):
        RawItem(channel="telegram", source_id="x", captured_at="2026-10-07T12:00:00+02:00", kind="text", foo=1)
    with pytest.raises(ValidationError):
        RawItem(channel="telegram", source_id="x", captured_at="2026-10-07T12:00:00", kind="text")  # naive


def test_seed_corpus_matches_committed_raw():
    assert main([str(CORPUS), "--check"]) == 0


def test_near_duplicate_seeds_have_different_hashes():
    items = {r.file: r.items[0] for r in import_corpus(CORPUS) if r.items}
    assert items["tg_introspection.md"].content_hash != items["tg_people_to_follow.md"].content_hash
    assert items["tg_people_to_follow.md"].author_hint.name == "AI assistant"
    assert items["tg_people_to_follow.md"].forwarded_by.name == "Kirill R"


def test_linkedin_seed_author_comes_from_the_paste():
    items = {r.file: r.items[0] for r in import_corpus(CORPUS) if r.items}
    li = items["linkedin_why_ai_excels_in_math.md"]
    assert li.author_hint.name == "Jianfeng Liu" and li.published_at.day == 7 and li.url
