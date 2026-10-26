from copy import deepcopy
import sqlite3

import pytest

from newsmap_sidecar.common import NewsmapError, StaleError
from newsmap_sidecar.intake import import_batch, packages
from newsmap_sidecar.storage import Database
from newsmap_sidecar import review as rv
from conftest import fixture_record, reseal, publish


ACTOR = 'fixture_operator'


def context():
    return dict(zip(rv.CONTEXT_FIELDS, ('Aurora', '1', 'score', 'synthetic', 'points',
                                      'desktop only', 'fixture date', 'independence unknown')))


def prepare(db, vault, pending=False):
    for label, accepted in [('a', not pending), ('b', True)]:
        r, raw = fixture_record(label, accepted=accepted)
        publish(vault, r, raw)
    import_batch(db, 'demo', vault)
    rows = sorted(packages(db), key=lambda r: r['record']['source']['source_id'])
    refs = [rv.reference(r, r['record']['claims'][0]['claim_id']) for r in rows]
    return refs


def proposal(db, refs, premises=None):
    return rv.propose(db, *refs, 'CONTRADICTS', context(), 'Synthetic interpretation', refs, premises or [], ACTOR)


def approve(db, pid):
    return rv.review(db, pid, 'accept', rv.show(db, pid)['token'], 'Synthetic reviewed example', ACTOR)


def test_pending_repaired_selected_needs_new_proposal(workspace):
    db, vault = workspace
    refs = prepare(db, vault, pending=True)
    p1 = proposal(db, refs)
    before = rv.show(db, p1)
    assert before['blocked'] and not before['active_reviewed']
    with pytest.raises(NewsmapError): approve(db, p1)
    repaired, raw = fixture_record('a')
    publish(vault, repaired, raw)
    import_batch(db, 'demo', vault)
    assert rv.show(db, p1)['token'] == before['token']  # Import alone did not replace a head.
    a = next(r for r in packages(db) if r['item'] == repaired['item_id'])
    db.select('demo', repaired['item_id'], repaired['bundle_id'], a['head_token'], ACTOR)
    with pytest.raises(StaleError):
        rv.review(db, p1, 'accept', before['token'], 'Stale review attempt', ACTOR)
    state = rv.show(db, p1)
    assert state['blocked'] and state['historical']
    refs[0] = {**refs[0], 'bundle': repaired['bundle_id']}
    p2 = proposal(db, refs)
    assert rv.show(db, p2)['review_status'] == 'proposed'
    approve(db, p2)
    assert rv.show(db, p2)['active_reviewed']
    assert not rv.show(db, p1)['active_reviewed']


def test_subject_premise_revocation_preserves_review_and_stales_token(workspace):
    db, vault = workspace
    refs = prepare(db, vault)
    premise = rv.assign(db, 'subject_assign', refs[0], {'subject': 'Aurora', 'version': '1'},
                        'Synthetic subject premise', [refs[0]], ACTOR)
    pid = proposal(db, refs, [premise])
    approve(db, pid)
    old = rv.show(db, pid)
    assert old['active_reviewed']
    rv.premise_revoke(db, premise, 'Synthetic premise withdrawn', ACTOR)
    current = rv.show(db, pid)
    assert current['needs_recheck'] and not current['active_reviewed']
    assert current['reviews'] == old['reviews']
    with pytest.raises(StaleError):
        rv.review(db, pid, 'revoke', old['token'], 'Stale revoke', ACTOR)
    with pytest.raises(NewsmapError): approve(db, pid)


def test_same_name_roles_and_identity_premise(workspace):
    db, vault = workspace
    refs = prepare(db, vault)
    ids = rv.identities(packages(db))
    authors = [i for i in ids if i['role'] == 'author_hint']
    assert len(authors) == 2 and authors[0]['id'] != authors[1]['id']
    assert authors[0]['hint']['name'] == authors[1]['hint']['name']
    link = rv.identity_link(db, authors[0]['id'], authors[1]['id'], 'Synthetic identity decision', refs, ACTOR)
    pid = proposal(db, refs, [link])
    approve(db, pid)
    rv.premise_revoke(db, link, 'Identity uncertain', ACTOR)
    assert rv.show(db, pid)['needs_recheck']
    assert rv.identities(packages(db)) == ids  # Linking never physically merges identities.


def test_revision_history_no_retarget_no_duplicate_approval(workspace):
    db, vault = workspace
    refs = prepare(db, vault)
    pid = proposal(db, refs)
    token = rv.show(db, pid)['token']
    approve(db, pid)
    with pytest.raises(StaleError):
        rv.review(db, pid, 'accept', token, 'Duplicate approval attempt', ACTOR)
    before = rv.show(db, pid)
    newer, raw = fixture_record('a')
    newer['claims'][0]['statement'] += ' Additional qualifier.'
    reseal(newer)
    publish(vault, newer, raw)
    import_batch(db, 'demo', vault)
    head = next(r['head_token'] for r in packages(db) if r['item'] == newer['item_id'])
    db.select('demo', newer['item_id'], newer['bundle_id'], head, ACTOR)
    after = rv.show(db, pid)
    assert after['historical'] and not after['active_reviewed']
    assert after['reviews'] == before['reviews']
    assert after['proposal']['payload']['left']['ref']['bundle'] == refs[0]['bundle']


def test_unrelated_editorial_changes_and_reserialization_do_not_stale(workspace):
    db, vault = workspace
    refs = prepare(db, vault)
    pid = proposal(db, refs)
    token = rv.show(db, pid)['token']
    story = rv.story_create(db, 'Synthetic story', 'evaluation', 'Fixture organization', refs, ACTOR)
    rv.assign(db, 'story_assign', refs[0], story, 'Fixture membership', refs, ACTOR)
    rv.assign(db, 'subject_assign', refs[1], {'subject': 'Unrelated subject', 'version': 'unknown'},
              'Unused premise', [refs[1]], ACTOR)
    r, raw = fixture_record('a')
    r['created_at'] = '2026-11-01T00:00:00+00:00'
    publish(vault, r, raw, ensure_ascii=True, indent=None)
    import_batch(db, 'demo', vault)
    assert rv.show(db, pid)['token'] == token
    rv.review(db, pid, 'accept', token, 'Synthetic approval', ACTOR)
    assert rv.show(db, pid)['active_reviewed']


@pytest.mark.parametrize('action', ['reject', 'defer', 'revoke'])
def test_review_actions_are_append_only(workspace, action):
    db, vault = workspace
    refs = prepare(db, vault)
    pid = proposal(db, refs)
    approve(db, pid)
    rv.review(db, pid, action, rv.show(db, pid)['token'], 'Synthetic review action', ACTOR)
    state = rv.show(db, pid)
    assert state['review_status'] == action and len(state['reviews']) == 2
    assert not state['active_reviewed']
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute('DELETE FROM events WHERE id=?', (state['reviews'][0]['id'],))


def test_missing_evidence_binding_requires_new_proposal_after_repair(workspace):
    db, vault = workspace
    refs = prepare(db, vault)
    # A third item first arrives without raw evidence.
    r, raw = fixture_record('missing')
    publish(vault, r, None)
    import_batch(db, 'demo', vault)
    missing = next(row for row in packages(db) if row['item'] == r['item_id'])
    pair = [rv.reference(missing, r['claims'][0]['claim_id']), refs[1]]
    pid = proposal(db, pair)
    publish(vault, r, raw)
    import_batch(db, 'demo', vault)
    assert rv.show(db, pid)['blocked']
    with pytest.raises(NewsmapError): approve(db, pid)
    new = proposal(db, pair)
    approve(db, new)
    assert rv.show(db, new)['active_reviewed']


def test_review_backup_without_vault_and_no_automatic_semantics(workspace, tmp_path, monkeypatch):
    import socket
    def no_network(*args, **kwargs):
        raise AssertionError('Offline restoration must never connect to network')
    monkeypatch.setattr(socket.socket, 'connect', no_network)
    db, vault = workspace
    refs = prepare(db, vault)
    assert not rv.snapshot(db)['relations']
    pid = proposal(db, refs)
    approve(db, pid)
    before = rv.snapshot(db)
    dest = tmp_path / 'backup' / 'review.sqlite'
    db.backup(dest)
    vault.rename(tmp_path / 'offline-input')
    restored = Database(dest)
    try:
        assert rv.snapshot(restored) == before
        assert rv.show(restored, pid)['active_reviewed']
    finally:
        restored.close()


def test_rebuild_replays_selections_and_preserves_review_tokens(workspace):
    db, vault = workspace
    refs = prepare(db, vault)
    pid = proposal(db, refs)
    approve(db, pid)
    before, canonical = rv.snapshot(db), db.canonical()
    with db.transaction():
        db.conn.execute('DELETE FROM heads')
    assert not rv.show(db, pid)['active_reviewed']
    result = db.rebuild_heads()
    assert result['selection_events_replayed'] == 2
    assert rv.snapshot(db) == before and db.canonical() == canonical
