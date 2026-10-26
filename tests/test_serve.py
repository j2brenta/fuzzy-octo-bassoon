import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from refresher import serve, vault
from tests.test_proposals import AT, topical_vault, write


@pytest.fixture
def served(tmp_path):
    v = topical_vault(tmp_path)
    report = vault.replay(v, include_unreviewed=True).review(AT)
    vault.save_review(v, report)
    server = ThreadingHTTPServer(("127.0.0.1", 0), serve.handler(v, log=lambda m: None))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield v, f"http://127.0.0.1:{server.server_address[1]}", report
    server.shutdown()
    server.server_close()


def get(url, **headers):
    with urlopen(Request(url, headers=headers)) as r:
        return r.status, r.read().decode(), r.headers


def post(base, pid, body, headers=None):
    data = json.dumps(body).encode()
    h = {"Content-Type": "application/json", "X-Refresher": "1", "Origin": base, **(headers or {})}
    try:
        with urlopen(Request(f"{base}/api/proposals/{pid}", data=data, headers=h, method="POST")) as r:
            return r.status, json.loads(r.read())
    except HTTPError as error:
        return error.code, json.loads(error.read())


def test_the_page_is_served_with_its_assets_and_may_post_back(served):
    v, base, _ = served
    status, html, _ = get(base + "/")
    assert status == 200 and "connect-src 'self'" in html and '"served": true' in html
    for name in ("app.js", "style.css", "cytoscape.min.js"):
        assert get(f"{base}/{name}")[0] == 200
    proposals = json.loads(get(base + "/api/proposals")[1])
    assert {p["status"] for p in proposals} == {"proposed"} and len(proposals) == 4


def test_accept_applies_and_rebuilds_and_an_old_token_is_refused(served):
    v, base, report = served
    topic = next(p for p in json.loads(get(base + "/api/proposals")[1]) if p["kind"] == "topic")
    status, body = post(base, topic["id"], {"action": "accept", "token": topic["token"], "rationale": "yes"})
    assert status == 200 and body["action"] == "accept"
    assert any(vault.replay(v, include_unreviewed=True).topics())
    assert topic["title"] in (v / "wiki" / "graph" / "graph.json").read_text()
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
    with pytest.raises(HTTPError) as error:
        get(base + "/", Host="attacker.example")
    assert error.value.code == 403
    assert vault.load_decisions(v) == []
