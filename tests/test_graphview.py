import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from refresher import graphview, vault
from refresher.cli import main
from tests.test_relate import trio

FIXTURES = Path(__file__).parent / "fixtures"
AT = datetime(2026, 10, 20, tzinfo=timezone.utc)


def test_trio_draws_the_correction_superseding_the_repost_without_hidden_stubs():
    data = graphview.graph_data(trio(), AT)
    ids = {n["id"] for n in data["nodes"]}
    assert "original" not in ids  # a triage-skipped stub is not drawn
    supersedes = [e for e in data["edges"] if e["type"] == "SUPERSEDES"]
    assert [(e["source"], e["target"]) for e in supersedes] == [("correction", "repost")]
    assert "16:00" in supersedes[0]["said"] and "14:00" in supersedes[0]["about"]
    idea = next(n for n in data["nodes"] if n["type"] == "idea" and "16:00" in n["label"])
    assert idea["chain"][-1].startswith("The demo workshop starts at 16:00")


def test_edges_are_typed_and_leave_out_mentions_and_spans():
    data = graphview.graph_data(vault.replay(FIXTURES / "vault", [], include_unreviewed=True), AT)
    ids = {n["id"] for n in data["nodes"]}
    assert {e["type"] for e in data["edges"]} <= graphview.EDGE_TYPES
    assert all(e["source"] in ids and e["target"] in ids and e["source"] != e["target"] for e in data["edges"])
    # People appear only through an edge drawn here, not because an item mentions them.
    linked = {end for e in data["edges"] for end in (e["source"], e["target"])}
    assert all(n["id"] in linked for n in data["nodes"] if n["type"] == "person")


def test_cli_writes_a_page_that_opens_from_disk(tmp_path):
    shutil.copytree(FIXTURES / "vault", tmp_path / "vault")
    assert main(["graph", "--vault", str(tmp_path / "vault"), "--at", "2026-10-20"]) == 0
    out = tmp_path / "vault" / "wiki" / "graph"
    page = (out / "index.html").read_text()
    assert "/*GRAPH_DATA*/" not in page and '"nodes"' in page and "</script>" in page
    assert json.loads((out / "graph.json").read_text())["at"].startswith("2026-10-20")
    assert (out / "cytoscape.min.js").exists() and (out / "CYTOSCAPE-LICENSE.txt").exists()
