import shutil
from pathlib import Path

from refresher import viewer

FIXTURES = Path(__file__).parent / "fixtures"


def scripted(*answers):
    queue = list(answers)
    return lambda prompt: queue.pop(0)


def test_items_list_headlines_and_status(tmp_path):
    shutil.copytree(FIXTURES / "vault", tmp_path / "vault")
    rows = viewer.items(tmp_path / "vault")
    assert len(rows) == 6 and all(r["status"] in {"accepted", "needs_revision"} for r in rows)
    assert viewer.headline("Kirill R, [7 Oct 2026 at 12:13:38]:\n# Title here\n") == "Title here"


def test_session_runs_default_and_custom_filters(tmp_path, capsys):
    shutil.copytree(FIXTURES / "vault", tmp_path / "vault")
    assert viewer.main(["--vault", str(tmp_path / "vault")], ask=scripted("1", "2", "8", "8 .claims | length", "t", "b", "q")) == 0
    out = capsys.readouterr().out
    assert "pick a file number" in out  # a missing step has no file to open
    assert "quality_status" in out and "jq '.claims | length'" in out and "✗ documents" in out


def test_unknown_item_and_eof_are_handled(tmp_path, capsys):
    shutil.copytree(FIXTURES / "vault", tmp_path / "vault")

    def eof(prompt):
        raise EOFError

    assert viewer.main(["--vault", str(tmp_path / "vault")], ask=scripted("zzz", "q")) == 0
    assert "no single item matches" in capsys.readouterr().out
    assert viewer.main(["--vault", str(tmp_path / "vault")], ask=eof) == 0


def test_problem_flags_reviews_gate_and_missing_steps(tmp_path):
    shutil.copytree(FIXTURES / "vault", tmp_path / "vault")
    vault = tmp_path / "vault"
    review = {"review": {"status": "needs_revision", "claims": [{"verdict": "supported"}, {"verdict": "unsupported"}],
                         "omitted_concepts": [{"passage_id": 1, "description": "x"}]}}
    assert viewer.problem("coverage-reviews", review) == "needs_revision: 1 flagged, 1 omitted"
    assert viewer.problem("reviews", {"review": {"status": "accepted"}}) == ""
    assert viewer.problem("entity-reviews", {"review": {"status": "needs_revision", "entities": [],
                                                        "omitted_entities": [{"name": "UTC"}]}}) == "needs_revision: 0 flagged, 1 omitted"
    assert viewer.problem("triage", {"labels": {"keep": False}}) == "triage: not kept"
    held = next(r for r in viewer.items(vault) if r["status"] == "needs_revision")
    rows = {s["stage"]: s for s in viewer.stages(vault, held["id"])}
    assert rows["processed"]["problem"].startswith("quality needs_revision")
    assert rows["triage"]["path"] is None and rows["triage"]["problem"].startswith("missing")
