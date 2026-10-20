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
    assert viewer.main(["--vault", str(tmp_path / "vault")], ask=scripted("1", "2", "2 .claims | length", "t", "b", "q")) == 0
    out = capsys.readouterr().out
    assert "quality_status" in out and "jq '.claims | length'" in out


def test_unknown_item_and_eof_are_handled(tmp_path, capsys):
    shutil.copytree(FIXTURES / "vault", tmp_path / "vault")

    def eof(prompt):
        raise EOFError

    assert viewer.main(["--vault", str(tmp_path / "vault")], ask=scripted("zzz", "q")) == 0
    assert "no single item matches" in capsys.readouterr().out
    assert viewer.main(["--vault", str(tmp_path / "vault")], ask=eof) == 0
