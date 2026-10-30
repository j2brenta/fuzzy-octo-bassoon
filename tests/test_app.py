import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from refresher import app, graphview, vault
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


def assert_loads_in_order(html, tags):
    """Every part of a page's script is on the page, core first and main last: each reads what the earlier ones made."""
    assert all(tag in html for tag in tags), [tag for tag in tags if tag not in html]
    assert [html.index(tag) for tag in tags] == sorted(html.index(tag) for tag in tags)


def test_both_pages_are_served_with_their_assets_and_one_shell(served):
    v, base, _ = served
    status, html, headers = get(base + "/atlas/")
    assert status == 200 and "connect-src 'self'" in html and '"served": true' in html
    assert "'unsafe-inline'" in headers["Content-Security-Policy"]
    for name in (*graphview.ATLAS_SCRIPTS, "style.css", "cytoscape.min.js", "shell.css", "nav.js"):
        assert get(f"{base}/atlas/{name}")[0] == 200
    assert_loads_in_order(html, [f'src="{name}"' for name in graphview.ATLAS_SCRIPTS])
    for page, folder, parts in (("/brief/", "brief", ("core", "ideas", "versions", "area", "source", "side", "reading", "who", "main")),
                                ("/topic/", "topic", ("core", "graph", "map", "results", "reading", "text", "panel", "main"))):
        for part in parts:
            assert get(f"{base}/static/{folder}/{part}.js")[0] == 200
        assert_loads_in_order(get(base + page)[1], [f'src="/static/{folder}/{part}.js"' for part in parts])
    status, html, headers = get(base + "/capture/")
    assert status == 200 and 'href="shell.css"' in html and 'id="app-nav"' in html
    assert "style-src 'self'" in headers["Content-Security-Policy"] and "unsafe-inline" not in headers["Content-Security-Policy"]
    for name in ("app.js", "app.css", "brain.png", "shell.css", "nav.js"):
        assert get(f"{base}/capture/{name}")[0] == 200
    assert 'src="/static/topics.js"' in get(base + "/")[1]  # / is the topics
    assert "frame-ancestors 'self'" in get(base + "/atlas/")[2]["Content-Security-Policy"]  # a dossier embeds it
    for name in ("brief.css", "topics.js", "topic/core.js", "brief/main.js", "shell.css", "nav.js"):
        assert get(f"{base}/static/{name}")[0] == 200
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


def test_topics_and_dossiers_are_served(served):
    v, base, _ = served
    topic = next(p for p in json.loads(get(base + "/api/proposals")[1]) if p["kind"] == "topic")
    assert post(base, topic["id"], {"action": "accept", "token": topic["token"]})[0] == 200
    rows = json.loads(get(base + "/api/topics")[1])
    accepted = next(r for r in rows if r["kind"] == "topic")
    assert accepted["title"] == topic["title"] and accepted["counts"]["ideas"] >= 3
    status, page, headers = get(f"{base}/topic/?id={accepted['id']}")
    assert status == 200 and 'src="/static/topic/main.js"' in page and 'id="reading"' in page
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    for name in ("topic/main.js", "reading.css", "brief.css", "cytoscape.min.js"):
        assert get(f"{base}/static/{name}")[0] == 200
    d = json.loads(get(f"{base}/api/dossier?topic={accepted['id']}")[1])
    assert d["scope"]["id"] == accepted["id"] and d["ideas"][0]["tier"] == "core" and d["sources"]
    assert d["documents"] and d["documents"][0]["key"] and d["threads"] and "people" in d["people"]
    assert d["reading"] == {"items": {}, "last_visit": None}
    d = json.loads(get(f"{base}/api/dossier?q=GPU+rental+prices&at=2026-11-01")[1])
    assert d["scope"]["kind"] == "query" and d["at"].startswith("2026-11-01")
    for bad in ("q=medieval+falconry", "topic=topic:nope", "q=GPU&at=yesterday"):
        with pytest.raises(HTTPError) as error:
            get(f"{base}/api/dossier?{bad}")
        assert error.value.code in (400, 404)
    assert get(f"{base}/topic?id=x")[0] == 200  # redirected to /topic/ with the query kept


def test_requests_at_the_same_moment_see_the_whole_graph(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    v = topical_vault(tmp_path)
    vault.save_review(v, vault.replay(v, include_unreviewed=True).review(AT))
    state = app.State(v)
    expected = len(list(vault.replay(v, include_unreviewed=True).clusters()))
    assert expected
    with ThreadPoolExecutor(8) as pool:  # a page load asks for the map, the topics and a dossier together
        maps = list(pool.map(lambda n: state.map() if n % 2 else state.catalog() and state.map(), range(16)))
    assert all(len(m["clusters"]) == expected for m in maps)


def test_consolidate_runs_from_the_page_one_at_a_time_and_shows_the_last_run(tmp_path):
    from refresher import consolidate
    v = topical_vault(tmp_path)
    go = threading.Event()

    def run():  # lexical, so no model; held until the test lets it finish
        go.wait(10)
        consolidate.consolidate(v, at=AT)
    review = app.Review(v, run=run)
    workspace = Workspace(v, place=lambda item_id, client: {"item": item_id, "status": "placed"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), app.handler(v, workspace, app.State(v), log=lambda m: None,
                                                               review=review))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        before = json.loads(get(base + "/api/consolidate")[1])
        assert before["last"] is None and not before["running"] and before["pending"]
        status, started = send(base, "/api/consolidate", {})
        assert status == 202 and started["running"]
        status, again = send(base, "/api/consolidate", {})
        assert status == 409 and again["error"] == "A review is already running."
        go.set()
        review._thread.join(10)
        after = json.loads(get(base + "/api/consolidate")[1])
        assert not after["running"] and after["error"] is None and not after["pending"]
        assert after["last"]["proposed"] == 4 and after["last"]["at"] == AT.isoformat()
        assert after["next_at"] == (AT + consolidate.EVERY).isoformat()
        assert send(base, "/api/consolidate", {}, {"X-Refresher": "0"})[0] == 403  # only from the app itself
    finally:
        server.shutdown()
        server.server_close()
        workspace.worker.shutdown(wait=True)


def test_a_failed_run_from_the_page_is_shown(tmp_path):
    def run():
        raise OSError("disk full")
    review = app.Review(topical_vault(tmp_path), run=run)
    assert review.start()
    review._thread.join(10)
    assert review.status()["error"] == "OSError: disk full" and not review.status()["running"]


def test_the_reader_gets_a_source_and_marks_what_you_read(served):
    v, base, _ = served
    d = json.loads(get(f"{base}/api/dossier?q=hallucination")[1])
    first = d["documents"][0]["id"]
    src = json.loads(get(f"{base}/api/document?id={first}")[1])
    assert src["id"] == first and src["text"] and src["spans"] and all(s["start"] < s["end"] for s in src["spans"])
    with pytest.raises(HTTPError) as error:
        get(f"{base}/api/document?id=nope")
    assert error.value.code == 404
    assert send(base, "/api/reading", {"item": first, "state": "read"})[0] == 200
    assert send(base, "/api/reading", {"visit": d["scope"]["id"]})[0] == 200
    assert send(base, "/api/reading", {"item": first, "state": "loved"})[0] == 400
    assert send(base, "/api/reading", {"item": first, "state": "read"}, {"X-Refresher": "0"})[0] == 403
    again = json.loads(get(f"{base}/api/dossier?q=hallucination")[1])
    assert again["reading"]["items"][first]["state"] == "read"
    assert again["reading"]["last_visit"] is None  # the visit just made is this sitting, not the last one
    assert (v / "reading" / "log.jsonl").exists()


def test_previous_graph_url_redirects_to_atlas_with_query(served):
    _, base, _ = served
    with urlopen(base + '/graph/?mode=people') as response:
        assert response.geturl() == base + '/atlas/?mode=people'
        assert b'id="mode-people"' in response.read()


def test_people_review_controls_save_reset_and_reject_stale_or_cross_origin(served):
    from tests.test_people_review import write
    v,base,_=served
    write(v,'speaker')
    assert get(base+'/atlas/people.js')[0]==200
    path='/api/people/attribution'
    ctx=json.loads(get(base+path+'?claim=speaker-claim')[1])
    body={'claim_id':'speaker-claim','token':ctx['token'],'action':'review',
          'review':{'kind':'unknown','person':None,'passages':[], 'identity_url':None,'origin_url':None,'origin_checked':False}}
    assert send(base,path,body,{'Origin':'https://elsewhere.test'})[0]==403
    assert send(base,path,body)[0]==200
    claim=next(c for c in json.loads(get(base+'/api/data')[1])['claims'] if c['id']=='speaker-claim')
    assert claim['speaker'] is None and claim['attribution_reviewed']
    assert send(base,path,body)[0]==409
    ctx=json.loads(get(base+path+'?claim=speaker-claim')[1])
    assert send(base,path,{'claim_id':'speaker-claim','token':ctx['token'],'action':'reset'})[0]==200
    claim=next(c for c in json.loads(get(base+'/api/data')[1])['claims'] if c['id']=='speaker-claim')
    assert claim['speaker'] and not claim['attribution_reviewed']
    assert send(base,path,dict(body,review={}))[0] in {400,409}


def test_people_backfill_http_preview_and_progress_use_only_selected_documents(served):
    from tests.test_people_review import write, response
    from tests.test_triage import FakeModel
    from refresher import people_backfill
    v,base,workspace=served
    write(v,'old-speaker');(v/'relations/old-speaker.json').unlink()
    model=FakeModel([response()])
    with patch.object(people_backfill,'judge_client_from_environment',return_value=model):
        preview=json.loads(get(base+'/api/people/backfill')[1])
        assert model.calls==[]
        body={'token':preview['token'],'item_ids':['old-speaker'],'confirm_paid':True}
        assert send(base,'/api/people/backfill',dict(body,confirm_paid=False))[0]==400
        status,job=send(base,'/api/people/backfill',body)
        assert status==202
        workspace.worker.shutdown(wait=True)
        done=json.loads(get(base+'/api/people/backfill/'+job['id'])[1])
        assert done['status']=='complete' and done['completed']==['old-speaker']
    assert len(model.calls)==1
