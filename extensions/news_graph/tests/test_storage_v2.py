"""Payload reuse differs from legitimate new observation metadata."""
import pytest
from conftest import fixture_record, publish
from newsmap_sidecar.intake import import_batch, packages
from newsmap_sidecar.common import NewsmapError, encoded


def test_five_imports_keep_payload_constant_and_bounded_metadata(workspace):
    db, vault = workspace
    record, raw = fixture_record(text=('é🧭' * 6000) + '\n' + 'Second exact occurrence')
    publish(vault, record, raw)
    import_batch(db, 'demo', vault)
    initial = packages(db)[0]['evidence']
    before = db.path.stat().st_size
    for _ in range(4): import_batch(db, 'demo', vault)
    assert packages(db)[0]['evidence'] == initial
    assert db.conn.execute('SELECT count(*) FROM sightings').fetchone()[0] == 5
    # Small sightings may allocate pages, but must not repeat the 60 KiB anchors.
    assert db.path.stat().st_size - before < len(encoded(initial))
    assert db.schema == 2


def test_metadata_limit_rolls_back_event(workspace):
    db, _ = workspace
    before = db.conn.execute('SELECT count(*) FROM events').fetchone()[0]
    db.metadata_quota = 128
    with pytest.raises(NewsmapError, match='metadata'):
        with db.transaction(): db.event('fixture', {'value': 'a' * 1024}, 'fixture_operator')
    assert db.conn.execute('SELECT count(*) FROM events').fetchone()[0] == before


def legacy_copy(db, path):
    """Build a small frozen schema-1 fixture, not an in-place production migration."""
    import sqlite3
    from newsmap_sidecar.storage import SCHEMA, Database
    path.parent.mkdir()
    target = sqlite3.connect(path)
    target.executescript(SCHEMA.replace('evidence_digest TEXT NOT NULL REFERENCES blobs(digest)', 'evidence TEXT NOT NULL'))
    target.execute("INSERT INTO meta VALUES ('schema','1')")
    for table in ('namespaces', 'blobs', 'batches', 'packages', 'associations', 'sightings', 'quarantine', 'events', 'heads'):
        for row in db.conn.execute(f'SELECT * FROM {table}'):
            row = dict(row)
            if table == 'sightings':
                row['evidence'] = encoded(db.evidence(row)).decode(); del row['evidence_digest']
            target.execute(f'INSERT INTO {table} ({",".join(row)}) VALUES ({",".join("?" for _ in row)})', tuple(row.values()))
    target.commit(); target.close()
    return Database(path)


def test_explicit_copy_migration_preserves_source_and_decision_identity(workspace, tmp_path):
    from newsmap_sidecar.migration import migrate_copy
    from newsmap_sidecar.storage import Database
    from newsmap_sidecar.review import snapshot
    from test_review import prepare, proposal, approve
    db, vault = workspace
    refs = prepare(db, vault); approve(db, proposal(db, refs))
    old = legacy_copy(db, tmp_path / 'legacy' / 'v1.sqlite')
    original = old.path.read_bytes()
    try:
        with pytest.raises(NewsmapError, match='read-only'):
            import_batch(old, 'demo', vault)
        result = migrate_copy(old, tmp_path / 'copy' / 'v2.sqlite')
        new = Database(result['destination'])
        try: assert snapshot(old) == snapshot(new)
        finally: new.close()
        assert old.path.read_bytes() == original
    finally: old.close()


@pytest.mark.parametrize('boundary', ['sightings', 'events', 'before_publish'])
def test_copy_interruption_never_publishes_or_mutates_source(workspace, tmp_path, boundary):
    from newsmap_sidecar.migration import migrate_copy
    db, vault = workspace
    r, raw = fixture_record(); publish(vault, r, raw); import_batch(db, 'demo', vault)
    old = legacy_copy(db, tmp_path / 'legacy' / 'v1.sqlite')
    before = old.path.read_bytes(); out = tmp_path / 'copy' / 'v2.sqlite'
    def fault(point):
        if point == boundary: raise InterruptedError('synthetic interruption')
    try:
        with pytest.raises(InterruptedError): migrate_copy(old, out, fault=fault)
        assert not out.exists() and old.path.read_bytes() == before
    finally: old.close()
