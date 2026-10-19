import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from graph.memory import person_id
from refresher import evaluate, km, vault
from refresher.cli import main
from refresher.schemas import Extraction, Override

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = datetime(2026, 10, 19, tzinfo=timezone.utc)
LATER = datetime(2027, 1, 15, tzinfo=timezone.utc)


@pytest.fixture
def vault_dir(tmp_path):
    shutil.copytree(FIXTURES / "vault", tmp_path / "vault")
    return tmp_path / "vault"


def by_source(store, source_id):
    return next(i for i in store.items() if i.source.source_id == source_id)


def test_extraction_reads_ingestion_record():
    path = next((FIXTURES / "vault" / "processed").glob("*.json"))
    ex = Extraction.model_validate_json(path.read_text())
    assert ex.stage == "processed_extraction" and ex.source.captured_at.tzinfo


def test_forwarded_by_string_becomes_author_hint(vault_dir):
    store = vault.replay(vault_dir)
    assert by_source(store, "seed:supplied-forwards").source.forwarded_by.name == "Kirill R"


def test_replay_places_items_and_holds_back_failed_quality(vault_dir):
    store = vault.replay(vault_dir)
    assert len(list(store.items())) == 5
    assert list(store.held_back.values()) == ["needs_revision"]


def test_repost_is_ordered_by_event_time_and_counted_as_noise(vault_dir):
    store = vault.replay(vault_dir)
    items = [i for i in store.items() if i.source.source_id == "seed:supplied-forwards"]
    original = min(items, key=lambda i: i.source.event_time)
    repost = max(items, key=lambda i: i.source.event_time)
    assert repost.duplicate_of == original.item_id and original.duplicate_of is None
    assert all(c.duplicate_rank == 1 for c in store.claims() if c.item_id == repost.item_id)


def test_independent_confirmation_resets_clock_and_raises_trust(vault_dir):
    store = vault.replay(vault_dir)
    original = next(c for c in store.claims() if c.claim.statement.startswith("Semantic entropy")
                    and c.item_id == by_source(store, "seed:supplied-forwards").item_id and c.duplicate_rank == 0)
    assert original.confirmations == 1 and original.half_life_days == 270
    assert original.clock_start == by_source(store, "seed:synthetic-confirmation").source.event_time
    assert store.trust(person_id("Demo Author")) > 0.5


def test_km_time_travel_moves_ephemeral_to_stale(vault_dir):
    store = vault.replay(vault_dir)
    now, later = km.run(store, TODAY), km.run(store, LATER)
    assert now.by_durability["ephemeral"]["fresh"] == 2
    assert later.by_durability["ephemeral"]["stale"] == 2
    assert later.by_durability["reference"]["fresh"] == now.by_durability["reference"]["fresh"]
    assert {c.node.durability for c in now.fading_soon} == {"ephemeral"}


def test_export_is_deterministic(vault_dir, tmp_path):
    other = tmp_path / "other"
    shutil.copytree(vault_dir, other)
    for v in (vault_dir, other):
        vault.export(vault.replay(v), v, TODAY)
    files = sorted(p.relative_to(vault_dir) for p in (vault_dir / "wiki").rglob("*.md"))
    assert files and files == sorted(p.relative_to(other) for p in (other / "wiki").rglob("*.md"))
    assert all((vault_dir / f).read_bytes() == (other / f).read_bytes() for f in files)


def test_user_notes_and_overrides_survive_rebuild(vault_dir):
    vault.export(vault.replay(vault_dir), vault_dir, TODAY)
    note = vault_dir / "wiki" / "people" / "Jianfeng Liu.md"
    text = note.read_text().replace("user_trust: null", "user_trust: 0.9") + "Met at a meetup.\n"
    note.write_text(text)
    redis = next((vault_dir / "wiki" / "items").glob("*18 30 PM*.md"))
    redis.write_text(redis.read_text().replace("user_durability: null", "user_durability: reference"))

    assert main(["rebuild", "--vault", str(vault_dir), "--at", "2027-01-15"]) == 0
    assert "Met at a meetup." in note.read_text() and "user_trust: 0.9" in note.read_text()
    saved = vault.load_overrides(vault_dir)
    assert {(o.target, o.trust, o.durability_class) for o in saved} == {("person", 0.9, None), ("item", None, "reference")}
    store = vault.replay(vault_dir)
    assert store.trust(person_id("Jianfeng Liu")) == 0.9
    assert km.run(store, LATER).by_durability["ephemeral"]["stale"] == 0


def test_override_validates():
    with pytest.raises(ValueError):
        Override(target="person", target_id="x", trust=1.5)


def test_eval_scores_fixture_labels(vault_dir):
    store = vault.replay(vault_dir)
    extractions = {ex.item_id: ex for ex, _ in vault.load_extractions(vault_dir)}
    checks = evaluate.evaluate(store, evaluate.load_expectations(FIXTURES / "manifest.yaml"), extractions)
    result = {(c.seed_id, c.name): c.ok for c in checks}
    assert result[("people-list", "author")] and result[("people-list", "claims")]
    assert result[("people-list", "people recall")] and result[("introspection-list", "DUPLICATES people-list")]
    assert result[("confirmation", "SUPPORTS people-list")]
    assert result[("redis-talks", "people recall")] is False  # the label lists a person the text never mentions
    assert result[("missing", "processed")] is None
    assert "checks pass" in evaluate.render(checks)


def test_cli_km_and_eval(vault_dir, capsys):
    assert main(["km", "--vault", str(vault_dir), "--at", "2027-01-15"]) == 0
    assert "Knowledge health at 2027-01-15" in capsys.readouterr().out
    assert main(["eval", "--vault", str(vault_dir), "--manifest", str(FIXTURES / "manifest.yaml")]) == 1
