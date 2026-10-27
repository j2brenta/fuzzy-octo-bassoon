"""Every warning and error is kept in the vault for review; `refresher problems` reads it back."""
import json
import logging

from engine import logs
from refresher.cli import main
from tests.test_proposals import ALPHA, write
import pytest


@pytest.fixture(autouse=True)
def problem_log_on(monkeypatch):
    monkeypatch.setattr("engine.logs.ENABLED", True)


def test_warnings_and_errors_are_kept_as_json_lines_and_info_is_not(tmp_path):
    logs.attach(tmp_path, process="test")
    try:
        log = logging.getLogger("refresher.place")
        log.info("item 5b492f4dec · triage: keep (2.0 s)")
        assert not (tmp_path / logs.PROBLEMS).exists()  # created with the first problem only
        log.warning("af05cfe35067: not judged: The relation model returned an invalid verdict")
        logging.getLogger("engine.llm").error("fixture/model: call failed (ModelError: HTTP 500)")
    finally:
        logs.attach(tmp_path / "elsewhere")  # replaces this test's handler
    rows = [json.loads(line) for line in (tmp_path / logs.PROBLEMS).read_text().splitlines()]
    assert [(r["level"], r["source"], r["item"], r["process"]) for r in rows] == [
        ("WARNING", "refresher.place", "af05cfe35067", "test"), ("ERROR", "engine.llm", None, "test")]
    assert logs.read_problems(tmp_path) == rows


def test_attaching_again_replaces_the_problem_log(tmp_path):
    root = logging.getLogger()
    logs.attach(tmp_path / "a")
    logs.attach(tmp_path / "b")
    assert sum(isinstance(h, logs.ProblemLog) for h in root.handlers) == 1


def test_problems_groups_repeats_and_lists_the_latest(tmp_path, capsys):
    v = write(tmp_path, ALPHA)
    (v / "logs").mkdir()
    (v / logs.PROBLEMS).write_text("\n".join(json.dumps(r) for r in [
        {"at": "2026-10-27T10:00:00+00:00", "level": "WARNING", "source": "refresher.place",
         "message": "af05cfe35067: not judged: invalid verdict", "item": "af05cfe35067", "process": "bot"},
        {"at": "2026-10-27T11:00:00+00:00", "level": "WARNING", "source": "refresher.place",
         "message": "261c73e38e0f: not judged: invalid verdict", "item": "261c73e38e0f", "process": "cli"}]) + "\n")
    assert main(["problems", "--vault", str(v)]) == 0
    out = capsys.readouterr().out
    assert "2 from 2026-10-27T10:00:00+00:00" in out and "   2 × refresher.place: #: not judged: invalid verdict" in out
    assert main(["problems", "--vault", str(v), "--since", "2026-10-27T10:30"]) == 0
    assert "1 from" in capsys.readouterr().out
