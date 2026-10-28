"""refresher serve: the one local app on one vault: add a source, see the Atlas, decide the review's proposals.

    /           the topics: pick a subject, or type one
    /topic/     a subject read cluster by cluster (refresher/subject.py): a plan, the clusters, a reader beside it
    /capture/   add a source and follow it through ingestion (Olga's workspace, refresher/web.py)
    /atlas/     the map and the proposals waiting for you (Victor's viewer, refresher/assets/map/)
    /api/...    submissions and uploads, the map's data, topics and dossiers, proposals and decisions

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
from threading import Lock, RLock, Thread
from urllib.parse import parse_qs, urlsplit

from graph.memory import MemoryStore
from refresher import consolidate, dossier, graphview, people_backfill, people_review, reading, subject, vault
from refresher.web import STATIC, Workspace, read_json

PORT = 8080
MAX_DECISION = 16 * 1024
MAX_REQUEST = 29 * 1024 * 1024
MAP_TTL = timedelta(minutes=5)  # freshness moves slowly; the map is recomputed at least this often
DECIDE = re.compile(r"^/api/proposals/(p-[0-9a-f]{16})$")
SUBMISSION = re.compile(r"^/api/submissions/([a-f0-9]{32})$")
RETRY = re.compile(r"^/api/submissions/([a-f0-9]{32})/retry$")
UPLOAD = re.compile(r"^/api/uploads/([a-f0-9]{64})$")
SOURCE = re.compile(r"^/api/source/([\w.:-]{1,200})/(context|state)$")
HTML, CSS, JS = "text/html; charset=utf-8", "text/css; charset=utf-8", "text/javascript; charset=utf-8"
FILES = {
    "/": (STATIC / "topics.html", HTML),
    "/topic/": (STATIC / "dossier.html", HTML),
    "/static/brief.css": (STATIC / "brief.css", CSS),
    "/static/reading.css": (STATIC / "reading.css", CSS),
    "/static/topics.js": (STATIC / "topics.js", JS),
    "/static/dossier.js": (STATIC / "dossier.js", JS),
    "/static/cytoscape.min.js": (graphview.ASSETS / "cytoscape.min.js", JS),
    "/capture/": (STATIC / "index.html", HTML),
    "/capture/app.css": (STATIC / "app.css", CSS),
    "/capture/app.js": (STATIC / "app.js", JS),
    "/capture/brain.png": (STATIC / "brain.png", "image/png"),
    "/atlas/people.js": (graphview.ASSETS / "map" / "people.js", JS),
    "/atlas/app.js": (graphview.ASSETS / "map" / "app.js", JS),
    "/atlas/style.css": (graphview.ASSETS / "map" / "style.css", CSS),
    "/atlas/cytoscape.min.js": (graphview.ASSETS / "cytoscape.min.js", JS),
}
for _page in ("/static/", "/capture/", "/atlas/"):  # the shared shell sits beside every page, as in wiki/graph/
    FILES[_page + "shell.css"] = (STATIC / "shell.css", CSS)
    FILES[_page + "nav.js"] = (STATIC / "nav.js", JS)
BASE_CSP = "default-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; " \
           "base-uri 'none'; form-action 'self'"
CSP = {"page": BASE_CSP + "; style-src 'self'; frame-ancestors 'none'",
       # Cytoscape and the map set inline styles; a dossier embeds the map from the same origin
       "atlas": BASE_CSP + "; style-src 'self' 'unsafe-inline'; frame-ancestors 'self'",
       "topic": BASE_CSP + "; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"}  # its graphs are Cytoscape
# What vault.replay reads; a change to any of these files replays the vault again.
WATCHED = ("raw", "processed", "relations", "graph-reviews", "people-reviews", "people", "translations", "summaries",
           "overrides.yaml",
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
    """The replayed vault and the map built from it, kept until a file replay reads changes. MemoryStore computes its
    clusters and topics lazily and is not safe to read from two threads at once, so every read holds the lock."""

    def __init__(self, vault_path: Path, include_unreviewed: bool = True):
        self.vault, self.include_unreviewed = vault_path, include_unreviewed
        self._lock = RLock()
        self._key = self._store = self._map = self._map_at = None
        self._subjects: dict[tuple, dict] = {}  # scored subjects, kept until the vault changes

    def store(self) -> MemoryStore:
        key = fingerprint(self.vault)
        with self._lock:
            if key != self._key:
                self._store = vault.replay(self.vault, include_unreviewed=self.include_unreviewed)
                self._store.topics()  # build the groups now, while no one else reads
                self._key, self._map, self._subjects = key, None, {}
            return self._store

    def catalog(self, now: datetime | None = None) -> list[dict]:
        with self._lock:
            return dossier.catalog(self.store(), now or datetime.now(timezone.utc))

    def dossier(self, key: str | None, query: str | None, at: datetime | None = None) -> dict | None:
        """The dossier with what you have read of it and when you last came (reading/log.jsonl)."""
        now = datetime.now(timezone.utc)
        with self._lock:
            store = self.store()
            scope = dossier.resolve(store, key, query)
            d = dossier.build(store, scope, at or now) if scope else None
        if d is not None:
            d["reading"] = reading.for_scope(reading.load(self.vault), d["scope"]["id"],
                                             [x["id"] for x in d["documents"]], now)
        return d

    def subject(self, key: str | None, query: str | None) -> dict | None:
        """The scored subject (refresher/subject.py), computed once per vault state."""
        with self._lock:
            store = self.store()
            if (key, query) not in self._subjects:
                scope = dossier.resolve(store, key, query)
                self._subjects[(key, query)] = subject.subject(store, scope, datetime.now(timezone.utc)) if scope else None
            return self._subjects[(key, query)]

    def marks(self) -> dict[str, str]:
        return {i: v["state"] for i, v in reading.load(self.vault)["items"].items()}

    def context(self, key: str | None, query: str | None, item_id: str) -> dict | None:
        s = self.subject(key, query)
        if s is None:
            return None
        with self._lock:
            return subject.context(self.store(), s, item_id, self.marks())

    def document(self, item_id: str) -> dict | None:
        with self._lock:
            return dossier.document(self.store(), item_id)

    def map(self, now: datetime | None = None) -> dict:
        now = now or datetime.now(timezone.utc)
        with self._lock:
            store = self.store()
            if self._map is None or now - self._map_at > MAP_TTL:
                self._map = {**graphview.map_data(store, now), "served": True}
                self._map_at = now
            return self._map


class Review:
    """The hourly review started from the page's Consolidate button: one run at a time, in the background, so the
    request returns at once and the page polls status(). A run started while the review container or the bot is
    reviewing waits for the vault lock, then reviews again."""

    def __init__(self, vault_path: Path, run=None):
        self.vault = vault_path
        self._run = run or self._consolidate
        self._lock = Lock()
        self._thread: Thread | None = None
        self._started: datetime | None = None
        self._error: str | None = None

    def _consolidate(self) -> None:
        model, search = consolidate.models_from_environment()
        consolidate.consolidate(self.vault, model, search, log=logger.info)

    def _work(self) -> None:
        try:
            self._run()
        except Exception as error:  # a background thread: whatever stopped the run is shown in the panel
            logger.error("consolidate from the app failed (%s)", type(error).__name__)
            self._error = f"{type(error).__name__}: {str(error)[:300]}"

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Start a run unless one is going; False when one is."""
        with self._lock:
            if self.running():
                return False
            self._started, self._error = datetime.now(timezone.utc), None
            self._thread = Thread(target=self._work, name="consolidate", daemon=True)
            self._thread.start()
            return True

    def status(self) -> dict:
        last = consolidate.last_run(self.vault)
        next_at = (datetime.fromisoformat(last["at"]) + consolidate.EVERY).isoformat() if last else None
        return {"running": self.running(), "started_at": self._started.isoformat() if self._started else None,
                "error": self._error, "last": last, "next_at": next_at, "pending": consolidate.pending(self.vault)}


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


def handler(vault_path: Path, workspace: Workspace, state: State, allow_hosts=(), log=print,
            review: Review | None = None):
    review = review or Review(vault_path)

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

        def _send(self, status: int, body: bytes, kind: str, csp: str = CSP["page"], headers=()):
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
            url = urlsplit(self.path)
            path, args = url.path, {k: v[-1] for k, v in parse_qs(url.query).items()}
            if path == "/graph" or path.startswith("/graph/"):
                return self._redirect("/atlas/" + path.removeprefix("/graph").lstrip("/") + (f"?{url.query}" if url.query else ""))
            if path in ("/atlas", "/capture", "/topic"):
                return self._redirect(path + "/" + (f"?{url.query}" if url.query else ""))
            if path == "/atlas/":
                html = graphview.page(state.map())
                html = html.replace("connect-src 'none'", "connect-src 'self'")  # the page may post decisions back
                return self._send(HTTPStatus.OK, html.encode(), HTML, CSP["atlas"])
            if path == "/api/people/attribution":
                try:
                    return self._json(HTTPStatus.OK, people_review.detail(vault_path, args.get('claim','')))
                except KeyError:
                    return self._json(HTTPStatus.NOT_FOUND, {'error':'Claim not found.'})
            if path == "/api/people/backfill":
                return self._json(HTTPStatus.OK, people_backfill.preview(vault_path))
            if path.startswith('/api/people/backfill/'):
                try:
                    return self._json(HTTPStatus.OK, people_backfill.job(vault_path,path.rsplit('/',1)[-1]))
                except KeyError:
                    return self._json(HTTPStatus.NOT_FOUND, {'error':'Update not found.'})
            if path == "/api/data":
                return self._json(HTTPStatus.OK, state.map())
            if path == "/api/proposals":
                return self._json(HTTPStatus.OK, state.map()["proposals"])
            if path == "/api/topics":
                return self._json(HTTPStatus.OK, state.catalog())
            if path == "/api/consolidate":
                return self._json(HTTPStatus.OK, review.status())
            if path == "/api/dossier":
                try:
                    at = datetime.fromisoformat(args["at"]) if args.get("at") else None
                except ValueError:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": "at must be an ISO date."})
                result = state.dossier(args.get("topic") or None, args.get("q") or None,
                                       at.replace(tzinfo=at.tzinfo or timezone.utc) if at else None)
                return self._json(HTTPStatus.OK if result else HTTPStatus.NOT_FOUND,
                                  result or {"error": "Nothing in your knowledge matches that yet."})
            if path == "/api/subject":
                s = state.subject(args.get("id") or None, args.get("q") or None)
                if s is None:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "Nothing in your knowledge matches that yet."})
                return self._json(HTTPStatus.OK, {**s, **subject.plan(s, state.marks())})
            if path in ("/api/subject/results", "/api/path"):
                s = state.subject(args.get("id") or None, args.get("q") or None)
                if s is None:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "Nothing in your knowledge matches that yet."})
                try:
                    if path == "/api/path":
                        levels = subject.path(s, args.get("cluster") or None, args.get("source") or None,
                                              args.get("idea") or None)
                        return self._json(HTTPStatus.OK, {"levels": levels})
                    return self._json(HTTPStatus.OK, subject.results(
                        s, subject.plan(s, state.marks()), args.get("group_by") or "cluster_home",
                        args.get("scope") or None, args.get("only_matches", "1") not in ("0", "false")))
                except ValueError as error:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                except KeyError:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "No such cluster, source, Idea or topic here."})
            if (match := SOURCE.match(path)) and match[2] == "context":
                result = state.context(args.get("id") or None, args.get("q") or None, match[1])
                return self._json(HTTPStatus.OK if result else HTTPStatus.NOT_FOUND,
                                  result or {"error": "No such source in this subject."})
            if path == "/api/document":
                result = state.document(args.get("id") or "")
                return self._json(HTTPStatus.OK if result else HTTPStatus.NOT_FOUND,
                                  result or {"error": "No such source in the vault."})
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
                csp = "atlas" if path.startswith("/atlas/") else "topic" if path == "/topic/" else "page"
                return self._send(HTTPStatus.OK, file.read_bytes(), kind, CSP[csp])
            return self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

        def do_POST(self):
            origin = self.headers.get("Origin")
            if not self._local() or self.headers.get("X-Refresher") != "1" or \
                    (origin is not None and origin not in {f"http://{h}" for h in self._hosts()}):
                return self._json(HTTPStatus.FORBIDDEN, {"error": "Changes are taken only from the app itself."})
            path = urlsplit(self.path).path
            if path == "/api/consolidate":  # no body: the button only says "now"
                started = review.start()
                return self._json(HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT,
                                  {**review.status(), **({} if started else {"error": "A review is already running."})})
            decide, retry, source = DECIDE.match(path), RETRY.match(path), SOURCE.match(path)
            if source and source[2] != "state":
                source = None
            people_change = path in {'/api/people/attribution', '/api/people/backfill'}
            if not (decide or retry or source or people_change or path in ("/api/submissions", "/api/reading")):
                return self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                return self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "Use JSON."})
            if retry:
                try:
                    return self._json(HTTPStatus.ACCEPTED, workspace.retry(retry[1]))
                except ValueError as error:
                    return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._json(HTTPStatus.BAD_REQUEST, {'error':'Invalid content length.'})
            if not 0 < length <= (MAX_DECISION if decide or people_change else MAX_REQUEST):
                return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE if length else HTTPStatus.BAD_REQUEST,
                                  {"error": "Send a JSON body; uploads go up to 20 MB."})
            if people_change:
                try:
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body,dict):
                        raise ValueError('Send a JSON object.')
                    if path == '/api/people/attribution':
                        if not isinstance(body.get('claim_id'),str) or len(body['claim_id']) > 180:
                            raise ValueError('A claim_id is required.')
                        result = people_review.save(vault_path,body['claim_id'],body)
                        rebuild(vault_path, state.include_unreviewed)
                        return self._json(HTTPStatus.OK,result)
                    return self._json(HTTPStatus.ACCEPTED,people_backfill.start(vault_path,body,workspace.worker))
                except vault.StaleToken as error:
                    return self._json(HTTPStatus.CONFLICT, {'error':str(error)})
                except KeyError:
                    return self._json(HTTPStatus.NOT_FOUND, {'error':'Claim not found.'})
                except (ValueError,TypeError,UnicodeError) as error:
                    return self._json(HTTPStatus.BAD_REQUEST, {'error':str(error)})
                except OSError:
                    return self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {'error':'Could not save this change. Check server storage.'})
            if decide:
                return self._decide(decide[1], length)
            if path == "/api/reading":
                return self._reading(length)
            if source:
                return self._source_state(source[1], length)
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

        def _reading(self, length: int):
            """{"item": id, "state": "read" | "skimmed" | "unread"} marks a source; {"visit": scope id} notes a visit."""
            if length > MAX_DECISION:
                return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Too long."})
            try:
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Send an object.")
                at = datetime.now(timezone.utc)
                if isinstance(body.get("visit"), str):
                    reading.visit(vault_path, body["visit"], at)
                elif isinstance(body.get("item"), str) and isinstance(body.get("state"), str):
                    reading.mark(vault_path, body["item"], body["state"], at)
                else:
                    raise ValueError("Send item and state, or visit.")
            except (ValueError, UnicodeError) as error:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return self._json(HTTPStatus.OK, {"ok": True, "at": at.isoformat()})

        def _source_state(self, item_id: str, length: int):
            """{"state": "read" | "skimmed" | "unread", "id" or "q": the subject} marks a source and returns the plan
            and coverage recomputed for that subject."""
            if length > MAX_DECISION:
                return self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "Too long."})
            try:
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict) or not isinstance(body.get("state"), str):
                    raise ValueError("Send a state: read, skimmed or unread.")
                s = state.subject(body.get("id") or None, body.get("q") or None)
                if s is None or item_id not in s["sources"]:
                    return self._json(HTTPStatus.NOT_FOUND, {"error": "No such source in this subject."})
                reading.mark(vault_path, item_id, body["state"], datetime.now(timezone.utc))
            except (ValueError, UnicodeError) as error:
                return self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return self._json(HTTPStatus.OK, subject.plan(s, state.marks()))

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
    people_backfill.recover(vault_path)
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
