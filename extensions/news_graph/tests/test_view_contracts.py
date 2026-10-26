"""CPU-only real-browser regressions; synthetic export, no product network."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import json

import pytest
from newsmap_sidecar import review as rv
from newsmap_sidecar.export import export_view
from test_review import prepare, proposal, approve, context


@pytest.fixture
def browser_view(workspace, tmp_path):
    from playwright.sync_api import sync_playwright
    db, vault = workspace
    refs = prepare(db, vault)
    first = proposal(db, refs)
    rv.review(db, first, 'reject', rv.show(db, first)['token'], 'Rejected fixture', 'fixture_operator')
    second = rv.propose(db, *reversed(refs), 'SUPPORTS', {**context(), 'dataset': 'SELECTED_REVERSE'},
                        'Selected reverse relation', refs, [], 'fixture_operator')
    approve(db, second)
    out = tmp_path / 'export'
    export_view(db, out)
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(out)))
    Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    errors, external = [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path='/usr/bin/google-chrome', headless=True,
                                       args=['--disable-gpu'])
            page = browser.new_page(timezone_id='America/Los_Angeles')
            page.on('pageerror', lambda e: errors.append(str(e)))
            def route(r):
                if r.request.url.startswith(base + '/'): r.continue_()
                else: external.append(r.request.url); r.abort()
            page.route('**/*', route)
            page.goto(base, wait_until='networkidle')
            yield page, out, base
            browser.close()
        assert not errors and not external
    finally:
        server.shutdown(); server.server_close()


def test_exact_edge_manual_pair_and_excluding_filter(browser_view):
    page, _, _ = browser_view
    page.locator('#mode-claims').click()
    selected = page.evaluate("() => {const a=window.newsmap; const e=a.data.edges.find(e=>e.context?.dataset==='SELECTED_REVERSE'); a.selectEdge(e); return e;}")
    assert 'SELECTED_REVERSE' in page.locator('#comparison').inner_text()
    assert page.evaluate('window.newsmap.state.comparison') == [selected['source'], selected['target']]
    assert page.locator('#comparison .context-table').count() == 1  # pair context only once
    page.locator('#review-filter').select_option('proposal')
    assert 'Selected reverse relation' not in page.locator('#detail').inner_text()
    assert 'SELECTED_REVERSE' not in page.locator('#comparison').inner_text()
    page.locator('#clear-compare').click()
    page.evaluate("() => {const a=window.newsmap; a.selectNode(a.data.claims[0].id);}")
    page.locator('#detail [data-compare]').click()
    page.evaluate("() => {const a=window.newsmap; a.selectNode(a.data.claims[1].id);}")
    page.locator('#detail [data-compare]').click()
    assert page.locator('#comparison .context-table').count() == 0
    assert 'SELECTED_REVERSE' not in page.locator('#comparison').inner_text()


def test_dense_edges_bounded_with_inspection_pages(browser_view):
    page, out, base = browser_view
    # Same authorized DTO, many independent decisions between a small node set.
    page.evaluate("""() => {const a=window.newsmap,e=a.data.edges[0];
      for(let i=0;i<90;i++) a.data.edges.push({...e,id:'dense-'+i}); a.refresh();}""")
    page.locator('#mode-claims').click()
    assert page.evaluate('window.newsmap.cy.edges().length') <= 60
    assert page.locator('#graph-list button').count() <= 81
    assert '32' in page.locator('#hidden-count').inner_text()
    assert page.get_by_role('button', name='Следующая страница связей', include_hidden=True).count() == 1


def test_time_instants_utc_and_invalid_warning(browser_view):
    page, _, _ = browser_view
    page.evaluate("""() => {const a=window.newsmap;
       a.data.items[0].time='2026-10-26T23:30:00-02:00';
       a.data.items[1].time='2026-10-27T00:30:00+00:00'; a.refresh();}""")
    assert page.evaluate('window.newsmap.filteredItems()[0].id') == page.evaluate('window.newsmap.data.items[0].id')
    page.locator('#date-filter').fill('2026-10-27')
    assert page.locator('#timeline .item-button').count() == 2
    assert 'UTC' in page.locator('#timeline').inner_text()
    page.locator('#date-filter').fill('')
    page.evaluate("window.newsmap.data.items[0].time='not-a-date'; window.newsmap.refresh();")
    assert 'Некорректная дата' in page.locator('#timeline').inner_text()


def test_macbook_sized_and_mobile_viewports(browser_view):
    page,_,_=browser_view
    for width,height in [(1440,900),(1280,800),(390,844)]:
        page.set_viewport_size({'width':width,'height':height})
        page.locator('#timeline .item-button').first.click()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert page.locator('#detail .quote mark').count() >= 2
        page.locator('#fit').click()
        assert page.evaluate('window.newsmap.cy.zoom()') > 0
