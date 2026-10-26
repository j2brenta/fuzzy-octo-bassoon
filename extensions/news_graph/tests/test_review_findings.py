from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from newsmap_sidecar import adapter
from newsmap_sidecar.common import NewsmapError, StaleError, local_storage
from newsmap_sidecar.intake import import_batch, packages
from newsmap_sidecar.storage import Database
from conftest import fixture_record, publish, reseal


def test_hardlink_never_retains_outside_bytes(workspace, tmp_path):
    db, vault = workspace
    r, raw = fixture_record()
    p = publish(vault, r, raw)
    outside = tmp_path / 'outside.json'
    outside.write_bytes(b'HARDLINK_OUTSIDE_CANARY')
    p.unlink(); os.link(outside, p)
    assert import_batch(db, 'demo', vault)['quarantined'] == 1
    assert not any(b'HARDLINK_OUTSIDE_CANARY' in bytes(x[0]) for x in db.conn.execute('SELECT data FROM blobs'))
    q = db.conn.execute('SELECT * FROM quarantine').fetchone()
    assert q['full_bytes_retained'] == 0


def test_namespace_relocation_rechecked_inside_item_transaction(workspace, tmp_path, monkeypatch):
    db, vault = workspace
    r, raw = fixture_record()
    publish(vault, r, raw)
    other = tmp_path / 'new-vault'; other.mkdir()
    writer = Database(db.path)
    original = adapter.evidence
    def relocate(*args, **kwargs):
        writer.register('demo', other, relocate=True, actor='fixture_operator')
        return original(*args, **kwargs)
    monkeypatch.setattr(adapter, 'evidence', relocate)
    try:
        with pytest.raises(StaleError): import_batch(db, 'demo', vault)
        assert not packages(db)
        batch = db.conn.execute('SELECT * FROM batches').fetchone()
        assert batch['status'] == 'running'
        assert json.loads(batch['detail'])['source_root'] == str(vault)
    finally:
        writer.close()


def test_ambiguous_stacked_mount_fails_closed(tmp_path, monkeypatch):
    original = Path.read_text
    def mountinfo(path, *args, **kwargs):
        if str(path) == '/proc/self/mountinfo':
            return '30 1 8:1 / / rw - xfs /dev/sda rw\n31 1 0:9 / / rw - nfs remote:/ rw\n'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', mountinfo)
    with pytest.raises(NewsmapError, match='stacked'): local_storage(tmp_path / 'test.sqlite')


def test_optional_link_item_must_match(workspace):
    db, vault = workspace
    r, raw = fixture_record(text='https://example.invalid/reference\nSynthetic qualifier')
    r['links'] = [{'url': 'https://example.invalid/reference', 'kind': 'url', 'item_id': 'f' * 64,
                   'section_id': r['sections'][0]['section_id'], 'start_char': 0,
                   'end_char': len('https://example.invalid/reference')}]
    reseal(r); publish(vault, r, raw)
    assert import_batch(db, 'demo', vault)['pending_evidence'] == 1


@pytest.mark.parametrize('kind,review_status,quality_status', [('concept', 'accepted', 'accepted'),
                                                            ('person', 'needs_revision', 'needs_revision')])
def test_current_entity_policy_matches_producer_convention(workspace, kind, review_status, quality_status):
    db, vault = workspace
    r, raw = fixture_record()
    span = {**r['claims'][0]['evidence'][0], 'passage_id': 1,
            'start_char': raw['text'].index('Aurora'), 'end_char': raw['text'].index('Aurora') + len('Aurora')}
    r['entities'] = [{'entity_id': 'example', 'name': 'Aurora', 'kind': kind, 'mentions': [span]}]
    r['reviews']['entities'].update(status=review_status, entities=[{
        'entity_id': 'example', 'verdict': 'needs_revision', 'reason': 'Synthetic review finding'}])
    r['quality_status'], r['quality_gate_passed'] = quality_status, quality_status == 'accepted'
    reseal(r); publish(vault, r, raw)
    assert import_batch(db, 'demo', vault)['valid'] == 1
    assert packages(db)[0]['eligible'] == (kind == 'concept')


def test_historical_text_reuse_reports_current_raw_mismatch(workspace):
    db, vault = workspace
    r, raw = fixture_record()
    publish(vault, r, raw); import_batch(db, 'demo', vault)
    (vault / r['raw_path']).write_bytes(b'{broken current raw')
    assert import_batch(db, 'demo', vault)['valid'] == 1
    row = packages(db)[0]
    assert row['evidence']['raw_basis'] == 'retained_text_only'
    assert 'current raw unavailable/mismatched' in row['evidence_observation_reason']
    assert len(row['evidence']['claims'][0]['anchors']) == 2


def test_unexpected_filename_never_read(workspace):
    db, vault = workspace
    (vault / 'processed').mkdir()
    (vault / 'processed' / '.env.json').write_bytes(b'UNEXPECTED_FILE_CANARY')
    assert import_batch(db, 'demo', vault)['quarantined'] == 1
    assert not any(b'UNEXPECTED_FILE_CANARY' in bytes(x[0]) for x in db.conn.execute('SELECT data FROM blobs'))


@pytest.mark.parametrize('field,value', [('kind', 'invented'), ('confidence', True), ('confidence', 1.1),
                                        ('durability_class', 'forever'), ('half_life_days', 7), ('statement', 'x' * 1201)])
def test_malformed_claim_contract_quarantined(workspace, field, value):
    db, vault = workspace
    r, raw = fixture_record()
    r['claims'][0][field] = value
    reseal(r); publish(vault, r, raw)
    assert import_batch(db, 'demo', vault)['quarantined'] == 1
    assert not packages(db)


def test_entity_passage_id_must_contain_quote(workspace):
    db, vault = workspace
    r, raw = fixture_record()
    start = raw['text'].index('Aurora')
    span = {**r['claims'][0]['evidence'][0], 'start_char': start, 'end_char': start + 6, 'passage_id': 2}
    r['entities'] = [{'entity_id': 'example', 'name': 'Aurora', 'kind': 'concept', 'mentions': [span]}]
    r['reviews']['entities']['entities'] = [{'entity_id': 'example', 'verdict': 'supported', 'reason': 'Synthetic'}]
    reseal(r); publish(vault, r, raw)
    assert import_batch(db, 'demo', vault)['pending_evidence'] == 1


def test_review_missing_anchors_cannot_approve_and_gate_changes_token(workspace, monkeypatch):
    from newsmap_sidecar import review as rv
    from test_review import prepare, proposal
    db, vault = workspace
    refs = prepare(db, vault)
    rows = deepcopy(packages(db))
    monkeypatch.setattr(rv, 'packages', lambda db: rows)
    rows[0]['evidence']['claims'] = []
    pid = proposal(db, refs)
    before = rv.show(db, pid)
    assert any('exact retained evidence unavailable' in reason for reason in before['blocked'])
    with pytest.raises(NewsmapError, match='approval blocked'):
        rv.review(db, pid, 'accept', before['token'], 'Synthetic attempt', 'fixture_operator')
    rows[0]['record']['quality_gate_passed'] = False
    assert rv.show(db, pid)['token'] != before['token']
