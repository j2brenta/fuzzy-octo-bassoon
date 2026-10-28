"""refresher serve: the one local app on one vault: add a source, see the Atlas, decide the review's proposals.

    /capture/   add a source and follow it through ingestion (Olga's workspace, refresher/web.py)
    /atlas/     the map and the proposals waiting for you (Victor's viewer, refresher/assets/map/)
    /api/...    submissions and uploads, the map's data, proposals and decisions

A web capture is placed in the graph like a Telegram one (refresher/place.py), so it shows on the map. Local only:
it binds to 127.0.0.1 unless --host says otherwise, answers only to a Host it knows (localhost, 127.0.0.1, any
--allow-host), and takes a POST only with the X-Refresher header and, when the browser sends one, a same-origin
Origin, so another site open in the browser cannot post. A decision goes through vault.decide(): the token the page
was given must still be current, or the answer is 409 and the page shows the current version. The replayed vault is
kept until a file it reads changes, so a page load does not replay the whole vault every time.
"""
import json
import logging
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

from graph.memory import MemoryStore
from refresher import graphview, vault
from refresher.web import STATIC, Workspace, read_json

PORT = 8080
MAX_DECISION = 16 * 1024
MAX_REQUEST = 29 * 1024 * 1024
MAP_TTL = timedelta(minutes=5)  # freshness moves slowly; the map is recomputed at least this often
DECIDE = re.compile(r"^/api/proposals/(p-[0-9a-f]{16})$")
SUBMISSION = re.compile(r"^/api/submissions/([a-f0-9]{32})$")
RETRY = re.compile(r"^/api/submissions/([a-f0-9]{32})/retry$")
UPLOAD = re.compile(r"^/api/uploads/([a-f0-9]{64})$")
HTML, CSS, JS = "text/html; charset=utf-8", "text/css; charset=utf-8", "text/javascript; charset=utf-8"
FILES = {
    "/capture/": (STATIC / "index.html", HTML),
    "/capture/app.css": (STATIC / "app.css", CSS),
    "/capture/app.js": (STATIC / "app.js", JS),
    "/capture/brain.png": (STATIC / "brain.png", "image/png"),
    "/atlas/app.js": (graphview.ASSETS / "map" / "app.js", JS),
    "/atlas/style.css": (graphview.ASSETS / "map" / "style.css", CSS),
    "/atlas/cytoscape.min.js": (graphview.ASSETS / "cytoscape.min.js", JS),
}
for _page in ("/capture/", "/atlas/"):  # the shared shell sits beside every page, as it does in wiki/graph/
    FILES[_page + "shell.css"] = (STATIC / "shell.css", CSS)
    FILES[_page + "nav.js"] = (STATIC / "nav.js", JS)
BASE_CSP = "default-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; " \
           "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
CSP = {"capture": BASE_CSP + "; style-src 'self'",
       "atlas": BASE_CSP + "; style-src 'self' 'unsafe-inline'"}  # Cytoscape and the map set inline styles
# What vault.replay reads; a change to any of these files replays the vault again.
WATCHED = ("processed", "relations", "graph-reviews", "people", "translations", "overrides.yaml",
           ".cache/cluster-names.json", ".cache/cluster-themes.json", "wiki/items", "wiki/people")
logger = logging.getLogger(__name__)


def fingerprint(root: Path) -> int:
    rows = []
    for name in WATCHED:
        path = root / name
        for f in sorted(path.rglob("*")) if path.is_dir() else [path]:
            try:
                s = f.stat()
            except OSError:
                continue
            if stat.S_ISREG(s.st_mode):
                rows.append((str(f.relative_to(root)), s.st_mtime_ns, s.st_size))
    return hash(tuple(rows))


class State:
    """The replayed vault and the map built from it, kept until a file replay reads changes."""

    def __init__(self, vault_path: Path, include_unreviewed: bool = True):
        self.vault, self.include_unreviewed = vault_path, include_unreviewed
        self._lock = Lock()
        self._key = self._store = self._map = self._map_at = None

    def store(self) -> MemoryStore:
        key = fingerprint(self.vault)
        with self._lock:
            if key != self._key:
                self._store = vault.replay(self.vault, include_unreviewed=self.include_unreviewed)
                self._key, self._map = key, None
            return self._store

    def map(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        store = self.store()
        with self._lock:
            if self._map is None or now - self._map_at > MAP_TTL:
                self._map = {**graphview.map_data(store, now), "served": True}
                self._map_at = now
            return self._map


def rebuild(vault_path: Path, include_unreviewed: bool) -> None:
    """Refresh wiki/ after a decision, as `refresher rebuild` would."""
    at = datetime.now(timezone.utc)
    with vault.lock(vault_path):
        store = vault.replay(vault_path, include_unreviewed=include_unreviewed)
        vault.export(store, vault_path, at)
        graphview.write(store, vault_path, at)


def placer(vault_path: Path):
    """Place a web capture in the graph as the bot does: the model judges relations unless GRAPH_RELATOR=lexical,
    and place() falls back to wording when it fails. What comes back is shown on the capture's detail."""
    from graph.relate import ModelRelator
    from refresher.place import place

    def place_item(item_id: str, client) -> dict:
        lexical = os.environ.get("GRAPH_RELATOR", "model").strip().lower() == "lexical"
        try:
            placed, store, note = place(vault_path, item_id, None if lexical or client is None else ModelRelator(client))
        except (OSError, ValueError) as error:
            logger.error("item %s · graph placement failed (%s)", item_id[:10], type(error).__name__)
            return {"item": item_id, "status": "failed", "note": "Graph placement failed; the next rebuild places it."}
        ideas = set(placed.ideas)
        topics = [{"id": t.topic_id, "title": t.title} for t in store.topics() if ideas & (t.ideas | t.attached)]
        return {"item": item_id, "status": placed.status, "claims": placed.claims, "ideas": len(ideas),
                "relations": len(placed.relations), "duplicate_of": placed.duplicate_of, "topics": topics,
                "note": note}

    return place_item


def handler(vault_path: Path, workspace: Workspace, state: State, allow_hosts=(), log=print):
    class Handler(BaseHTTPRequestHandler):
        server_version = "refresher"

        def log_message(self, fmt, *args):  # one line per request, no headers or bodies
            log(f"serve: {self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}".rstrip())

        def _hosts(self) -> set[str]:
            port = self.server.server_address[1]
            return {f"{h}:{port}" for h in ("127.0.0.1", "localhost")} | \
                {h if ":" in h else f"{h}:{port}" for h in allow_hosts}

        def _local(self) -> bool:
            return self.headers.get("Host", "") in self._hosts()

        def _send(self, status: int, body: bytes, kind: str, csp: str = CSP["capture"], headers=()):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", csp)
            for name, value in headers:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, value):
            self._send(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _redirect(self, to: str):
            self._send(HTTPStatus.FOUND, b"", "text/plain", headers=[("Location", to)])

        def do_GET(self):
            if not self._local():
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Open the app through 127.0.0.1 or localhost."})
            path = urlsplit(self.path).path
            if path == "/":
                return self._redirect("/atlas/")
            if path in ("/atlas", "/capture"):
                return self._redirect(path + "/")
            if path == "/atlas/":
                html = graphview.page(state.map())
                html = html.replace("connect-src 'none'", "connect-src 'self'")  # the page may post decisions back
                return self._send(HTTPStatus.OK, html.encode(), HTML, CSP["atlas"])
            if path == "/api/data":
                return self._json(HTTPStatus.OK, state.map())
            if path == "/api/proposals":
                return self._json(HTTPStatus.OK, state.map()["proposals"])
            if path == "/api/submissions":
                return self._json(HTTPStatus.OK, workspace.list_jobs())
            if match := SUBMISSION.match(path):
                result = workspace.detail(match[1])
                return self._json(HTTPStatus.OK if result else HTTPStatus.NOT_FOUND,
                                  result or {"error": "Submission not found."})
            if (match := UPLOAD.match(path)) and (upload := workspace.vault / "uploads" / match[1]).is_file():
                return self._send(HTTPStatus.OK, upload.read_bytes(), "application/octet-stream",
                                  headers=[("Content-Disposition", 'attachment; filename="original-upload"')])
            if path in FILES:
                file, kind = FILES[path]
                return self._send(HTTPStatus.OK, file.read_bytes(), kind, CSP[path.split("/")[1]])
            return self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self._local() or self.headers.get("X-Refresher") != "1" or \
                    (origin is not None and origin not in {f"http://{h}" for h in self._hosts()}):
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Changes are taken only from the app itself."})
            path = urlsplit(self.path).path
            decide, retry = DECIDE.match(path), RETRY.match(path)
            if not (decide or retry or path == "/api/submissions"):
                return self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Use JSON."})
            if retry:
                try:
                    return self._json(HTTPStatus.ACCEPTED, workspace.retry(retry[1]))
                except ValueError as error:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= (MAX_DECISION if decide else MAX_REQUEST):
                return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE if length else HTTPStatus.BAD_REQUEST,
                                  {"error": "Send a JSON body; uploads go up to 20 MB."})
            if decide:
                return self._decide(decide[1], length)
            try:
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError("Submission must be an object.")
                return self._json(HTTPStatus.ACCEPTED, workspace.submit(data))
            except (ValueError, UnicodeError) as error:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except OSError:
                return self._json(HTTPStatus.INTERNAL_SERVER_ERROR,
                                  {"error": "Could not save this submission. Check server storage and try again."})

        def _decide(self, proposal_id: str, length: int):
            try:
                body = json.loads(self.rfile.read(length))
                action, token = body["action"], body["token"]
                rationale = str(body.get("rationale") or "")[:2000]
                if not isinstance(action, str) or not isinstance(token, str):
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                return self._json(HTTPStatus.BAD_REQUEST, {"error": "The body needs action and token."})
            try:
                decision = vault.decide(vault_path, proposal_id, action, token, rationale)
            except KeyError:
                return self._json(HTTPStatus.NOT_FOUND, {"error": "No such proposal; the review may have replaced it."})
            except vault.StaleToken as error:
                return self._json(HTTPStatus.CONFLICT, {"error": str(error)})
            except ValueError as error:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            rebuild(vault_path, state.include_unreviewed)
            vault.log(vault_path, decision.at, f"decide: {decision.action} {decision.proposal_id}")
            return self._json(HTTPStatus.OK, {"ok": True, "action": decision.action})

    return Handler


def serve(vault_path: Path, port: int = PORT, host: str = "127.0.0.1", allow_hosts=(),
          include_unreviewed: bool = True) -> None:
    from dotenv import load_dotenv
    from engine import logs
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")  # the capture pipeline reads its providers from it
    logs.configure(vault=vault_path, process="app")
    workspace = Workspace(vault_path, place=placer(vault_path))
    for path in (vault_path / "submissions").glob("*.json"):
        job = read_json(path)
        if job.get("status") in {"queued", "processing"}:
            job.update(status="pending", stage="Interrupted",
                       message="Server restarted. Original retained; retry to process it.")
            workspace.save(job)
    state = State(vault_path, include_unreviewed)
    server = ThreadingHTTPServer((host, port), handler(vault_path, workspace, state, allow_hosts))
    print(f"serve: Refresher on http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{server.server_address[1]}/ "
          f"over {vault_path} (Ctrl+C stops it)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("serve: stopped")
    finally:
        server.server_close()
        workspace.worker.shutdown(wait=True)
