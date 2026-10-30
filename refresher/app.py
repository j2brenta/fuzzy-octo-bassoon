"""refresher serve: the one local app on one vault: add a source, see the Atlas, decide the review's proposals.

    /           the topics: pick a subject, or type one
    /topic/     a subject read cluster by cluster (refresher/subject.py): a plan, the clusters, a reader beside it
    /capture/   add a source and follow it through ingestion (Olga's workspace, refresher/web.py)
    /atlas/     the map (Victor's viewer, refresher/assets/map/)
    /review/    the same page with the proposals waiting for you
    /api/...    submissions and uploads, the map's data, topics and dossiers, proposals and decisions

A web capture is placed in the graph like a Telegram one (refresher/place.py), so it shows on the map. Local only:
it binds to 127.0.0.1 unless --host says otherwise, answers only to a Host it knows (localhost, 127.0.0.1, any
--allow-host), and takes a POST only with the X-Refresher header and, when the browser sends one, a same-origin
Origin, so another site open in the browser cannot post. A decision goes through vault.decide(): the token the page
was given must still be current, or the answer is 409 and the page shows the current version. The replayed vault is
kept until a file it reads changes, so a page load does not replay the whole vault every time.
"""
import logging
import os
import stat
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Lock, RLock, Thread

from graph.memory import MemoryStore
from refresher import area as area_engine, brief, consolidate, context as context_engine, dossier, graphview, \
    people_backfill, profile, reading, subject, thesis_relate, threads, vault
from refresher.web import Workspace, read_json
from refresher.serving.base import Base
from refresher.serving.files import Files
from refresher.serving.pages import Pages
from refresher.serving.posts import Posts, rebuild  # noqa: F401  (people_backfill imports rebuild from here)
from refresher.serving.routes import CSP, FILES, HTML, JS, MAX_DECISION, MAX_REQUEST  # noqa: F401  (as before)
from refresher.serving.subjects import Subjects

PORT = 8080
MAP_TTL = timedelta(minutes=5)  # freshness moves slowly; the map is recomputed at least this often
# What vault.replay reads; a change to any of these files replays the vault again.
WATCHED = ("raw", "processed", "relations", "graph-reviews", "people-reviews", "people", "translations", "summaries",
           "theses", "thesis-relations", "threads",
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
        self._pool = self._groups = None  # every source's theses with vectors, and the major ideas over them
        self._contexts: dict[tuple, dict] = {}

    def store(self) -> MemoryStore:
        key = fingerprint(self.vault)
        with self._lock:
            if key != self._key:
                self._store = vault.replay(self.vault, include_unreviewed=self.include_unreviewed)
                self._store.topics()  # build the groups now, while no one else reads
                self._key, self._map, self._subjects = key, None, {}
                self._pool = self._groups = None
                self._contexts = {}
            return self._store

    def pool(self) -> thesis_relate.Pool:
        with self._lock:
            store = self.store()
            if self._pool is None:
                self._pool = thesis_relate.Pool(store)
            return self._pool

    def brief(self, key: str | None, query: str | None, at: datetime | None = None) -> dict | None:
        """The Context Engine on a subject (refresher/context.py) with what is written of it, once per vault state
        (and per moment, when one is asked for)."""
        with self._lock:
            store, pool = self.store(), self.pool()
            now = at or datetime.now(timezone.utc)
            if at is None and self._groups is None:
                self._groups = context_engine.groups(store, pool, now)
            cache = (key, query, at.isoformat() if at else None)
            if cache not in self._contexts:
                c = context_engine.build(store, key, query, now, pool, self._groups if at is None else None)
                if c is not None:
                    c["area"] = area_engine.area(store, c, pool)
                    c["area_full"] = area_engine.area(store, c, pool, full=True)
                    c["details"] = area_engine.details(store, c)
                self._contexts[cache] = c
            c = self._contexts[cache]
        if c is None:
            return None
        with self._lock:
            further = area_engine.further(self.store(), c, c["area"], self.pool(), self.marks())
        return {**c, "written": brief.write(self.vault, c, None), "further": further}

    def brief_source(self, key: str | None, query: str | None, item_id: str) -> dict | None:
        """One source read inside its brief: text, passages by idea, where it sits, where to go next."""
        c = self.brief(key, query)
        if c is None:
            return None
        with self._lock:
            return area_engine.reader(self.store(), c, c["area"], self.pool(), item_id, self.marks())

    def brief_entity(self, key: str | None, query: str | None, entity_id: str) -> dict | None:
        """A person or organization opened from a brief: standing, sources, facts, neighbours for the mini map."""
        c = self.brief(key, query)
        if c is None:
            return None
        with self._lock:
            return profile.card(self.store(), self.pool(), c, entity_id, datetime.fromisoformat(c["at"]))

    def thread(self, name: str, at: datetime | None = None) -> dict | None:
        with self._lock:
            return threads.view(self.vault, self.store(), name, at, self.pool())

    def threads(self) -> list[dict]:
        with self._lock:
            return threads.summary(self.vault, self.store(), pool=self.pool())

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

    def named(self, query: str | None) -> list[dict]:
        if not query:
            return []
        with self._lock:
            return dossier.named(self.store(), query)

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
                self._map = {**graphview.map_data(store, now, vault=self.vault), "served": True}
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
    """The request handler for one vault: the routes of refresher/serving/, with what they answer from."""
    return type("Handler", (Posts, Pages, Subjects, Files, Base), {
        "vault_path": vault_path, "workspace": workspace, "state": state, "review": review or Review(vault_path),
        "allow_hosts": tuple(allow_hosts), "log": staticmethod(log)})


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
