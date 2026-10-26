from copy import deepcopy
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from newsmap_sidecar.common import NewsmapError, StaleError, UnsafePath, Vault, digest, fingerprint, parse_json
from newsmap_sidecar.intake import import_batch, packages
from newsmap_sidecar.storage import Database
from conftest import fixture_record, reseal, publish


def count(db, table):
    return db.conn.execute('SELECT count(*) FROM ' + table).fetchone()[0]


@pytest.mark.parametrize('version', [1, 2])
def test_supported_fingerprint_exact_bytes_and_multispans(workspace, version):
    db, vault = workspace
    r, raw = fixture_record(version=version)
    p = publish(vault, r, raw)
    original = p.read_bytes()
    assert import_batch(db, 'demo', vault)['valid'] == 1
    row = packages(db)[0]
    assert row['eligible']
    assert db.bytes(row['envelope_digest']) == original
    a, b = row['evidence']['claims'][0]['anchors']
    assert '🧭 cafe\u0301' in a['quote']
    assert b['quote'] == 'Not measured on mobile devices.'
    assert a['coordinate_unit'] == 'unicode_code_points'
    assert a['text_sha256'] == raw['content_hash']
    assert db.doctor()['integrity'] == 'ok'


def test_reserialization_outer_time_observations_and_unknown_core(workspace):
    db, vault = workspace
    r, raw = fixture_record()
    publish(vault, r, raw)
    import_batch(db, 'demo', vault)
    import_batch(db, 'demo', vault)
    assert count(db, 'packages') == count(db, 'associations') == 1
    assert count(db, 'sightings') == 2
    r['created_at'] = '2026-10-27T00:00:00+00:00'
    publish(vault, r, raw, ensure_ascii=True, indent=None)
    import_batch(db, 'demo', vault)
    assert count(db, 'packages') == 1 and count(db, 'associations') == 2
    r['unknown_extension'] = {'nested': ['retained', 42]}
    p = publish(vault, r, raw)
    assert import_batch(db, 'demo', vault)['quarantined'] == 1
    q = db.conn.execute('SELECT * FROM quarantine').fetchone()
    assert db.bytes(q['blob_digest']) == p.read_bytes()
    reseal(r)
    publish(vault, r, raw)
    assert import_batch(db, 'demo', vault)['valid'] == 1
    assert count(db, 'packages') == 2
    assert next(p for p in packages(db) if p['bundle'] == r['bundle_id'])['record']['unknown_extension']['nested'][1] == 42


@pytest.mark.parametrize('payload', [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}',
                                    b'{"a":1e999}', b'{"a":"\\ud800"}', b'\xff', b'{broken'])
def test_hostile_json_bounded_quarantine(workspace, payload):
    db, vault = workspace
    r, raw = fixture_record()
    p = publish(vault, r, raw)
    p.write_bytes(payload)
    result = import_batch(db, 'demo', vault)
    assert result['quarantined'] == 1
    assert count(db, 'packages') == 0
    q = db.conn.execute('SELECT * FROM quarantine').fetchone()
    assert q['full_bytes_retained'] == 1
    assert db.bytes(q['blob_digest']) == payload


def test_unsupported_is_distinct_and_inert(workspace):
    db, vault = workspace
    r, raw = fixture_record()
    r['quality_policy_version'] = 'future-policy'
    reseal(r)
    publish(vault, r, raw)
    assert import_batch(db, 'demo', vault)['unsupported_contract'] == 1
    assert not packages(db)
    assert db.conn.execute('SELECT status FROM quarantine').fetchone()[0] == 'unsupported_contract'


@pytest.mark.parametrize('alter', ['bool', 'negative', 'overflow', 'wrong_item', 'wrong_section', 'empty'])
def test_invalid_evidence_is_not_eligible(workspace, alter):
    db, vault = workspace
    r, raw = fixture_record()
    span = r['claims'][0]['evidence'][0]
    if alter == 'bool': span['start_char'] = False
    if alter == 'negative': span['start_char'] = -1
    if alter == 'overflow': span['end_char'] = len(raw['text']) + 1
    if alter == 'wrong_item': span['item_id'] = 'not-this-item'
    if alter == 'wrong_section': span['section_id'] = 'not-this-section'
    if alter == 'empty': r['claims'][0]['evidence'] = []
    reseal(r)
    publish(vault, r, raw)
    assert import_batch(db, 'demo', vault)['pending_evidence'] == 1
    assert not packages(db)[0]['eligible']


def test_raw_metadata_mismatch_never_changes_envelope_source(workspace):
    db, vault = workspace
    r, raw = fixture_record()
    raw['author_hint'] = {'name': 'Unverified mutable raw author'}
    raw['channel_metadata'] = {'parent': {'item_id': 'untrusted'}}
    publish(vault, r, raw)
    assert import_batch(db, 'demo', vault)['valid'] == 1
    row = packages(db)[0]
    assert row['evidence']['raw_basis'] == 'text_match_metadata_unconfirmed'
    assert row['record']['source']['author_hint']['name'] == 'Alex Example'
    assert 'parent' not in row['record']


def test_missing_changed_and_retained_text(workspace):
    db, vault = workspace
    r, raw = fixture_record()
    publish(vault, r, None)
    assert import_batch(db, 'demo', vault)['pending_evidence'] == 1
    publish(vault, r, raw)
    assert import_batch(db, 'demo', vault)['valid'] == 1
    (vault / r['raw_path']).unlink()
    assert import_batch(db, 'demo', vault)['valid'] == 1
    assert packages(db)[0]['evidence']['raw_basis'] == 'retained_text_only'
    other, changed = fixture_record('other')
    changed['text'] += ' altered'
    publish(vault, other, changed)
    assert import_batch(db, 'demo', vault)['pending_evidence'] == 1
    assert not next(x for x in packages(db) if x['item'] == other['item_id'])['eligible']


def test_revision_selection_cas_and_availability(workspace):
    db, vault = workspace
    old, raw = fixture_record(accepted=False)
    publish(vault, old, raw)
    import_batch(db, 'demo', vault)
    first = packages(db)[0]
    new, raw = fixture_record()
    publish(vault, new, raw)
    import_batch(db, 'demo', vault)
    rows = packages(db)
    assert len(rows) == 2 and not any(r['eligible'] for r in rows)
    db.select('demo', new['item_id'], new['bundle_id'], first['head_token'], 'fixture_operator')
    assert next(r for r in packages(db) if r['bundle'] == new['bundle_id'])['eligible']
    with pytest.raises(StaleError):
        db.select('demo', new['item_id'], old['bundle_id'], first['head_token'], 'fixture_operator')
    (vault / 'processed' / (new['item_id'] + '.json')).unlink()
    assert import_batch(db, 'demo', vault)['valid'] == 0
    assert len(packages(db)) == 2
    assert all(r['availability'] == 'upstream availability not confirmed' for r in packages(db))


@pytest.mark.parametrize('alter', ['gate', 'missing_row', 'duplicate_row', 'contradictory_verdict', 'triage', 'quality_type'])
def test_forged_quality_quarantines(workspace, alter):
    db, vault = workspace
    r, raw = fixture_record()
    rows = r['reviews']['claims']['claims']
    if alter == 'gate': r['quality_gate_passed'] = False
    if alter == 'missing_row': rows.clear()
    if alter == 'duplicate_row': rows.append(deepcopy(rows[0]))
    if alter == 'contradictory_verdict': rows[0]['issues'] = ['unsupported']
    if alter == 'triage': r['triage']['keep'] = False
    if alter == 'quality_type': r['quality_gate_passed'] = 1
    reseal(r)
    publish(vault, r, raw)
    assert import_batch(db, 'demo', vault)['quarantined'] == 1
    assert not packages(db)


@pytest.mark.parametrize('version,expected', [(1, 'quarantined'), (2, 'valid')])
def test_advisory_omissions_policy(workspace, version, expected):
    db, vault = workspace
    r, raw = fixture_record(version=version)
    r['reviews']['claims']['omitted_concepts'] = [{'passage_id': 1, 'description': 'Synthetic omission'}]
    reseal(r)
    publish(vault, r, raw)
    assert import_batch(db, 'demo', vault)[expected] == 1


def test_symlink_special_and_oversize(workspace, tmp_path):
    db, vault = workspace
    r, raw = fixture_record()
    p = publish(vault, r, raw)
    secret = tmp_path / 'outside.txt'
    secret.write_text('OUTSIDE_SYNTHETIC_CANARY')
    p.unlink()
    p.symlink_to(secret)
    assert import_batch(db, 'demo', vault)['quarantined'] == 1
    p.unlink()
    os.mkfifo(p)
    assert import_batch(db, 'demo', vault)['quarantined'] == 1
    p.unlink()
    p.write_bytes(b'x' * 1025)
    assert import_batch(db, 'demo', vault, max_bytes=1024)['quarantined'] == 1
    q = db.conn.execute('SELECT * FROM quarantine ORDER BY id DESC LIMIT 1').fetchone()
    assert q['full_bytes_retained'] == 0 and q['blob_digest'] is None
    assert 'OUTSIDE_SYNTHETIC_CANARY' not in str(db.canonical())


def test_descriptor_parent_and_scope_boundaries(workspace, tmp_path):
    db, vault = workspace
    r, raw = fixture_record()
    publish(vault, r, raw)
    for scope in ('../outside', '/tmp', 'processed/../../bad', 'processed\\bad', 'raw'):
        with pytest.raises(NewsmapError):
            import_batch(db, 'demo', vault, scope)
    (vault / r['raw_path']).unlink()
    (vault / 'raw').rmdir()
    (vault / 'raw').symlink_to(tmp_path, target_is_directory=True)
    assert import_batch(db, 'demo', vault)['pending_evidence'] == 1


def test_limits_quota_and_changed_batch(workspace):
    db, vault = workspace
    for label in ('a', 'b'):
        r, raw = fixture_record(label)
        publish(vault, r, raw)
    assert import_batch(db, 'demo', vault, max_items=1)['deferred'] == 1
    def mutate(stage):
        if stage == 'after_commit':
            p = vault / r['raw_path']
            p.write_bytes(p.read_bytes() + b' ')
    assert import_batch(db, 'demo', vault, fault=mutate)['status'] == 'changed'
    db.quota = 1
    other, raw = fixture_record('overquota')
    publish(vault, other, raw)
    result = import_batch(db, 'demo', vault)
    assert result['quarantined'] >= 1
    assert not any(x['item'] == other['item_id'] for x in packages(db))


@pytest.mark.parametrize('boundary,expected_packages', [('before_commit', 0), ('after_commit', 1)])
def test_process_crash_and_idempotent_retry(workspace, boundary, expected_packages):
    db, vault = workspace
    r, raw = fixture_record()
    publish(vault, r, raw)
    code = '''import os,sys
from newsmap_sidecar.storage import Database
from newsmap_sidecar.intake import import_batch
db=Database(sys.argv[1])
def crash(stage):
    if stage == sys.argv[3]: os._exit(73)
import_batch(db,'demo',sys.argv[2],fault=crash)
'''
    env = {**os.environ, 'PYTHONPATH': str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'src')}
    child = subprocess.run([sys.executable, '-c', code, str(db.path), str(vault), boundary], env=env)
    assert child.returncode == 73
    assert count(db, 'packages') == expected_packages
    assert db.conn.execute('SELECT status FROM batches').fetchone()[0] == 'running'
    assert db.doctor()['integrity'] == 'ok'
    assert import_batch(db, 'demo', vault)['valid'] == 1
    assert count(db, 'packages') == count(db, 'associations') == 1


def test_backup_offline_canonical_and_immutable_events(workspace, tmp_path):
    db, vault = workspace
    r, raw = fixture_record()
    publish(vault, r, raw)
    import_batch(db, 'demo', vault)
    p = vault / 'processed' / (r['item_id'] + '.json')
    p.write_bytes(b'bad')
    import_batch(db, 'demo', vault)
    before = db.canonical()
    target = tmp_path / 'backup' / 'restored.sqlite'
    db.backup(target)
    vault.rename(tmp_path / 'unavailable-vault')
    restored = Database(target)
    try:
        assert restored.canonical() == before
        assert packages(restored)[0]['evidence']['claims'][0]['anchors'][1]['quote'] == 'Not measured on mobile devices.'
        assert restored.doctor()['foreign_key_errors'] == 0
        with pytest.raises(sqlite3.IntegrityError):
            restored.conn.execute("UPDATE events SET actor='changed'")
    finally:
        restored.close()
