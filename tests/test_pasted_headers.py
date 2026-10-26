from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import json
from channels.pasted import telegram_copy_metadata
from channels.importer import main
from channels.seed import load_manifest, import_seed
from channels.telegram import capture_message
from engine.parse import parse_capture
from refresher.schemas import Source
from tests.test_telegram import message

TEXT = "Kirill R, [7 Oct 2026 at 12:13:38]:\nAlice says the workshop starts tomorrow.\n"


def test_reuses_header_parser_without_editing_text_and_separates_publication():
    data = telegram_copy_metadata(TEXT, "Europe/Berlin")
    header = data["messages"][0]
    assert header["sender_hint"] == {"name": "Kirill R"}
    assert header["copied_at"] == "2026-10-07T12:13:38+02:00"
    assert header["copied_at_local"] == "2026-10-07T12:13:38"
    assert "published_at" not in header
    assert data["original_text_preserved"]


def test_unknown_timezone_is_retained_without_guessing_utc():
    data = telegram_copy_metadata(TEXT, "")
    assert data["messages"][0]["copied_at"] is None
    assert data["messages"][0]["timezone"] is None
    assert data["messages"][0]["copied_at_local"].startswith("2026-10-07")


def test_file_import_preserves_original_text_and_actual_import_time(tmp_path):
    source = tmp_path / "paste.md"
    source.write_text(TEXT)
    main(
        [
            str(source),
            "--vault",
            str(tmp_path / "vault"),
            "--copy-timezone",
            "Europe/Berlin",
        ]
    )
    path = next((tmp_path / "vault" / "raw").glob("*.json"))
    raw = json.loads(path.read_text())
    assert raw["text"] == TEXT and raw["published_at"] is None
    assert raw["captured_at"].startswith("2026-")
    assert raw["forwarded_by"] == {"name": "Kirill R"}
    document = json.loads(parse_capture(path).path.read_text())
    assert document["telegram_copy"]["messages"][0]["copied_at"].startswith(
        "2026-10-07"
    )


def test_seed_source_timezone_comes_from_corpus_configuration_and_labels_are_not_in_metadata(
    tmp_path,
):
    items = load_manifest(Path("eval/seed_corpus/manifest.yaml"))
    item = next(i for i in items if i.seed_id == "people-list")
    copied = item.capture["channel_metadata"]["telegram_copy"]
    assert copied["messages"][0]["timezone"] == "Europe/Berlin"
    raw = json.loads(import_seed([item], tmp_path)[0][1].path.read_text())
    assert raw["text"] == item.text and raw["published_at"] is None
    assert "expected" not in json.dumps(raw["channel_metadata"])


def test_timeline_prioritizes_publication_then_copy_then_capture():
    src = Source(
        channel="telegram",
        source_id="test",
        captured_at="2026-10-23T10:00:00+00:00",
        copied_at="2026-10-07T12:13:38+02:00",
    )
    assert src.event_time.day == 7
    src = src.model_copy(
        update={"published_at": datetime(2026, 10, 1, tzinfo=timezone.utc)}
    )
    assert src.event_time.day == 1


def test_multiple_headers_keep_separate_senders_and_times():
    data = telegram_copy_metadata(
        TEXT + "Bob, [8 Oct 2026 at 10:00:00]:\nA reply", "Europe/Berlin"
    )
    assert len(data["messages"]) == 2
    assert [row["sender_hint"]["name"] for row in data["messages"]] == [
        "Kirill R",
        "Bob",
    ]


def test_pasted_message_sender_is_not_promoted_to_original_author(tmp_path):
    with patch.dict("os.environ", {"TELEGRAM_COPY_TIMEZONE": "Europe/Berlin"}):
        result = capture_message(message(text=TEXT), tmp_path)
    raw = json.loads(result.path.read_text())
    assert raw["text"] == TEXT and raw["author_hint"] is None
    assert raw["forwarded_by"]["name"] == "Olga"
    assert (
        raw["channel_metadata"]["telegram_copy"]["messages"][0]["sender_hint"]["name"]
        == "Kirill R"
    )


def test_copied_timestamp_reaches_handoff_and_timeline_without_becoming_publication(
    tmp_path,
):
    from engine.llm import ModelReply
    from store.raw import capture_text
    from engine.triage import triage_capture
    from engine.extract import extract_capture
    from engine.judge import judge_capture
    from engine.entities import extract_entities, review_entities
    from engine.package import build_extraction
    from tests.test_triage import FakeModel, LABELS
    from tests.test_judge import review_data
    from tests.test_entities import entity_review

    copied = telegram_copy_metadata(TEXT, "Europe/Berlin")
    raw = capture_text(
        TEXT,
        tmp_path,
        channel="file",
        source_id="header-fixture",
        source_name="Pasted text",
        channel_metadata={"telegram_copy": copied},
    )
    triage_capture(raw.path, FakeModel([ModelReply(dict(LABELS))]))
    claims = extract_capture(
        raw.path,
        FakeModel(
            [
                ModelReply(
                    {
                        "claims": [
                            {
                                "statement": "Alice says the workshop starts tomorrow.",
                                "evidence_passage_ids": [2],
                                "kind": "announcement",
                                "durability_class": "ephemeral",
                                "confidence": 0.9,
                            }
                        ]
                    }
                )
            ]
        ),
    )
    judge_capture(raw.path, FakeModel([ModelReply(review_data(claims.claims))]))
    entities = extract_entities(raw.path, FakeModel([ModelReply({"entities": []})]))
    review_entities(raw.path, FakeModel([ModelReply(entity_review([]))]), entities)
    source = build_extraction(raw.path).record["source"]
    assert source["published_at"] is None
    assert source["forwarded_by"] == {"name": "Kirill R"}
    assert Source.model_validate(source).event_time.day == 7
    assert json.loads(raw.path.read_text())["text"] == TEXT
