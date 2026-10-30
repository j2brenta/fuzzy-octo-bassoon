import json
import os
import stat

import pytest

from refresher import mcp_server, vault
from tests.test_proposals import AT, topical_vault


@pytest.fixture
def reviewed(tmp_path):
    v = topical_vault(tmp_path)
    vault.save_review(v, vault.replay(v, include_unreviewed=True).review(AT))
    return v


def snapshot(root):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*")}


def test_the_tools_answer_a_subject_from_the_vault(reviewed):
    tools = mcp_server.tools(reviewed)
    topics = tools["list_topics"]()["topics"]
    assert topics and all({"id", "title", "counts"} <= set(t) for t in topics)
    b = tools["brief"](topic_id=topics[0]["id"])
    assert b["found"] and b["ideas"] and b["sources"]
    assert all(s["ref"].startswith("S") for s in b["sources"])
    found = tools["find_sources"](topic_id=topics[0]["id"], limit=2)
    assert 0 < len(found["sources"]) <= 2
    first = found["sources"][0]["id"]
    read = tools["read_source"](first)
    assert read["found"] and read["text"] and read["next_offset"] is None
    assert tools["read_source"]("no-such-item") == {"note": mcp_server.NOTE, "found": False}
    assert tools["brief"](topic_id="topic:no-such-topic")["found"] is False
    assert "Yarin Gal" in tools["find_sources"](query="Gal")["naming"][0]["names"]
    assert tools["list_threads"]()["threads"] == []
    with pytest.raises(ValueError):
        tools["brief"]()


def test_an_empty_vault_answers_with_nothing(tmp_path):
    tools = mcp_server.tools(tmp_path)
    assert tools["list_topics"]()["topics"] == []
    assert tools["brief"](query="anything")["found"] is False
    assert tools["find_sources"](query="anything")["sources"] == []


def test_every_answer_says_it_is_data_not_instructions(reviewed):
    tools = mcp_server.tools(reviewed)
    topic = tools["list_topics"]()["topics"][0]["id"]
    for name, call in [("list_topics", {}), ("brief", {"topic_id": topic}), ("find_sources", {"topic_id": topic}),
                       ("read_source", {"source_id": "item-g1"}), ("list_threads", {})]:
        assert tools[name](**call)["note"] == mcp_server.NOTE


def test_the_tools_write_nothing_into_a_read_only_vault(reviewed):
    tools = mcp_server.tools(reviewed)
    before = snapshot(reviewed)
    modes = {p: p.stat().st_mode for p in [reviewed, *reviewed.rglob("*")]}
    for p, mode in modes.items():
        p.chmod(stat.S_IMODE(mode) & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    try:
        topic = tools["list_topics"]()["topics"][0]["id"]
        tools["brief"](topic_id=topic)
        tools["brief"](query="GPU prices")
        for row in tools["find_sources"](topic_id=topic)["sources"]:
            tools["read_source"](row["id"])
        tools["list_threads"]()
    finally:
        for p, mode in modes.items():
            p.chmod(stat.S_IMODE(mode))
    assert snapshot(reviewed) == before


def test_http_needs_the_token(reviewed):
    pytest.importorskip("mcp")
    from starlette.testclient import TestClient
    app = mcp_server.http_app(reviewed, "t0ken")
    hello = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"}}}
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(app) as client:
        assert client.post("/mcp", json=hello, headers=headers).status_code == 401
        assert client.post("/mcp", json=hello, headers={**headers, "Authorization": "Bearer wrong"}).status_code == 401
        r = client.post("/mcp", json=hello, headers={**headers, "Authorization": "Bearer t0ken"})
        assert r.status_code == 200
        assert json.loads(r.text)["result"]["serverInfo"]["name"] == "refresher"


def test_http_does_not_start_without_a_token(reviewed, monkeypatch):
    monkeypatch.delenv("MCP_TOKEN", raising=False)
    for name in ("dotenv.load_dotenv", "refresher.cli._language", "refresher.cli._embedder"):  # .env stays out
        monkeypatch.setattr(name, lambda *a, **k: None)
    with pytest.raises(SystemExit):
        mcp_server.main(["--vault", os.fspath(reviewed), "--http", "--host", "0.0.0.0"])
