"""Explicit browser acceptance on a generated synthetic export (not auto-collected).

python browser_check.py EXPORT_DIRECTORY REPORT_DIRECTORY [CHROME_EXECUTABLE]
Requires Playwright only in the independent development environment.
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
from threading import Thread

from playwright.sync_api import sync_playwright


def main():
    root, reports = [Path(p).resolve() for p in sys.argv[1:3]]
    reports.mkdir(parents=True, exist_ok=True)
    chrome = sys.argv[3] if len(sys.argv) > 3 else '/usr/bin/google-chrome'
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(root)))
    Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    requests, errors, external, checks = [], [], [], []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=chrome, headless=True)
            page = browser.new_page(viewport={'width': 1500, 'height': 1100}, device_scale_factor=1)
            def route(request):
                url = request.request.url
                if not url.startswith(base + '/'):
                    external.append(url)
                    request.abort()
                else:
                    request.continue_()
            page.route('**/*', route)
            page.on('request', lambda r: requests.append(r.url))
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(base + '/', wait_until='networkidle')
            assert page.evaluate('window.newsmap.cy.nodes(":childless").length') <= 20
            assert page.evaluate('window.newsmap.cy.nodes(":parent").length') >= 2
            checks.append('bounded graph and distinct thematic regions rendered')
            page.screenshot(path=str(reports / 'overview.png'), full_page=True)
            page.locator('#timeline .item-button').first.click()
            assert page.locator('#detail .quote mark').count() == 2
            assert page.evaluate('window.newsmap.cy.elements(".highlight").length') > 0
            checks.append('timeline selection highlights graph and exposes all retained spans')
            old_zoom = page.evaluate('window.newsmap.cy.zoom()')
            page.locator('#zoom-in').click()
            assert page.evaluate('window.newsmap.cy.zoom()') > old_zoom
            page.locator('#zoom-out').click()
            page.locator('#graph').scroll_into_view_if_needed()
            old_pan = page.evaluate('window.newsmap.cy.pan()')
            graph = page.locator('#graph').bounding_box()
            page.mouse.move(graph['x'] + 8, graph['y'] + 8)
            page.mouse.down()
            page.mouse.move(graph['x'] + 65, graph['y'] + 65, steps=12)
            page.mouse.up()
            assert page.evaluate('window.newsmap.cy.pan()') != old_pan
            checks.append('zoom and physical pointer pan')
            page.locator('#fit').click()
            page.locator('#topics button').filter(has_text='Модели и архитектуры').click()
            assert page.locator('#timeline .item-button').count() == 4
            story_value = page.locator('#story-filter option').filter(has_text='Aurora / от анонса к уточнению').get_attribute('value')
            page.locator('#story-filter').select_option(story_value)
            assert page.locator('#timeline .item-button').count() == 4
            checks.append('topic to story filtering')
            edge = page.evaluate('''() => {const e=window.newsmap.cy.edges().filter(e=>e.data('kind')==='REFERS_TO')[0];
              return {id:e.id(), point:e.renderedMidpoint()};}''')
            graph = page.locator('#graph').bounding_box()
            page.mouse.click(graph['x'] + edge['point']['x'], graph['y'] + edge['point']['y'])
            assert 'REFERS_TO' in page.locator('#detail').inner_text()
            assert page.locator('#detail .quote mark').count() == 1
            checks.append('physical edge click opens exact retained link evidence')
            page.locator('#mode-claims').click()
            edge = page.evaluate('''() => {const e=window.newsmap.cy.edges()[0]; return {point:e.renderedMidpoint()};}''')
            graph = page.locator('#graph').bounding_box()
            page.mouse.click(graph['x'] + edge['point']['x'], graph['y'] + edge['point']['y'])
            assert 'CORRECTS' in page.locator('#detail').inner_text()
            assert page.locator('#comparison .quote mark').count() == 4
            assert page.locator('#comparison .context-table').count() == 2
            assert 'fixture_operator' in page.locator('#comparison').inner_text()
            checks.append('reviewed claim edge links to side-by-side multi-span evidence and attributed context')
            page.screenshot(path=str(reports / 'comparison.png'), full_page=True)
            page.locator('#topics button').filter(has_text='Безопасность').click()
            assert page.locator('#comparison .quote mark').count() == 0
            assert 'Синтетическая интерпретация' not in page.locator('#detail').inner_text()
            checks.append('scope changes clear excluded evidence and comparison endpoints')
            page.locator('#reset').click()
            page.locator('#review-filter').select_option('active')
            assert page.evaluate('window.newsmap.cy.edges().length') == 1
            page.locator('#review-filter').select_option('proposal')
            assert page.evaluate('window.newsmap.cy.edges().length') == 0
            page.locator('#reset').click()
            page.locator('#mode-documents').click()
            page.locator('#quality-filter').select_option('pending')
            assert page.locator('#timeline .item-button').count() == 1
            page.locator('#timeline .item-button').first.click()
            assert 'pending' in page.locator('#detail').inner_text()
            checks.append('quality and review filters preserve pending inspection')
            page.locator('#reset').click()
            page.locator('#role-filter').select_option('forwarder_hint')
            assert page.locator('#timeline .item-button').count() == 2
            page.locator('#reset').click()
            page.locator('#timeline .item-button').first.click()
            page.locator('#ego').click()
            assert page.evaluate('window.newsmap.state.ego')
            page.locator('#clear-focus').click()
            checks.append('source role filter and bounded ego focus')
            page.set_viewport_size({'width': 390, 'height': 844})
            page.screenshot(path=str(reports / 'mobile.png'), full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
            checks.append('mobile layout has no horizontal overflow')
            assert not errors, errors
            assert not external, external
            assert not page.evaluate('Boolean(window.PWNED)')
            checks.append('zero JavaScript errors and zero external requests through interaction sequence')
            browser.close()
    finally:
        server.shutdown(); server.server_close()
        result = {'checks': checks, 'requests': requests, 'external_requests': external, 'page_errors': errors,
                  'human_usefulness_walkthrough': 'pending; automated functional checks only'}
        (reports / 'browser.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
