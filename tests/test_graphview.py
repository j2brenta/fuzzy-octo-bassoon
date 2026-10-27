import json
import shutil
from pathlib import Path

from refresher import graphview, vault
from refresher.cli import main
from tests.test_proposals import AT, propose, token, topical_vault
from tests.test_relate import trio

FIXTURES = Path(__file__).parent / "fixtures"


def test_trio_map_has_the_correction_superseding_the_repost_without_hidden_stubs():
    data = graphview.map_data(trio(), AT)
    ids = {i["id"] for i in data["items"]}
    assert "original" not in ids  # a triage-skipped stub is not drawn
    [edge] = [e for e in data["item_edges"] if e["kind"] == "SUPERSEDES"]
    assert (edge["source"], edge["target"]) == ("correction", "repost") and edge["basis"] == "auto"
    assert "16:00" in edge["said"] and "14:00" in edge["about"]
    story = next(s for s in data["stories"] if "16:00" in s["title"])
    assert story["chain"][-1].startswith("The demo workshop starts at 16:00")


def test_claims_carry_their_exact_passages():
    data = graphview.map_data(vault.replay(FIXTURES / "vault", [], include_unreviewed=True), AT)
    texts = {i.item_id: i.text for i in vault.replay(FIXTURES / "vault", [], include_unreviewed=True).items()}
    anchored = [c for c in data["claims"] if c["anchors"]]
    assert anchored
    for c in anchored:
        for a in c["anchors"]:
            assert texts[c["item"]][a["start_char"]:a["end_char"]] == a["quote"]
    for item in data["items"]:  # the evidence panel shows the whole post, and everyone it names
        assert item["text"] == texts[item["id"]] and item["translation"] == []
    named = {m["id"]: m["name"] for i in data["items"] for m in i["mentions"]}
    assert named["person:yarin-gal"] == "Yarin Gal" and "org:metr" in named
    claim_ids, item_ids = {c["id"] for c in data["claims"]}, {i["id"] for i in data["items"]}
    assert all(e["source"] in claim_ids and e["target"] in claim_ids for e in data["edges"])
    assert all(e["source"] in item_ids and e["target"] in item_ids for e in data["item_edges"])


def test_proposals_topics_and_pending_merges_are_in_the_map(tmp_path):
    v = topical_vault(tmp_path)
    store = vault.replay(v, include_unreviewed=True)
    report = store.review(AT)
    vault.save_review(v, report)
    gpu = next(p for p in report.proposals if p.kind == "topic" and "g1" in p.payload["claims"])
    vault.decide(v, gpu.proposal_id, "accept", token(v, gpu.proposal_id), at=AT)
    data = graphview.map_data(vault.replay(v, include_unreviewed=True), AT)
    assert [t["title"] for t in data["topics"]] == [gpu.payload["title"]]
    assert all(i["topics"] for i in data["items"] if i["claims"] and i["claims"][0].startswith("g"))
    assert data["counts"]["waiting"] == 3
    merge = next(e for e in data["edges"] if e["kind"] == "MERGE")
    assert {merge["source"], merge["target"]} == {"m1", "m2"} and merge["basis"] == "proposal"
    states = {p["kind"]: p["status"] for p in data["proposals"]}
    assert states["merge_ideas"] == "proposed" and "accepted" in [p["status"] for p in data["proposals"]]


def test_hostile_text_stays_inert_in_the_page():
    data = {"items": [{"title": "</script><script>alert(1)</script><!--"}]}
    html = graphview.page(data)
    assert "</script><script>" not in html and "<!--</script>" not in html
    inline = html.split('<script type="application/json" id="map-data">', 1)[1].split("</script>", 1)[0]
    assert json.loads(inline)["items"][0]["title"].startswith("</script>")


def test_cli_writes_a_page_that_opens_from_disk(tmp_path):
    shutil.copytree(FIXTURES / "vault", tmp_path / "vault")
    assert main(["graph", "--vault", str(tmp_path / "vault"), "--at", "2026-10-20"]) == 0
    out = tmp_path / "vault" / "wiki" / "graph"
    page = (out / "index.html").read_text()
    assert "<!--MAP_DATA-->" not in page and '"items"' in page and "connect-src 'none'" in page
    assert json.loads((out / "graph.json").read_text())["snapshot_at"].startswith("2026-10-20")
    for name in ("app.js", "style.css", "cytoscape.min.js", "CYTOSCAPE-LICENSE.txt"):
        assert (out / name).exists()


def test_the_map_script_parses():
    """A syntax error in app.js leaves the whole page blank; check it whenever Node is available."""
    import subprocess
    import pytest
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = Path(__file__).parents[1] / "refresher" / "assets" / "map" / "app.js"
    result = subprocess.run([node, "--check", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
