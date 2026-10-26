"""Explicit schema-1 copy migration. Never edits or replaces the source authority."""
import os
from pathlib import Path
import uuid

from .common import local_storage, overlaps, require
from .storage import Database
from .intake import packages
from .review import snapshot


def migrate_copy(source, destination, *, fault=None):
    destination = local_storage(destination)
    require(source.schema == 1, 'migrate-copy requires a schema 1 source')
    require(destination.suffix == '.sqlite', 'destination must end in .sqlite')
    require(not destination.exists(), 'migration requires a fresh destination')
    require(not overlaps(destination.parent, source.path.parent), 'migration copy must use a separate directory')
    for row in source.conn.execute('SELECT root FROM namespaces'):
        require(not overlaps(destination.parent, Path(row[0])), 'migration copy overlaps a source vault')
    destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temporary = destination.with_name('.migration-' + uuid.uuid4().hex + '.sqlite')
    target = None
    try:
        with source.transaction(write=False):
            check = source.doctor()
            require(check['integrity'] == 'ok' and not check['foreign_key_errors'] and not check['corrupt_blobs'],
                    'source integrity failed; no migration')
            before = snapshot(source)  # also rejects incomplete package/evidence records
            target = Database(temporary, create=True)
            with target.transaction():
                for table in ('namespaces', 'blobs', 'batches', 'packages', 'associations', 'sightings', 'quarantine', 'events', 'heads'):
                    for row in source.conn.execute(f'SELECT * FROM {table}'):
                        row = dict(row)
                        if table == 'sightings':
                            row['evidence_digest'] = target.retain_evidence(source.evidence(row))
                            del row['evidence']
                        columns = ','.join(row)
                        target.conn.execute(f'INSERT INTO {table} ({columns}) VALUES ({",".join("?" for _ in row)})', tuple(row.values()))
                    if fault: fault(table)
            require(snapshot(target) == before, 'copy semantic snapshot mismatch')
            check = target.doctor()
            require(check['integrity'] == 'ok' and not check['foreign_key_errors'] and not check['corrupt_blobs'],
                    'migrated copy integrity failed')
            target.close(); target = None
            if fault: fault('before_publish')
            # O_EXCL-like publication, no overwriting a raced destination.
            os.link(temporary, destination)
            temporary.unlink()
            fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
        return {'destination': str(destination), 'schema': 2, 'source_unchanged': True,
                'semantic_snapshot_equal': True, 'integrity': 'ok'}
    finally:
        if target is not None: target.close()
        if temporary.exists(): temporary.unlink()
