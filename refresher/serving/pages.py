"""GET: the pages, their redirects and the map behind them; dispatching every GET to its group of routes."""
import logging
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

from engine.llm import ModelError
from refresher import graphview, people_backfill, people_review
from refresher.serving.routes import CSP, HTML

logger = logging.getLogger("refresher.app")


class Pages:
    """The Atlas and Review pages, short paths redirected, the map data, people review and the consolidate status."""

    def do_GET(self):
        try:
            return self._get()
        except ModelError as error:  # a model or the embeddings service did not answer: say so, never hang up
            logger.warning("GET %s: %s", urlsplit(self.path).path, error)
            return self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(error)})

    def _get(self):
        if not self._local():
            return self._json(HTTPStatus.FORBIDDEN, {"error": "Open the app through 127.0.0.1 or localhost."})
        url = urlsplit(self.path)
        path, args = url.path, {k: v[-1] for k, v in parse_qs(url.query).items()}
        # Each group of routes answers (True) or passes (None) to the next.
        return self._get_pages(path, url, args) or self._get_subjects(path, args) or self._get_files(path, args) \
            or self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def _get_pages(self, path: str, url, args: dict):
        if path in ("/read", "/read/"):  # the reading layout of the Atlas: evidence, text, map
            return self._redirect("/atlas/?layout=read" + (f"&{url.query}" if url.query else ""))
        if path == "/graph" or path.startswith("/graph/"):
            return self._redirect("/atlas/" + path.removeprefix("/graph").lstrip("/") + (f"?{url.query}" if url.query else ""))
        if path in ("/atlas", "/review", "/capture", "/topic", "/brief", "/library", "/example", "/timeline"):
            return self._redirect(path + "/" + (f"?{url.query}" if url.query else ""))
        if path in ("/atlas/", "/review/"):  # app.js lays the page out by its path
            html = graphview.page(self.state.map())
            html = html.replace("connect-src 'none'", "connect-src 'self'")  # the page may post decisions back
            return self._send(HTTPStatus.OK, html.encode(), HTML, CSP["atlas"])
        if path == "/api/people/attribution":
            try:
                return self._json(HTTPStatus.OK, people_review.detail(self.vault_path, args.get('claim','')))
            except KeyError:
                return self._json(HTTPStatus.NOT_FOUND, {'error':'Claim not found.'})
        if path == "/api/people/backfill":
            return self._json(HTTPStatus.OK, people_backfill.preview(self.vault_path))
        if path.startswith('/api/people/backfill/'):
            try:
                return self._json(HTTPStatus.OK, people_backfill.job(self.vault_path,path.rsplit('/',1)[-1]))
            except KeyError:
                return self._json(HTTPStatus.NOT_FOUND, {'error':'Update not found.'})
        if path == "/api/data":
            return self._json(HTTPStatus.OK, self.state.map())
        if path == "/api/proposals":
            return self._json(HTTPStatus.OK, self.state.map()["proposals"])
        if path == "/api/topics":
            return self._json(HTTPStatus.OK, self.state.catalog())
        if path == "/api/consolidate":
            return self._json(HTTPStatus.OK, self.review.status())
