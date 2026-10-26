"""Extension snapshot/decision authority; producer Vault JSON remains authoritative upstream."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

from .common import (NewsmapError, BusyError, StaleError, digest, encoded, now, require,
                     local_storage, overlaps, checked_path, nonblank)

SCHEMA = '''
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE namespaces(id TEXT PRIMARY KEY, root TEXT NOT NULL);
CREATE TABLE blobs(digest TEXT PRIMARY KEY, data BLOB NOT NULL);
CREATE TABLE batches(
 id INTEGER PRIMARY KEY, namespace TEXT NOT NULL REFERENCES namespaces(id),
 scope TEXT NOT NULL, started TEXT NOT NULL, finished TEXT,
 status TEXT NOT NULL CHECK(status IN ('running','complete','partial','changed')), detail TEXT NOT NULL);
CREATE TABLE packages(
 namespace TEXT NOT NULL REFERENCES namespaces(id), item TEXT NOT NULL, bundle TEXT NOT NULL,
 envelope_digest TEXT NOT NULL REFERENCES blobs(digest), adapter TEXT NOT NULL,
 PRIMARY KEY(namespace,item,bundle));
CREATE TABLE associations(
 namespace TEXT NOT NULL, item TEXT NOT NULL, bundle TEXT NOT NULL,
 artifact_digest TEXT NOT NULL REFERENCES blobs(digest),
 PRIMARY KEY(namespace,item,bundle,artifact_digest),
 FOREIGN KEY(namespace,item,bundle) REFERENCES packages(namespace,item,bundle));
CREATE TABLE sightings(
 id INTEGER PRIMARY KEY, batch INTEGER NOT NULL REFERENCES batches(id),
 namespace TEXT NOT NULL, item TEXT NOT NULL, bundle TEXT NOT NULL,
 artifact_digest TEXT NOT NULL REFERENCES blobs(digest), path TEXT NOT NULL,
 raw_digest TEXT REFERENCES blobs(digest), text_digest TEXT REFERENCES blobs(digest),
 admission TEXT NOT NULL CHECK(admission IN ('valid','pending_evidence')),
 evidence_digest TEXT NOT NULL REFERENCES blobs(digest), reason TEXT NOT NULL,
 FOREIGN KEY(namespace,item,bundle,artifact_digest) REFERENCES associations(namespace,item,bundle,artifact_digest));
CREATE TABLE quarantine(
 id INTEGER PRIMARY KEY, batch INTEGER NOT NULL REFERENCES batches(id), path TEXT NOT NULL,
 status TEXT NOT NULL, reason TEXT NOT NULL, digest TEXT,
 blob_digest TEXT REFERENCES blobs(digest), full_bytes_retained INTEGER NOT NULL CHECK(full_bytes_retained IN (0,1)));
CREATE TABLE events(
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE heads(
 namespace TEXT NOT NULL, item TEXT NOT NULL, bundle TEXT NOT NULL, event INTEGER NOT NULL REFERENCES events(id),
 PRIMARY KEY(namespace,item), FOREIGN KEY(namespace,item,bundle) REFERENCES packages(namespace,item,bundle));
CREATE INDEX sightings_package ON sightings(namespace,item,bundle);
'''


class Database:
    def __init__(self, path, *, create=False, busy_ms=5000, quota=32 * 1024 * 1024,
                 metadata_quota=8 * 1024 * 1024, database_quota=64 * 1024 * 1024):
        self.path = local_storage(path)
        self.quota = quota
        self.metadata_quota = metadata_quota
        self.database_quota = database_quota
        require(self.path.suffix == '.sqlite', 'authority path must end in .sqlite')
        require(create or self.path.is_file(), 'authority does not exist; run init')
        if create:
            require(not self.path.exists(), 'init refuses an existing authority')
            self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        self.conn = sqlite3.connect(self.path, isolation_level=None, timeout=busy_ms / 1000)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.execute('PRAGMA synchronous=FULL')
        if sys.platform == 'darwin':
            self.conn.execute('PRAGMA fullfsync=ON')
        mode = self.conn.execute('PRAGMA journal_mode').fetchone()[0]
        require(mode == 'delete', 'authority must use DELETE journal mode')
        if create:
            self.conn.executescript('BEGIN IMMEDIATE;\n' + SCHEMA)
            try:
                self.conn.execute("INSERT INTO meta VALUES ('schema','2')")
                for table in ('blobs', 'packages', 'associations', 'sightings', 'quarantine', 'events'):
                    for action in ('UPDATE', 'DELETE'):
                        self.conn.execute(f'''CREATE TRIGGER immutable_{table}_{action.lower()}
                          BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable authority record'); END''')
                self.conn.execute('COMMIT')
            except BaseException:
                self.conn.execute('ROLLBACK')
                raise
        self.schema = int(self.conn.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0])
        require(self.schema in {1, 2}, 'unsupported database schema')
        self.conn.execute('PRAGMA cache_size=-2048')
        self.conn.execute('PRAGMA temp_store=FILE')
        if self.schema == 2:
            pagesize = self.conn.execute('PRAGMA page_size').fetchone()[0]
            self.conn.execute(f'PRAGMA max_page_count={max(1, database_quota // pagesize)}')

    def close(self):
        self.conn.close()

    @contextmanager
    def transaction(self, write=True):
        if not write and self.conn.in_transaction:
            # A caller's explicit transaction already provides the consistent snapshot.
            yield self.conn
            return
        if write and self.conn.in_transaction:
            require(self.schema == 2, 'schema 1 is read-only; use explicit migrate-copy')
            savepoint = 'nested_' + uuid.uuid4().hex
            self.conn.execute('SAVEPOINT ' + savepoint)
            try:
                yield self.conn
                self.conn.execute('RELEASE ' + savepoint)
            except BaseException:
                self.conn.execute('ROLLBACK TO ' + savepoint)
                self.conn.execute('RELEASE ' + savepoint)
                raise
            return
        try:
            require(not write or self.schema == 2, 'schema 1 is read-only; use explicit migrate-copy')
            self.conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            yield self.conn
            if write:
                require(self.metadata_size() <= self.metadata_quota, 'metadata quota exceeded; transaction rolled back')
            self.conn.execute('COMMIT')
        except BaseException as exc:
            if self.conn.in_transaction:
                self.conn.execute('ROLLBACK')
            if isinstance(exc, sqlite3.OperationalError) and (getattr(exc, 'sqlite_errorcode', 0) & 255) in {
                    sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise BusyError('authority busy; retry the complete command') from None
            raise

    def metadata_size(self):
        total = 0
        for table in ('namespaces', 'batches', 'packages', 'associations', 'sightings', 'quarantine', 'events', 'heads'):
            columns = [r[1] for r in self.conn.execute(f'PRAGMA table_info({table})')]
            expr = '+'.join(f'coalesce(length(CAST("{c}" AS BLOB)),0)' for c in columns)
            total += self.conn.execute(f'SELECT coalesce(sum({expr}),0) FROM {table}').fetchone()[0]
        return total

    def retain_evidence(self, evidence):
        # Resolver version is part of content identity; all arrays preserve order.
        return self.blob(encoded({'resolver': 'exact-unicode-v1', 'evidence': evidence}))

    def evidence(self, observation):
        if self.schema == 1:
            return json.loads(observation['evidence'])
        value = json.loads(self.bytes(observation['evidence_digest']))
        require(value['resolver'] == 'exact-unicode-v1', 'unsupported retained resolver contract')
        return value['evidence']

    def event(self, kind, payload, actor):
        require(self.conn.in_transaction, 'events require an explicit transaction')
        require(nonblank(actor), 'decision actor required')
        return self.conn.execute('INSERT INTO events(kind,actor,at,payload) VALUES (?,?,?,?)',
                                 (kind, actor, now(), encoded(payload).decode())).lastrowid

    def blob(self, data):
        require(self.conn.in_transaction, 'blobs require an explicit transaction')
        h = digest(data)
        if not self.conn.execute('SELECT 1 FROM blobs WHERE digest=?', (h,)).fetchone():
            used = self.conn.execute('SELECT coalesce(sum(length(data)),0) FROM blobs').fetchone()[0]
            require(used + len(data) <= self.quota, 'retained-input quota exceeded; no evidence evicted')
            self.conn.execute('INSERT INTO blobs VALUES (?,?)', (h, data))
        return h

    def bytes(self, h):
        row = self.conn.execute('SELECT data FROM blobs WHERE digest=?', (h,)).fetchone()
        require(row is not None, 'retained artifact not found')
        return bytes(row[0])

    def register(self, namespace, root, *, relocate=False, actor='operator'):
        require(nonblank(namespace) and len(namespace) <= 128 and '/' not in namespace and '\\' not in namespace,
                'namespace must be a short logical name, not a path')
        root = checked_path(root)
        require(root.is_dir(), 'vault must be a directory')
        require(not overlaps(root, self.path.parent), 'vault and authority directory must be separate')
        with self.transaction():
            existing = self.conn.execute('SELECT root FROM namespaces WHERE id=?', (namespace,)).fetchone()
            if existing and existing[0] != str(root):
                require(relocate, 'namespace relocation requires explicit --relocate')
                self.event('namespace_relocate', {'namespace': namespace, 'root': str(root)}, actor)
                self.conn.execute('UPDATE namespaces SET root=? WHERE id=?', (str(root), namespace))
            elif not existing:
                self.conn.execute('INSERT INTO namespaces VALUES (?,?)', (namespace, str(root)))
                self.event('namespace_register', {'namespace': namespace, 'root': str(root)}, actor)
        return {'namespace': namespace, 'root': str(root)}

    def head(self, namespace, item):
        row = self.conn.execute('SELECT bundle,event FROM heads WHERE namespace=? AND item=?', (namespace, item)).fetchone()
        return dict(row) if row else None

    def select(self, namespace, item, bundle, expected, actor):
        with self.transaction():
            head = self.head(namespace, item)
            actual = None if head is None else f"{head['bundle']}:{head['event']}"
            if actual != expected:
                raise StaleError('selection changed; inspect current head and retry')
            require(self.conn.execute('SELECT 1 FROM packages WHERE namespace=? AND item=? AND bundle=?',
                                      (namespace, item, bundle)).fetchone() is not None, 'unknown package revision')
            event = self.event('select', {'namespace': namespace, 'item': item, 'bundle': bundle}, actor)
            self.conn.execute('INSERT INTO heads VALUES (?,?,?,?) ON CONFLICT(namespace,item) DO UPDATE SET bundle=excluded.bundle,event=excluded.event',
                              (namespace, item, bundle, event))
        return {'bundle': bundle, 'token': f'{bundle}:{event}'}

    def canonical(self):
        """Logical authority; no filesystem locations or SQLite page layout."""
        result = {}
        with self.transaction(write=False):
            for table in ('meta', 'namespaces', 'blobs', 'batches', 'packages', 'associations', 'sightings', 'quarantine', 'events', 'heads'):
                rows = []
                for row in self.conn.execute(f'SELECT * FROM {table}'):
                    r = dict(row)
                    if table == 'blobs':
                        r['data'] = digest(r['data'])
                    if table == 'namespaces':
                        r.pop('root')
                    if table == 'batches':
                        detail = json.loads(r['detail'])
                        detail.pop('source_root', None)
                        r['detail'] = detail
                    if table == 'events':
                        p = json.loads(r['payload'])
                        if r['kind'].startswith('namespace_'):
                            p.pop('root', None)
                        r['payload'] = p
                    rows.append(r)
                result[table] = sorted(rows, key=lambda r: encoded(r))
        return result

    def doctor(self):
        integrity = self.conn.execute('PRAGMA integrity_check').fetchone()[0]
        foreign = [tuple(r) for r in self.conn.execute('PRAGMA foreign_key_check')]
        corrupt = sum(digest(bytes(r['data'])) != r['digest'] for r in self.conn.execute('SELECT * FROM blobs'))
        return {'schema': self.schema, 'integrity': integrity, 'foreign_key_errors': len(foreign),
                'corrupt_blobs': corrupt, 'journal_mode': self.conn.execute('PRAGMA journal_mode').fetchone()[0],
                'synchronous': self.conn.execute('PRAGMA synchronous').fetchone()[0]}

    def rebuild_heads(self):
        """Recreate only the disposable selection index in recorded event order."""
        with self.transaction():
            selections = list(self.conn.execute("SELECT id,payload FROM events WHERE kind='select' ORDER BY id"))
            self.conn.execute('DELETE FROM heads')
            for event in selections:
                p = json.loads(event['payload'])
                self.conn.execute('''INSERT INTO heads VALUES (?,?,?,?) ON CONFLICT(namespace,item)
                    DO UPDATE SET bundle=excluded.bundle,event=excluded.event''',
                    (p['namespace'], p['item'], p['bundle'], event['id']))
            return {'selection_events_replayed': len(selections),
                    'heads': self.conn.execute('SELECT count(*) FROM heads').fetchone()[0]}

    def backup(self, destination):
        destination = local_storage(destination)
        require(not destination.exists(), 'backup requires a fresh destination')
        require(not overlaps(destination.parent, self.path.parent), 'backup must be in a separate directory')
        for row in self.conn.execute('SELECT root FROM namespaces'):
            require(not overlaps(destination.parent, Path(row[0])), 'backup overlaps a vault')
        destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        target = sqlite3.connect(destination)
        try:
            self.conn.backup(target)
        finally:
            target.close()
        restored = Database(destination)
        try:
            check = restored.doctor()
            require(check['integrity'] == 'ok' and not check['foreign_key_errors'] and not check['corrupt_blobs'],
                    'backup integrity check failed')
        finally:
            restored.close()
        return {'backup': str(destination), **check}
