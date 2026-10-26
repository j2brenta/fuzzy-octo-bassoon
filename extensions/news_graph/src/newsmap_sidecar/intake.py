"""Explicit bounded batches over idle producer outputs; no producer imports."""
import json
import os
from pathlib import Path

from . import adapter
from .common import (NewsmapError, BusyError, StaleError, Vault, Oversize, UnsafePath, checked_path, digest, encoded,
                     now, require, relative_parts, overlaps, identifier)


def _quarantine(db, batch, path, reason, data=None, status='quarantined'):
    with db.transaction():
        h = None
        if data is not None:
            used = db.conn.execute('SELECT coalesce(sum(length(data)),0) FROM blobs').fetchone()[0]
            if used + len(data) <= db.quota or db.conn.execute('SELECT 1 FROM blobs WHERE digest=?', (digest(data),)).fetchone():
                h = db.blob(data)
        db.conn.execute('INSERT INTO quarantine(batch,path,status,reason,digest,blob_digest,full_bytes_retained) VALUES (?,?,?,?,?,?,?)',
                        (batch, path, status, reason, digest(data) if data is not None else None, h, int(h is not None)))


def import_batch(db, namespace, root, scope='processed', *, max_items=200, max_bytes=10 * 1024 * 1024,
                 fault=None):
    root = checked_path(root)
    require(type(max_items) is int and 1 <= max_items <= 200, 'batch item cap must be 1..200')
    require(type(max_bytes) is int and 0 < max_bytes <= 10 * 1024 * 1024, 'artifact byte cap must be <=10 MiB')
    require(not overlaps(root, db.path.parent), 'input vault overlaps authority')
    registered = db.conn.execute('SELECT root FROM namespaces WHERE id=?', (namespace,)).fetchone()
    require(registered is not None and registered[0] == str(root), 'register namespace/root explicitly before import')
    parts = relative_parts(scope)
    require(parts[0] == 'processed' and len(parts) <= 2, 'scope must be processed or processed/<item>.json')
    vault = Vault(root, max_bytes)
    def check_root():
        current = db.conn.execute('SELECT root FROM namespaces WHERE id=?', (namespace,)).fetchone()
        if current is None or current[0] != str(root):
            raise StaleError('namespace root changed during import; register/inspect and retry explicitly')
    try:
        if len(parts) == 1:
            folder = os.open('processed', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=vault.fd)
            try:
                names = sorted(n for n in os.listdir(folder) if n.endswith('.json'))
            finally:
                os.close(folder)
            paths = ['processed/' + n for n in names]
        else:
            paths = [scope]
        over_limit = len(paths) > max_items
        with db.transaction():
            check_root()
            batch = db.conn.execute('INSERT INTO batches(namespace,scope,started,status,detail) VALUES (?,?,?,?,?)',
                                    (namespace, scope, now(), 'running', encoded({'source_root': str(root)}).decode())).lastrowid
        counts = {'valid': 0, 'pending_evidence': 0, 'quarantined': 0, 'unsupported_contract': 0,
                  'deferred': max(0, len(paths) - max_items)}
        for path in paths[:max_items]:
            data = None
            try:
                require(Path(path).suffix == '.json' and identifier(Path(path).stem),
                        'expected processed/<64-hex-item>.json; unexpected filename not read')
                data = vault.read(path)
                record, contract = adapter.envelope(data, Path(path).name)
                if contract == 'unsupported_contract':
                    _quarantine(db, batch, path, 'unknown stage/builder/policy; not interpreted', data, contract)
                    counts[contract] += 1
                    continue
                raw_data, ev, reason = None, None, ''
                try:
                    raw_data = vault.read(record['raw_path'])
                    ev = adapter.evidence(record, raw_data)
                    require(len(encoded(ev)) <= max_bytes, 'resolved evidence exceeds derived-data cap')
                except (OSError, NewsmapError, TypeError, KeyError) as exc:
                    ev = None
                    # Never publish OS exception strings containing absolute filesystem paths.
                    reason = str(exc) if isinstance(exc, NewsmapError) else type(exc).__name__
                    retained = db.conn.execute('''SELECT b.data FROM blobs b JOIN sightings s ON s.text_digest=b.digest
                        WHERE s.namespace=? AND s.item=? AND s.admission='valid' AND b.digest=? LIMIT 1''',
                        (namespace, record['item_id'], record['source_content_hash'])).fetchone()
                    if retained:
                        try:
                            ev = adapter.evidence(record, retained_text=bytes(retained[0]).decode('utf-8'))
                            require(len(encoded(ev)) <= max_bytes, 'resolved evidence exceeds derived-data cap')
                            reason = 'current raw unavailable/mismatched; using previously retained exact text'
                        except (NewsmapError, UnicodeError):
                            ev = None
                admission = 'valid' if ev else 'pending_evidence'
                with db.transaction():
                    check_root()
                    envelope_hash = db.blob(data)
                    raw_hash = db.blob(raw_data) if raw_data is not None else None
                    text_hash = db.blob(ev['text'].encode('utf-8')) if ev else None
                    evidence_data = {k: v for k, v in ev.items() if k != 'text'} if ev else {}
                    key = (namespace, record['item_id'], record['bundle_id'])
                    db.conn.execute('INSERT OR IGNORE INTO packages VALUES (?,?,?,?,?)', (*key, envelope_hash, contract))
                    db.conn.execute('INSERT OR IGNORE INTO associations VALUES (?,?,?,?)', (*key, envelope_hash))
                    db.conn.execute('INSERT INTO sightings(batch,namespace,item,bundle,artifact_digest,path,raw_digest,text_digest,admission,evidence_digest,reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                                    (batch, *key, envelope_hash, path, raw_hash, text_hash, admission, db.retain_evidence(evidence_data), reason))
                    if db.head(namespace, record['item_id']) is None:
                        event = db.event('select', {'namespace': namespace, 'item': record['item_id'],
                                                   'bundle': record['bundle_id'], 'method': 'first_valid_package_bootstrap'}, 'import_bootstrap')
                        db.conn.execute('INSERT INTO heads VALUES (?,?,?,?)', (*key, event))
                    if fault:
                        fault('before_commit')
                if fault:
                    fault('after_commit')
                counts[admission] += 1
            except (BusyError, StaleError):
                raise
            except (OSError, NewsmapError, TypeError, KeyError, OverflowError, RecursionError) as exc:
                reason = str(exc) if isinstance(exc, NewsmapError) else type(exc).__name__
                _quarantine(db, batch, path, reason, data)
                counts['quarantined'] += 1
        changed = not vault.unchanged()
        status = 'changed' if changed else ('partial' if over_limit or counts['quarantined'] or
                  counts['unsupported_contract'] or counts['pending_evidence'] else 'complete')
        with db.transaction():
            check_root()
            db.conn.execute('UPDATE batches SET finished=?,status=?,detail=? WHERE id=?',
                            (now(), status, encoded({**counts, 'source_root': str(root)}).decode(), batch))
        return {'batch': batch, 'status': status, **counts,
                'availability': 'upstream availability not confirmed', 'snapshot_guarantee': 'per-item; not globally atomic'}
    finally:
        vault.close()


def packages(db, *, keys=None, bounded=False):
    """One consistent snapshot, with best retained evidence and all state axes."""
    with db.transaction(write=False):
        rows = []
        query, values = 'SELECT * FROM packages', []
        if keys is not None:
            require(len(keys) <= 200, 'view package work limit exceeded')
            query += ' WHERE ' + (' OR '.join('(namespace=? AND item=? AND bundle=?)' for _ in keys) or '0')
            values = [v for k in keys for v in k]
        query += ' ORDER BY namespace,item,bundle'
        if bounded: query += ' LIMIT 201'
        selected = list(db.conn.execute(query, values))
        require(not bounded or len(selected) <= 200, 'view package work limit exceeded; narrow explicit grants')
        admitted_bytes = 0
        for pkg in selected:
            if bounded:
                admitted_bytes += db.conn.execute('SELECT length(data) FROM blobs WHERE digest=?', (pkg['envelope_digest'],)).fetchone()[0]
                require(admitted_bytes <= 8 * 1024 * 1024, 'view input byte limit exceeded')
            row = dict(pkg)
            record = json.loads(db.bytes(row['envelope_digest']))
            obs = db.conn.execute('''SELECT * FROM sightings WHERE namespace=? AND item=? AND bundle=?
              ORDER BY (admission='valid') DESC,id DESC LIMIT 1''',
                                  (row['namespace'], row['item'], row['bundle'])).fetchone()
            require(obs is not None, 'incomplete package: no retained observation')
            history = db.conn.execute('''SELECT min(batch),max(batch) FROM sightings
              WHERE namespace=? AND item=? AND bundle=?''', (row['namespace'], row['item'], row['bundle'])).fetchone()
            if bounded and db.schema == 2:
                admitted_bytes += db.conn.execute('SELECT length(data) FROM blobs WHERE digest=?', (obs['evidence_digest'],)).fetchone()[0]
                require(admitted_bytes <= 8 * 1024 * 1024, 'view input byte limit exceeded')
            elif bounded:
                admitted_bytes += len(obs['evidence'].encode('utf-8'))
                require(admitted_bytes <= 8 * 1024 * 1024, 'view input byte limit exceeded')
            head = db.head(row['namespace'], row['item'])
            selected = bool(head and head['bundle'] == row['bundle'])
            rows.append({**row, 'record': record, 'admission': obs['admission'], 'evidence': db.evidence(obs),
                         'text_digest': obs['text_digest'], 'quality': record['quality_status'], 'selected': selected,
                         'eligible': selected and obs['admission'] == 'valid' and record['quality_gate_passed'],
                         'head_token': f"{head['bundle']}:{head['event']}" if head else None,
                         'evidence_observation_reason': obs['reason'],
                         'first_observed_batch': history[0], 'last_observed_batch': history[1],
                         'availability': 'upstream availability not confirmed'})
        return rows
