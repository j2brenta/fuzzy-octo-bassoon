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


def test_cited_passages_come_from_omissions_and_reasons():
    review = {"review": {"claims": [{"reason": "Cited passage 21 only states it; see passages 3 and 4."}],
                         "omitted_concepts": [{"passage_id": 7, "description": "x"}], "summary": "Passages 9-10 fine."}}
    assert viewer.cited("coverage-reviews", review) == {3, 4, 7, 9, 10, 21}
    rows = [{"id": 1, "text": "one"}, {"id": 2, "text": "two"}]
    assert viewer.render_passages(rows, {2}) == "[  2] two\n"


def test_review_output_joins_claim_text_from_the_reviewed_candidate(tmp_path):
    import json
    vault = tmp_path / "vault"
    for folder in ("raw", "extractions", "reviews"):
        (vault / folder).mkdir(parents=True)
    cid = "a" * 64
    (vault / "raw" / "x.json").write_text(json.dumps({"text": "one\ntwo\n"}))
    (vault / "extractions" / "x.json").write_text(json.dumps({"claims": [{"claim_id": cid, "statement": "Two is true."}]}))
    (vault / "reviews" / "x.json").write_text(json.dumps({"review": {
        "status": "needs_revision", "summary": f"Claim {cid} cites the wrong line.", "omitted_concepts": [],
        "claims": [{"claim_id": cid, "verdict": "needs_revision", "issues": ["wrong_evidence"], "reason": "See passage 2."}]}}))
    s = {"stage": "reviews", "path": vault / "reviews" / "x.json"}
    out = viewer.output(vault, "x", s, viewer.FILTERS["reviews"], color=False)
    assert "--slurpfile c extractions/x.json" in out
    assert '"statement": "Two is true."' in out and "Claim «Two is true.» cites" in out and "✗ Two is true." in out
    assert "[  2] two" in out


def test_vault_picker_lists_vaults_and_goes_back(tmp_path, capsys):
    root = tmp_path / "vaults"
    shutil.copytree(FIXTURES / "vault", root / "a")
    shutil.copytree(FIXTURES / "vault", root / "nested" / "b")
    assert viewer.find_vaults(root) == [root / "a", root / "nested" / "b"]
    summary = viewer.vault_summary(root / "a")
    assert summary["items"] == 6 and summary["status"]["accepted"] == 5
    assert viewer.main(["--root", str(root)], ask=scripted("2", "b", "1", "q")) == 0
    out = capsys.readouterr().out
    assert "5 ok / 1 rev / 0 pend" in out
    assert out.count("accepted/revision/pending") == 2  # b returned to the vault list


def test_ignored_candidates_and_their_reviews_are_marked_stale(tmp_path):
    import json
    vault = tmp_path / "vault"
    for folder in ("raw", "coverage", "coverage-reviews", "processed"):
        (vault / folder).mkdir(parents=True)
    (vault / "raw" / "x.json").write_text(json.dumps({"text": "t"}))
    (vault / "coverage" / "x.json").write_text("{}")
    (vault / "coverage-reviews" / "x.json").write_text(json.dumps({"review": {"status": "needs_revision"}}))
    (vault / "processed" / "x.json").write_text(json.dumps({"ignored_candidates": [{"stage": "coverage", "reason": "stale_parent"}]}))
    rows = {s["stage"]: s for s in viewer.stages(vault, "x")}
    assert rows["coverage-reviews"]["stale"].startswith("stale (stale_parent)") and rows["coverage-reviews"]["problem"] == ""
    assert viewer.stage_row(rows["coverage"]).startswith("· coverage")
