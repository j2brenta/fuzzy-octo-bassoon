"""Article parsing, safe downloads and Telegram integration without real network calls."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import Message

from channels.telegram import handle_message
from channels.web import fetch_linked_articles
from engine.entities import extract_entities, review_entities
from engine.extract import extract_capture, source_passages
from engine.judge import judge_capture
from engine.llm import ModelReply
from engine.package import build_extraction
from engine.parse import parse_capture
from engine.parse.article import parse_article
from engine.parse.web import HtmlPage, WebError, _public_address, fetch_html, normalize_url, _PublicHTTPSConnection
from engine.placement import apply_storage_stub
from store.raw import capture_text
from tests.test_entities import entity_review
from tests.test_judge import review_data
from tests.test_telegram import message
from tests.test_triage import FakeModel, LABELS
from engine.triage import triage_capture

URL = 'https://example.org/article'
HTML = b'''<!doctype html><html><head><meta charset="utf-8">
<title>Memory for agents</title><meta name="author" content="Maya Example">
<meta property="og:site_name" content="Example Journal">
<meta property="article:published_time" content="2026-10-21T09:30:00+02:00">
</head><body><nav>Menu Login Subscribe</nav><article><h1>Memory for agents</h1>
<p>An agent can retain useful knowledge between sessions by separating task state from long-term memory. Source references let readers inspect the original evidence behind each stored statement.</p>
<p>Corrections must preserve the original text and explicitly identify which earlier value has changed. Replaying saved records should not trigger another paid extraction call.</p>
</article><footer>Subscribe Cookie settings</footer></body></html>'''
DATE = '2026-10-21T10:00:00+00:00'


def page(content=HTML):
    return HtmlPage(URL, URL, content, DATE)


class ArticleParseTests(unittest.TestCase):
    def test_body_and_metadata_without_navigation_or_footer(self):
        article = parse_article(HTML, URL)
        self.assertEqual(article.title, 'Memory for agents')
        self.assertEqual(article.author, 'Maya Example')
        self.assertEqual(article.publication_date, '2026-10-21T09:30:00+02:00')
        self.assertIn('separating task state', article.text)
        self.assertNotIn('Menu Login Subscribe', article.text)
        self.assertNotIn('Cookie settings', article.text)

    def test_jsonld_publication_date_and_author(self):
        content = HTML.replace(b'<meta name="author" content="Maya Example">', b'')
        content = content.replace(b'<meta property="article:published_time" content="2026-10-21T09:30:00+02:00">', b'''<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"NewsArticle","headline":"Memory for agents","author":{"@type":"Person","name":"Nina Example"},"datePublished":"2026-10-20"}]}</script>''')
        article = parse_article(content, URL)
        self.assertEqual(article.publication_date, '2026-10-20')
        self.assertEqual(article.author, 'Nina Example')

    def test_unknown_date_is_not_guessed_from_url(self):
        content = HTML.replace(b'<meta property="article:published_time" content="2026-10-21T09:30:00+02:00">', b'')
        self.assertIsNone(parse_article(content, 'https://example.org/2026/10/20/article').publication_date)

    def test_login_only_page_does_not_become_an_article(self):
        with self.assertRaises(WebError):
            parse_article(b'<html><body><form>Please log in</form></body></html>', URL)


class FetchTests(unittest.TestCase):
    def connection(self, status=200, content=HTML, headers=None):
        response = MagicMock()
        response.status = status
        values = {'Content-Type': 'text/html; charset=utf-8', **(headers or {})}
        response.getheader.side_effect = lambda key, default=None: values.get(key, default)
        response.read.return_value = content
        connection = MagicMock()
        connection.getresponse.return_value = response
        return connection

    def test_public_html_and_relative_redirect_preserve_requested_and_final_urls(self):
        first = self.connection(302, headers={'Location': '/final'})
        second = self.connection()
        with patch('engine.parse.web._connection', side_effect=[first, second]) as factory:
            result = fetch_html(URL + '#heading')
        self.assertEqual(result.requested_url, URL)
        self.assertEqual(result.final_url, 'https://example.org/final')
        self.assertEqual(result.content, HTML)
        self.assertEqual(factory.call_args_list[1].args[0], result.final_url)
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_url_validation_rejects_credentials_non_http_and_unexpected_ports(self):
        for url in ['file:///etc/passwd', 'https://user:pass@example.org/', 'https://example.org:9000/', 'https://example.org/\r\nsecret', 'https://example.org:bad/']:
            with self.subTest(url=url), self.assertRaises(WebError):
                normalize_url(url)

    def test_international_paths_are_encoded_without_double_encoding(self):
        url = normalize_url('https://example.org/статья?q=текст#heading')
        self.assertEqual(url, 'https://example.org/%D1%81%D1%82%D0%B0%D1%82%D1%8C%D1%8F?q=%D1%82%D0%B5%D0%BA%D1%81%D1%82')
        self.assertEqual(normalize_url(url), url)

    def test_private_or_mixed_dns_addresses_are_rejected(self):
        for addresses in [['127.0.0.1'], ['169.254.169.254'], ['::1'], ['93.184.216.34', '10.0.0.1']]:
            records = [(2, 1, 6, '', (address, 443)) for address in addresses]
            with self.subTest(addresses=addresses), patch('engine.parse.web.socket.getaddrinfo', return_value=records), self.assertRaises(WebError):
                _public_address('example.org', 443)

    def test_redirect_to_private_host_is_blocked_before_second_request(self):
        records = [[(2, 1, 6, '', ('93.184.216.34', 443))], [(2, 1, 6, '', ('127.0.0.1', 443))]]
        first = self.connection(302, headers={'Location': 'https://internal.test/private'})
        with patch('engine.parse.web.socket.getaddrinfo', side_effect=records), patch('engine.parse.web._PublicHTTPSConnection', return_value=first) as factory, self.assertRaises(WebError):
            fetch_html(URL)
        self.assertEqual(factory.call_count, 1)
        first.close.assert_called_once()

    def test_tls_uses_validated_address_and_original_hostname(self):
        connection = _PublicHTTPSConnection('example.org', 443)
        connection.public_address = '93.184.216.34'
        connection._context = MagicMock()
        with patch('engine.parse.web.socket.create_connection') as connect:
            connection.connect()
        self.assertEqual(connect.call_args.args[0], ('93.184.216.34', 443))
        self.assertEqual(connection._context.wrap_socket.call_args.kwargs['server_hostname'], 'example.org')

    def test_non_html_http_errors_compression_and_empty_bodies_are_explicit(self):
        cases = [self.connection(403), self.connection(headers={'Content-Type': 'application/pdf'}),
                 self.connection(headers={'Content-Encoding': 'gzip'}), self.connection(content=b'')]
        for connection in cases:
            with self.subTest(connection=connection), patch('engine.parse.web._connection', return_value=connection), self.assertRaises(WebError):
                fetch_html(URL)
            connection.close.assert_called_once()

    def test_blocked_download_explains_browser_fallback_without_parsing_the_challenge(self):
        connection = self.connection(403, content=b'private diagnostic challenge')
        with patch('engine.parse.web._connection', return_value=connection), self.assertRaises(WebError) as failure:
            fetch_html(URL)
        self.assertIn('blocked the automated download', str(failure.exception))
        self.assertIn('HTTP 403', str(failure.exception))
        self.assertIn('paste the article text', str(failure.exception))
        self.assertNotIn('private diagnostic', str(failure.exception))
        connection.getresponse.return_value.read.assert_not_called()

    def test_oversized_response_and_endless_redirects_stop(self):
        with patch('engine.parse.web._connection', return_value=self.connection(content=b'x' * 21)), patch('engine.parse.web.MAX_HTML_BYTES', 20), self.assertRaisesRegex(WebError, 'limit'):
            fetch_html(URL)
        connection = self.connection(302, headers={'Location': '/again'})
        with patch('engine.parse.web._connection', return_value=connection) as factory, self.assertRaisesRegex(WebError, 'redirect'):
            fetch_html(URL)
        self.assertEqual(factory.call_count, 4)


class ArticleCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.vault = Path(temporary.name)
        self.parent = capture_text('My comment. ' + URL, self.vault, channel='telegram', source_id='fixture:article', source_name='Olga', author_hint={'name': 'Olga', 'id': '100'})

    def fetch(self, content=HTML, **kwargs):
        return fetch_linked_articles(self.parent.path, fetcher=lambda url: page(content), **kwargs)

    def test_child_keeps_original_message_author_and_html_separate(self):
        before = self.parent.path.read_bytes()
        row = self.fetch()[0]
        self.assertEqual(row['status'], 'captured')
        child_path = self.vault / row['raw_path']
        child = json.loads(child_path.read_text())
        self.assertEqual(child['author_hint'], {'name': 'Maya Example'})
        self.assertEqual(child['forwarded_by'], {'name': 'Olga'})
        self.assertEqual(child['url'], URL)
        self.assertEqual(child['published_at'], '2026-10-21T07:30:00+00:00')
        self.assertEqual(child['channel_metadata']['parent']['item_id'], self.parent.item_id)
        self.assertNotIn('text', child['channel_metadata']['web_article'])
        self.assertEqual((self.vault / row['snapshot']['path']).read_bytes(), HTML)
        document = json.loads(parse_capture(child_path).path.read_text())
        self.assertEqual(document['parent']['item_id'], self.parent.item_id)
        self.assertEqual(document['sections'][0]['provenance']['derived_from'], row['snapshot'])
        self.assertEqual(self.parent.path.read_bytes(), before)

    def test_retry_reuses_child_and_snapshot_without_fetching(self):
        first = self.fetch()[0]
        with patch('channels.web.fetch_html', side_effect=AssertionError('No network on retry')):
            second = fetch_linked_articles(self.parent.path)[0]
        self.assertTrue(second['reused'])
        self.assertEqual(first['item_id'], second['item_id'])
        self.assertEqual(len(list((self.vault / 'raw').glob('*.json'))), 2)

    def test_fetch_failure_is_recorded_and_retry_can_succeed(self):
        before = self.parent.path.read_bytes()
        rows = fetch_linked_articles(self.parent.path, fetcher=MagicMock(side_effect=WebError('HTTP 403')))
        self.assertEqual(rows[0]['status'], 'failed')
        self.assertEqual(self.fetch()[0]['status'], 'captured')
        self.assertEqual(self.parent.path.read_bytes(), before)

    def test_failed_parse_keeps_html_snapshot_and_no_false_child(self):
        row = self.fetch(b'<html><body><form>Log in</form></body></html>')[0]
        self.assertEqual(row['status'], 'failed')
        self.assertTrue((self.vault / row['snapshot']['path']).exists())
        self.assertEqual(len(list((self.vault / 'raw').glob('*.json'))), 1)

    def test_refresh_versions_changed_html_without_overwriting_previous_source(self):
        first = self.fetch()[0]
        old = (self.vault / first['raw_path']).read_bytes()
        second = self.fetch(HTML.replace(b'Memory for agents', b'Better memory for agents'), force=True)[0]
        self.assertNotEqual(first['item_id'], second['item_id'])
        self.assertEqual((self.vault / first['raw_path']).read_bytes(), old)
        self.assertEqual(len(list((self.vault / 'web-snapshots').glob('*.html'))), 2)

    def test_date_only_does_not_invent_a_timezone_or_midnight(self):
        row = self.fetch(HTML.replace(b'2026-10-21T09:30:00+02:00', b'2026-10-21'))[0]
        raw = json.loads((self.vault / row['raw_path']).read_text())
        self.assertIsNone(raw['published_at'])
        self.assertEqual(raw['channel_metadata']['web_article']['publication_date'], '2026-10-21')

    def test_unknown_article_author_is_not_inherited_from_sharer(self):
        row = self.fetch(HTML.replace(b'<meta name="author" content="Maya Example">', b''))[0]
        child = json.loads((self.vault / row['raw_path']).read_text())
        self.assertIsNone(child['author_hint'])
        self.assertEqual(child['forwarded_by'], {'name': 'Olga'})

    def test_legacy_string_forwarder_is_accepted(self):
        parent = json.loads(self.parent.path.read_text())
        parent['forwarded_by'] = 'Kirill R'
        self.parent.path.write_text(json.dumps(parent))
        row = self.fetch()[0]
        child = json.loads((self.vault / row['raw_path']).read_text())
        self.assertEqual(child['forwarded_by'], {'name': 'Kirill R'})

    def test_snapshot_corruption_is_not_treated_as_valid_source(self):
        row = self.fetch()[0]
        (self.vault / row['snapshot']['path']).write_bytes(b'corrupted')
        with self.assertRaisesRegex(ValueError, 'snapshot'):
            parse_capture(self.vault / row['raw_path'])

    def test_duplicate_links_have_one_child_and_children_do_not_crawl(self):
        raw = capture_text(URL + '\n' + URL, self.vault, channel='file', source_id='duplicate-links', source_name='Fixture')
        fetcher = MagicMock(return_value=page())
        rows = fetch_linked_articles(raw.path, fetcher=fetcher)
        self.assertEqual(len(rows), 1)
        self.assertEqual(fetcher.call_count, 1)
        self.assertEqual(fetch_linked_articles(self.vault / rows[0]['raw_path'], fetcher=fetcher), [])
        self.assertEqual(fetcher.call_count, 1)

    def test_url_fragments_do_not_cause_duplicate_downloads(self):
        raw = capture_text(URL + '#one\n' + URL + '#two', self.vault, channel='file', source_id='fragment-links', source_name='Fixture')
        fetcher = MagicMock(return_value=page())
        rows = fetch_linked_articles(raw.path, fetcher=fetcher)
        self.assertEqual(len(rows), 1)
        self.assertEqual(fetcher.call_count, 1)

    def test_distinct_links_to_one_final_page_keep_parent_provenance_and_cache(self):
        raw = capture_text(URL + '\n' + URL + '/alias', self.vault, channel='file', source_id='redirect-aliases', source_name='Fixture')
        rows = fetch_linked_articles(raw.path, fetcher=MagicMock(return_value=page()))
        self.assertNotEqual(rows[0]['item_id'], rows[1]['item_id'])
        for row in rows:
            child = json.loads((self.vault / row['raw_path']).read_text())
            self.assertEqual(child['channel_metadata']['parent']['url'], row['url'])
        with patch('channels.web.fetch_html', side_effect=AssertionError('No new requests')):
            cached = fetch_linked_articles(raw.path)
        self.assertTrue(all(row['reused'] for row in cached))

    def test_non_article_links_and_excess_links_are_explicit(self):
        text = '\n'.join([URL, URL + '/two', URL + '/three', URL + '/four', URL + '.pdf', 'https://youtu.be/demo'])
        raw = capture_text(text, self.vault, channel='file', source_id='many-links', source_name='Fixture')
        fetcher = MagicMock(return_value=page())
        rows = fetch_linked_articles(raw.path, fetcher=fetcher)
        self.assertEqual([r['status'] for r in rows], ['captured'] * 3 + ['deferred', 'unsupported', 'unsupported'])
        self.assertEqual(fetcher.call_count, 3)

    def test_child_uses_existing_review_and_storage_pipeline_with_article_evidence(self):
        row = self.fetch()[0]
        path = self.vault / row['raw_path']
        raw = json.loads(path.read_text())
        passage = source_passages(raw['text'])[1]
        candidate = {'claims': [{'statement': 'An agent can retain useful knowledge between sessions by separating task state from long-term memory.', 'evidence_passage_ids': [passage['id']], 'kind': 'how-to', 'durability_class': 'reference', 'confidence': .9}]}
        triage_capture(path, FakeModel([ModelReply(dict(LABELS))]))
        extracted = extract_capture(path, FakeModel([ModelReply(candidate)]))
        judge_capture(path, FakeModel([ModelReply(review_data(extracted.claims))]))
        entities = extract_entities(path, FakeModel([ModelReply({'entities': []})]))
        review_entities(path, FakeModel([ModelReply(entity_review([]))]), entities)
        record = build_extraction(path)
        self.assertTrue(record.record['quality_gate_passed'])
        self.assertEqual(record.record['source']['url'], URL)
        self.assertEqual(record.record['parent']['item_id'], self.parent.item_id)
        evidence = record.record['claims'][0]['evidence']
        self.assertEqual(evidence['item_id'], row['item_id'])
        self.assertIn('agent can retain', raw['text'][evidence['start_char']:evidence['end_char']])
        self.assertEqual(apply_storage_stub(record.path)['status'], 'stored_only')


class TelegramArticleTests(unittest.IsolatedAsyncioTestCase):
    async def run_message(self, text, fetcher=None, setting='on'):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        vault = Path(temporary.name)
        with patch.dict(os.environ, {'WEB_FETCH': setting}), patch('channels.web.fetch_html', fetcher or MagicMock(return_value=page())), patch('channels.telegram.process_capture', new_callable=AsyncMock) as process, patch.object(Message, 'answer', new_callable=AsyncMock) as answer:
            await handle_message(message(text=text), vault, 100)
        return vault, process, answer

    async def test_non_url_notes_keep_the_existing_processing_behavior(self):
        for text in ['An idea to keep.', '💡']:
            _, process, _ = await self.run_message(text)
            self.assertEqual(process.await_count, 1)

    async def test_bare_url_processes_article_not_pointer_text(self):
        vault, process, answer = await self.run_message(URL)
        self.assertEqual(process.await_count, 1)
        child = process.await_args.args[1]
        record = json.loads(child.path.read_text())
        self.assertIn('retain useful knowledge', record['text'])
        self.assertEqual(record['url'], URL)
        self.assertTrue(any('Article saved' in call.args[0] for call in answer.await_args_list))

    async def test_comment_and_link_preserve_two_separate_processing_inputs(self):
        _, process, _ = await self.run_message('My own idea. ' + URL)
        self.assertEqual(process.await_count, 2)
        self.assertNotEqual(process.await_args_list[0].args[1].item_id, process.await_args_list[1].args[1].item_id)

    async def test_failed_url_never_generates_article_claims(self):
        vault, process, answer = await self.run_message(URL, MagicMock(side_effect=WebError('Article requires login.')))
        process.assert_not_awaited()
        self.assertEqual(len(list((vault / 'raw').glob('*.json'))), 1)
        self.assertTrue(any('requires login' in call.args[0] for call in answer.await_args_list))

    async def test_fetch_can_be_disabled_without_modeling_the_bare_url(self):
        fetcher = MagicMock(side_effect=AssertionError('Disabled'))
        _, process, _ = await self.run_message(URL, fetcher, setting='off')
        fetcher.assert_not_called()
        process.assert_not_awaited()
