import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from refresher import app, vault
from refresher.web import Workspace
from tests.test_extract import TEXT
from tests.test_proposals import AT, topical_vault, write


@pytest.fixture
def served(tmp_path):
    v = topical_vault(tmp_path)
    report = vault.replay(v, include_unreviewed=True).review(AT)
    vault.save_review(v, report)
    workspace = Workspace(v, place=lambda item_id, client: {"item": item_id, "status": "placed"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler(v, workspace, app.State(v), log=lambda m: None))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield v, f"http://127.0.0.1:{server.server_address[1]}", workspace
    server.shutdown()
    server.server_close()
    workspace.worker.shutdown(wait=True)


def get(url, **headers):
    with urlopen(Request(url, headers=headers)) as r:
        return r.status, r.read().decode(errors="replace"), r.headers


def send(base, path, body, headers=None):
    data = json.dumps(body).encode()
    h = {"Content-Type": "application/json", "X-Refresher": "1", "Origin": base, **(headers or {})}
    try:
        with urlopen(Request(base + path, data=data, headers=h, method="POST")) as r:
            return r.status, json.loads(r.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def post(base, pid, body, headers=None):
    return send(base, f"/api/proposals/{pid}", body, headers)


def test_both_pages_are_served_with_their_assets_and_one_shell(served):
    v, base, _ = served
    status, html, headers = get(base + "/atlas/")
    assert status == 200 and "connect-src 'self'" in html and '"served": true' in html
    assert "'unsafe-inline'" in headers["Content-Security-Policy"]
    for name in ("app.js", "style.css", "cytoscape.min.js", "shell.css", "nav.js"):
        assert get(f"{base}/atlas/{name}")[0] == 200
    status, html, headers = get(base + "/capture/")
    assert status == 200 and 'href="shell.css"' in html and 'id="app-nav"' in html
    assert "style-src 'self'" in headers["Content-Security-Policy"] and "unsafe-inline" not in headers["Content-Security-Policy"]
    for name in ("app.js", "app.css", "brain.png", "shell.css", "nav.js"):
        assert get(f"{base}/capture/{name}")[0] == 200
    assert '"served": true' in get(base + "/")[1]  # / leads to the Atlas
    for path in ("/app.js", "/capture/../../.env", "/atlas/index.html", "/static/x"):
        with pytest.raises(HTTPError) as error:
            get(base + path)
        assert error.value.code == 404
    proposals = json.loads(get(base + "/api/proposals")[1])
    assert {p["status"] for p in proposals} == {"proposed"} and len(proposals) == 4


def test_accept_applies_and_rebuilds_and_an_old_token_is_refused(served):
    v, base, _ = served
    topic = next(p for p in json.loads(get(base + "/api/proposals")[1]) if p["kind"] == "topic")
    status, body = post(base, topic["id"], {"action": "accept", "token": topic["token"], "rationale": "yes"})
    assert status == 200 and body["action"] == "accept"
    assert any(vault.replay(v, include_unreviewed=True).topics())
    assert topic["title"] in (v / "wiki" / "graph" / "graph.json").read_text()
    assert (v / "wiki" / "graph" / "shell.css").exists()  # the offline copy keeps the shared look
    # the cached state saw the decision: the page shows it without a restart
    after = next(p for p in json.loads(get(base + "/api/proposals")[1]) if p["id"] == topic["id"])
    assert after["status"] == "accepted"
    status, body = post(base, topic["id"], {"action": "reject", "token": topic["token"]})
    assert status == 409 and "look again" in body["error"]


def test_evidence_changed_after_the_page_loaded_is_refused(served):
    v, base, _ = served
    merge = next(p for p in json.loads(get(base + "/api/proposals")[1]) if p["kind"] == "merge_ideas")
    changed = next(ex for ex, _ in vault.load_extractions(v) if ex.item_id == "item-m2").model_copy(deep=True)
    changed.claims[0].statement = "Chat assistants rarely cite sources."
    write(v, changed)
    status, body = post(base, merge["id"], {"action": "accept", "token": merge["token"]})
    assert status == 409
    assert vault.load_decisions(v) == []


def test_requests_from_elsewhere_are_refused(served):
    v, base, _ = served
    p = json.loads(get(base + "/api/proposals")[1])[0]
    good = {"action": "accept", "token": p["token"]}
    assert post(base, p["id"], good, {"Origin": "https://evil.example"})[0] == 403
    assert post(base, p["id"], good, {"X-Refresher": "0"})[0] == 403
    assert post(base, p["id"], good, {"Host": "evil.example"})[0] == 403
    assert post(base, "p-0000000000000000", good)[0] == 404
    assert post(base, p["id"], {"action": "explode", "token": p["token"]})[0] in (400, 409)
    assert send(base, "/api/submissions", {"text": "hello"}, {"Origin": "https://evil.example"})[0] == 403
    assert send(base, "/api/submissions", {"text": "hello"}, {"X-Refresher": "0"})[0] == 403
    for path in ("/atlas/", "/api/submissions"):
        with pytest.raises(HTTPError) as error:
            get(base + path, Host="attacker.example")
        assert error.value.code == 403
    assert vault.load_decisions(v) == []
    assert not (v / "submissions").exists()


def test_a_published_host_is_accepted_only_when_allowed(tmp_path):
    v = topical_vault(tmp_path)
    workspace = Workspace(v)
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler(v, workspace, app.State(v), ["localhost:9000"],
                                                               log=lambda m: None))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert get(base + "/api/submissions", Host="localhost:9000")[0] == 200
        with pytest.raises(HTTPError):
            get(base + "/api/submissions", Host="localhost:9001")
    finally:
        server.shutdown()
        server.server_close()
        workspace.worker.shutdown(wait=True)


def test_a_capture_goes_into_the_same_vault_as_the_map(served):
    v, base, workspace = served
    with patch.object(workspace.worker, "submit"):
        status, job = send(base, "/api/submissions", {"text": TEXT, "title": "From the web"})
    assert status == 202
    assert (v / "raw" / f"{job['items'][0]}.json").exists() and (v / "submissions" / f"{job['id']}.json").exists()
    status, detail, _ = get(f"{base}/api/submissions/{job['id']}")
    assert json.loads(detail)["results"][0]["text"] == TEXT


def test_the_state_is_replayed_only_when_a_file_it_reads_changes(tmp_path):
    v = topical_vault(tmp_path)
    state = app.State(v)
    with patch.object(vault, "replay", wraps=vault.replay) as replay:
        first = state.store()
        assert state.store() is first and replay.call_count == 1
        (v / "wiki" / "index.md").parent.mkdir(exist_ok=True)
        (v / "wiki" / "index.md").write_text("not read by replay")
        assert state.store() is first and replay.call_count == 1
        changed = next(ex for ex, _ in vault.load_extractions(v) if ex.item_id == "item-m2").model_copy(deep=True)
        changed.claims[0].statement = "Chat assistants rarely cite sources."
        write(v, changed)
        assert state.store() is not first and replay.call_count == 2


def test_a_processed_capture_is_placed_in_the_graph(tmp_path):
    from engine.llm import ModelReply
    from tests.test_extract import response
    from tests.test_triage import FakeModel, LABELS
    placed = []
    workspace = Workspace(tmp_path, place=lambda item_id, client: placed.append(item_id) or {"item": item_id,
                                                                                            "status": "placed"})
    try:
        with patch.object(workspace.worker, "submit"):
            job = workspace.submit({"text": TEXT, "title": "Placed"})
        client = FakeModel([ModelReply(LABELS), response()])
        with patch("engine.llm.client_from_environment", return_value=client), \
                patch("engine.llm.judge_client_from_environment", return_value=None), \
                patch("engine.entities.entity_client_from_environment", return_value=None), \
                patch.dict("os.environ", {"WEB_FETCH": "off"}):
            workspace.process(job)
        assert placed == job["items"]
        assert workspace.detail(job["id"])["placed"] == [{"item": job["items"][0], "status": "placed"}]
    finally:
        workspace.worker.shutdown(wait=True)


def test_the_placer_places_with_the_vaults_own_pipeline(tmp_path):
    v = topical_vault(tmp_path)
    with patch.dict("os.environ", {"GRAPH_RELATOR": "lexical"}), \
            patch("refresher.translate.client_from_environment", return_value=None):
        result = app.placer(v)("item-gal", None)
    assert result["status"] == "placed" and result["claims"] == 1 and result["ideas"] == 1
    assert (v / "relations" / "item-gal.json").exists()
