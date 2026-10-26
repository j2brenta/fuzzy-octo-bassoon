"""Browser security/offline smoke on fresh hostile synthetic data and producer export."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from threading import Thread

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from newsmap_sidecar.common import outside_repositories, require, fingerprint
from newsmap_sidecar.demo import record, write
from newsmap_sidecar.storage import Database
from newsmap_sidecar.intake import import_batch
from newsmap_sidecar.export import export_view


def main():
    root = outside_repositories(sys.argv[1])
    producer_view = Path(sys.argv[2]).resolve()
    require(not root.exists(), 'fresh browser safety directory required')
    root.mkdir(parents=True, mode=0o700)
    vault = root / 'vault'; vault.mkdir()
    r, raw = record('hostile', '</script><script>window.PWNED=1</script>',
                    '<img src="https://example.invalid/egress" onerror="window.PWNED=2">',
                    link='https://example.invalid/reference')
    r['source']['url'] = 'javascript:window.PWNED=3'
    r['bundle_id'] = fingerprint({k: v for k, v in r.items() if k not in {'bundle_id', 'created_at'}})
    write(vault, r, raw)
    db = Database(root / 'authority' / 'news.sqlite', create=True)
    try:
        db.register('hostile-synthetic', vault, actor='fixture_operator')
        assert import_batch(db, 'hostile-synthetic', vault)['valid'] == 1
        export_view(db, root / 'viewer')
        for index in range(24):
            extra, raw_extra = record('bounded-' + str(index), 'Synthetic item ' + str(index), 'Synthetic qualifier, no automatic semantic assertion.')
            write(vault, extra, raw_extra)
        import_batch(db, 'hostile-synthetic', vault)
        export_view(db, root / 'bounded-viewer')
    finally:
        db.close()
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
    receipts = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path='/usr/bin/google-chrome', headless=True, args=['--disable-gpu'])
        for name, folder in [('hostile', root / 'viewer'), ('producer', producer_view), ('bounded', root / 'bounded-viewer')]:
            server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(folder)))
            Thread(target=server.serve_forever, daemon=True).start()
            base = f'http://127.0.0.1:{server.server_port}'
            errors, external = [], []
            page = browser.new_page(viewport={'width': 1440, 'height': 1050})
            def route(request):
                if not request.request.url.startswith(base + '/'):
                    external.append(request.request.url); request.abort()
                else: request.continue_()
            page.route('**/*', route)
            page.on('pageerror', lambda e: errors.append(str(e)))
            try:
                page.goto(base + '/', wait_until='networkidle')
                page.locator('#timeline .item-button').first.click()
                assert page.locator('#detail .quote mark').count() == 2
                assert not page.evaluate('Boolean(window.PWNED)')
                assert page.locator('a[href^="javascript:"]').count() == 0
                assert page.locator('#detail img,#detail script').count() == 0
                if name == 'hostile':
                    assert '<script>' in page.locator('#detail').inner_text()
                elif name == 'producer':
                    assert 'mobile' in page.locator('#detail').inner_text()
                    assert 'Alex Example' in page.locator('#detail').inner_text()
                else:
                    assert page.evaluate('window.newsmap.cy.nodes(":childless").length') == 20
                    assert 'Скрыто' in page.locator('#hidden-count').inner_text()
                    hidden = page.evaluate('window.newsmap.data.items.find(i=>!window.newsmap.cy.getElementById(i.id).length).id')
                    page.locator('[data-item="' + hidden + '"]').click()
                    assert page.evaluate('(id)=>window.newsmap.cy.getElementById(id).length', hidden) == 1
                # Load and inspect with browser network disabled after initial local assets.
                page.context.set_offline(True)
                page.locator('#mode-claims').click()
                page.locator('#timeline .item-button').first.click()
                assert page.locator('#detail .quote mark').count() == 2
                assert not errors and not external
                page.screenshot(path=str(root / (name + '.png')), full_page=True)
                receipts.append({'scenario': name, 'script_execution': False, 'external_requests': external,
                                 'page_errors': errors, 'offline_evidence_drilldown': 'passed'})
            finally:
                page.close(); server.shutdown(); server.server_close()
        browser.close()
    (root / 'browser-safety.json').write_text(json.dumps(receipts, indent=2))
    print(json.dumps(receipts, indent=2))


if __name__ == '__main__':
    main()
