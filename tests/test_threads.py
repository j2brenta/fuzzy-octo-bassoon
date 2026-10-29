from datetime import datetime, timedelta, timezone

from refresher import threads
from tests.test_context import dense  # noqa: F401 - the fixture

T1 = datetime(2026, 10, 20, tzinfo=timezone.utc)
T2 = datetime(2026, 10, 29, tzinfo=timezone.utc)


def arrive(store, late: set[str]):
    """Everything is captured on 19 October except `late`, captured on the 25th: after the stamp at T1."""
    for i in late:
        item = store.item(i)
        item.source = item.source.model_copy(update={"captured_at": T1 + timedelta(days=5)})


def test_a_stamp_is_kept_and_what_arrives_after_it_is_sorted(dense, tmp_path):  # noqa: F811
    arrive(dense, {"blog", "lender", "german", "fix"})
    t = threads.follow(tmp_path, query="semantic entropy confabulations", at=T1)
    assert threads.follow(tmp_path, query="semantic entropy confabulations")["created"] == t["created"]
    snap = threads.stamp(tmp_path, dense, t["slug"], T1)
    assert "blog" not in snap["items"] and "paper" in snap["items"]  # as of T1, the post had not reached you
    assert (tmp_path / "threads" / t["slug"] / f"{snap['stamp']}.md").exists()
    assert (tmp_path / "wiki" / "briefs" / f"{t['slug']}.md").exists()
    v = threads.view(tmp_path, dense, t["slug"], T2)
    d = v["delta"]
    assert d["since"] == T1.isoformat() and [s["stamp"] for s in v["stamps"]] == [snap["stamp"]]
    kinds = {r["item"]: r["kind"] for r in d["critical"] + d["notes"]}
    assert kinds.get("blog") == "builds on" and kinds.get("lender") == "lends facts"
    listed = {r["item"] for rows in d["noise"].values() for r in rows} | set(kinds)
    assert "german" not in listed  # off the thread: not listed at all
    assert not d["critical"]


def test_a_correction_after_the_stamp_is_critical_and_an_old_view_is_noise(dense, tmp_path):  # noqa: F811
    arrive(dense, {"fix"})
    t = threads.follow(tmp_path, query="confabulation workshop time", at=T1)
    threads.stamp(tmp_path, dense, t["slug"], T1)
    d = threads.view(tmp_path, dense, t["slug"], T2)["delta"]
    [c] = d["critical"]
    assert c["item"] == "fix" and c["kind"] == "updates" and c["about"] == "old:t1"


def test_an_original_arriving_after_its_correction_is_already_replaced(dense, tmp_path):  # noqa: F811
    arrive(dense, {"old"})  # the 14:00 announcement reaches you after the 16:00 correction
    t = threads.follow(tmp_path, query="confabulation workshop time", at=T1)
    threads.stamp(tmp_path, dense, t["slug"], T1)
    d = threads.view(tmp_path, dense, t["slug"], T2)["delta"]
    assert not d["critical"] and [r["item"] for r in d["noise"]["outdated"]] == ["old"]


def test_a_repost_after_the_stamp_is_noise_and_the_home_lists_threads(dense, tmp_path):  # noqa: F811
    arrive(dense, {"repost"})
    t = threads.follow(tmp_path, query="semantic entropy confabulations", at=T1)
    first = threads.stamp(tmp_path, dense, t["slug"], T1)
    d = threads.view(tmp_path, dense, t["slug"], T2)["delta"]
    assert [r["item"] for r in d["noise"]["retells"]] == ["repost"]
    [row] = threads.summary(tmp_path, dense, T2)
    assert row["slug"] == t["slug"] and row["stamps"] == 1 and row["noise"] == 1 and row["critical"] == 0
    second = threads.stamp(tmp_path, dense, t["slug"], T2)
    assert threads.view(tmp_path, dense, t["slug"], T2)["delta"]["arrived"] == 0
    change = threads.diff(first, second)
    assert change["from"] == first["stamp"] and change["to"] == second["stamp"]


def test_the_app_serves_a_brief_follows_and_stamps(tmp_path, monkeypatch):
    import json
    import threading
    from http.server import ThreadingHTTPServer
    from refresher import app
    from refresher.web import Workspace
    from tests.test_app import get, send
    from tests.test_proposals import topical_vault
    from refresher import brief
    monkeypatch.setattr(brief, "client_from_environment", lambda: None)  # the cited theses, no model
    v = topical_vault(tmp_path)
    workspace = Workspace(v, place=lambda item_id, client: {"item": item_id, "status": "placed"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler(v, workspace, app.State(v), log=lambda m: None))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, body = get(base + "/brief/?q=hallucination+detection")[:2]
        assert status == 200
        b = json.loads(get(base + "/api/brief?q=hallucination+detection")[1])
        assert b["major"] and b["written"]["ids"] and b["thread"] is None and "people" in b
        md = get(base + "/api/brief.md?q=hallucination+detection")[1]
        assert md.startswith("# hallucination detection")
        t = send(base, "/api/threads", {"q": "hallucination detection"})[1]
        status, snap = send(base, f"/api/threads/{t['slug']}/stamp", {})
        assert status == 200, snap
        assert snap["major"] and "items" not in snap
        b = json.loads(get(base + "/api/brief?q=hallucination+detection")[1])
        assert b["thread"]["delta"]["arrived"] == 0 and len(b["thread"]["stamps"]) == 1
        [row] = json.loads(get(base + "/api/threads")[1])
        assert row["slug"] == t["slug"] and row["stamps"] == 1
        snaps = json.loads(get(base + f"/api/threads/{t['slug']}/snapshots")[1])
        assert snaps["snapshots"][0]["stamp"] == snap["stamp"]
    finally:
        server.shutdown()
        server.server_close()
        workspace.worker.shutdown(wait=True)
