"""Collection and recovery contracts behind the UX journeys; no real vault or model calls."""
import base64
import json
from unittest.mock import patch

import pytest

from engine.artifacts import atomic_json
from refresher import library, reading
from refresher.web import Workspace
from store.raw import capture_text
from tests.test_app import served, get, send
from tests.test_proposals import AT


def test_library_keeps_raw_sources_children_and_unparsed_upload_receipts(served):
    v, base, workspace = served
    tg = capture_text("An original Telegram note", v, channel="telegram", source_id="tg:1", source_name="A channel")
    imported = capture_text("An imported note", v, channel="import", source_id="import:1", source_name="A file")
    with patch.object(workspace.worker, "submit"):
        _, job = send(base, "/api/submissions", {"text": "A web note", "title": "Browser source"})
        _, upload = send(base, "/api/submissions", {"kind": "file", "filename": "scan.png", "content": base64.b64encode(b"unparsed").decode()})
    child = capture_text("Linked article body", v, channel="import", source_id="article:1", source_name="Linked article")
    job["items"].append(child.item_id)
    workspace.save(job)
    rows = json.loads(get(base + "/api/library")[1])
    by_id = {r["id"]: r for r in rows if r["id"]}
    assert {tg.item_id, imported.item_id, job["items"][0], child.item_id} <= by_id.keys()
    assert by_id[job["items"][0]]["receipt"] == job["id"]
    assert by_id[child.item_id]["receipt"] is None
    assert [r for r in rows if r["receipt"] == upload["id"]][0]["id"] is None
    assert all("text" not in r for r in rows)


def test_source_keeps_unicode_evidence_and_reading_is_explicit(served):
    v, base, _ = served
    text = "🧠 A claim with its evidence."
    raw = capture_text(text, v, channel="import", source_id="evidence", source_name="Evidence")
    atomic_json(v / "extractions" / f"{raw.item_id}.json", {"claims": [{"statement": "A claim", "evidence": {"start_char": 2, "end_char": 9}}]})
    path = f"/api/library/source?id={raw.item_id}"
    detail = json.loads(get(base + path)[1])
    assert detail["text"] == text and detail["quality"] == "pending"
    assert detail["text"][2:9] == "A claim"
    assert detail["read"] == "unread" and not reading.load(v)["items"]
    assert send(base, "/api/reading", {"item": raw.item_id, "state": "read"})[0] == 200
    assert json.loads(get(base + path)[1])["read"] == "read"
    assert send(base, "/api/reading", {"item": raw.item_id, "state": "unread"})[0] == 200
    assert json.loads(get(base + path)[1])["read"] == "unread"
    assert library.source(v, "../../.env") is None


def test_unsupported_receipt_preserves_original_and_refuses_useless_retry(tmp_path):
    workspace = Workspace(tmp_path)
    try:
        with patch.object(workspace.worker, "submit"):
            job = workspace.submit({"kind": "file", "filename": "scan.png", "content": base64.b64encode(b"original bytes").decode()})
        workspace.process(job)
        saved = workspace.detail(job["id"])
        assert saved["recovery"] == "format" and saved["status"] == "pending"
        job.pop("recovery")  # existing vaults also receive specific recovery actions
        workspace.save(job)
        assert workspace.detail(job["id"])["recovery"] == "format"
        with pytest.raises(ValueError, match="format"):
            workspace.retry(job["id"])
        assert (tmp_path / "uploads" / saved["attachment"]["sha256"]).read_bytes() == b"original bytes"
    finally:
        workspace.worker.shutdown(wait=True)


def test_public_pages_and_receipt_bookmarks_keep_the_same_shell(served):
    _, base, _ = served
    for route in ("/", "/library/", "/example/", "/brief/?q=entropy", "/topic/?q=entropy", "/capture/?receipt=" + "a" * 32, "/atlas/"):
        status, html, headers = get(base + route)
        assert status == 200 and 'data-page="library"' in html
        assert 'data-workspace="' in html
        assert 'data-page="read"' not in html and 'href="/atlas/#review"' in html
        assert "default-src 'self'" in headers["Content-Security-Policy"]
    assert get(base + "/library")[2].get("Content-Type", "").startswith("text/html")
    assert get(base + "/example")[0] == 200
    assert 'id="brain-pause"' in get(base + "/")[1]
    assert 'id="brain-motion"' not in get(base + "/capture/")[1]
    for name in ("library.js", "journey.js", "journey.css", "brain.js", "brain.css"):
        assert get(base + "/static/" + name)[0] == 200


def test_telegram_links_only_use_an_explicit_app_origin(monkeypatch):
    monkeypatch.delenv("REFRESHER_APP_URL", raising=False)
    assert library.app_url("item-1") is None
    monkeypatch.setenv("REFRESHER_APP_URL", "https://knowledge.example/")
    assert library.app_url("item-1") == "https://knowledge.example/library/?source=item-1"
    for value in ("javascript:alert(1)", "https://user:password@example.org", "https://example.org/?token=secret", "https://example.org/path"):
        monkeypatch.setenv("REFRESHER_APP_URL", value)
        assert library.app_url("item-1") is None


def test_unparsed_media_is_visible_and_downloaded_with_integrity_checks(served):
    from channels.pdf import save_pdf
    from urllib.error import HTTPError
    v, base, _ = served
    content = b"%PDF-1.4\nsynthetic original for receipt tests\n%%EOF"
    receipt = save_pdf(content, v, {"source_id": "pdf:1", "source_name": "Saved PDF", "channel_metadata": {}})
    rows = json.loads(get(base + "/api/library")[1])
    assert any(r["id"] == receipt.stem and r["status"] == "saved" for r in rows)
    url = f"/api/library/original?id={receipt.stem}&kind=pdfs"
    assert get(base + url)[1] == content.decode()
    assert 'filename="saved-original.pdf"' in get(base + url)[2]["Content-Disposition"]
    assert json.loads(get(base + f"/api/library/source?id={receipt.stem}")[1])["original"] == {"id": receipt.stem, "kind": "pdfs"}
    derived = capture_text("PDF derived text", v, channel="telegram", source_id="pdf-derived", source_name="Read PDF", channel_metadata={"pdf_reading": {"receipt_path": f"raw/pdfs/{receipt.stem}.json"}})
    rows = json.loads(get(base + "/api/library")[1])
    assert any(r["id"] == derived.item_id for r in rows) and not any(r["id"] == receipt.stem for r in rows)
    digest = json.loads(receipt.read_text())["sha256"]
    (receipt.parent / f"{digest}.pdf").write_bytes(b"changed bytes")
    with pytest.raises(HTTPError) as error:
        get(base + url)
    assert error.value.code == 409


def test_timeline_is_a_separate_page_and_atlas_keeps_only_map_filters(served):
    _, base, _ = served
    _, atlas, _ = get(base + '/atlas/?item=item-h1')
    assert 'id="open-timeline" href="/timeline/"' in atlas
    assert 'class="timeline-column"' not in atlas and 'id="timeline"' not in atlas
    assert '<summary>Filter map</summary>' in atlas and 'id="story-filter"' in atlas
    for path in ('/timeline/', '/timeline?topic=unassigned&search=example'):
        status, html, headers = get(base + path)
        assert status == 200 and 'src="/static/timeline.js"' in html
        assert 'id="timeline-filters"' in html and 'id="timeline-back"' in html
        assert 'cytoscape' not in html and "style-src 'self'" in headers['Content-Security-Policy']
    assert get(base + '/static/timeline.js')[0] == 200
    data = json.loads(get(base + '/api/data')[1])
    assert data['items'] and all('time_basis' in item for item in data['items'])


def test_link_receipt_and_fetched_article_share_one_library_row(tmp_path):
    url = 'https://example.org/article#section'
    receipt = 'a' * 32
    parent = capture_text(url, tmp_path, channel='app', source_id='submission', source_name=url,
                          channel_metadata={'submission_id': receipt})
    child = capture_text('The actual article body.', tmp_path, channel='import', source_id='article', source_name='Blog name',
                         url='https://example.org/article', channel_metadata={
                             'parent': {'item_id': parent.item_id},
                             'web_article': {'title': 'The article title'}})
    job = {'id': receipt, 'kind': 'link', 'items': [parent.item_id, child.item_id], 'title': url,
           'status': 'complete', 'stage': 'Finished', 'links': [
               {'status': 'captured', 'item_id': child.item_id, 'url': url}]}
    rows = library.catalog(tmp_path, [job])
    assert [r['id'] for r in rows] == [child.item_id]
    assert rows[0]['title'] == 'The article title' and rows[0]['receipt'] == receipt
    assert library.source(tmp_path, child.item_id)['receipt'] == receipt
    assert library.source(tmp_path, parent.item_id)['text'] == url
    # Notes containing commentary plus a URL remain separate evidence.
    raw = json.loads(parent.path.read_text())
    raw['text'] = 'My notes about this article: ' + url
    atomic_json(parent.path, raw)
    assert len(library.catalog(tmp_path, [job])) == 2
    # A failed fetch keeps the saved URL visible.
    job['links'][0]['status'] = 'failed'
    assert any(r['id'] == parent.item_id for r in library.catalog(tmp_path, [job]))
