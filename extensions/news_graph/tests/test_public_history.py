"""Fixed public grants describe disclosed history, independently of private state."""
import pytest
from newsmap_sidecar import review as rv
from newsmap_sidecar.common import NewsmapError
from newsmap_sidecar.projection import build
from newsmap_sidecar.intake import packages, import_batch
from conftest import fixture_record, publish, reseal
from test_review import prepare, proposal, approve
from test_export import grant


def stable(db, allow):
    result = build(db, allow); result.pop('snapshot_at'); return result


def test_hidden_history_cannot_rewrite_public_snapshot(workspace):
    db, vault = workspace
    refs = prepare(db, vault)
    premise = rv.assign(db, 'subject_assign', refs[0], {'subject': 'Aurora', 'version': '1'},
                        'Disclosed premise', refs, 'fixture_operator')
    pid = proposal(db, refs, [premise]); rid = approve(db, pid)
    allow = {'actor': 'fixture_operator', 'items': [grant(r) for r in packages(db)],
             'decisions': [premise, pid, rid]}
    before = stable(db, allow)
    revoked = rv.review(db, pid, 'revoke', rv.show(db, pid)['token'], 'HIDDEN_REVOKE', 'private_operator')
    assert not rv.show(db, pid)['active_reviewed']
    assert stable(db, allow) == before
    rv.premise_revoke(db, premise, 'HIDDEN_PREMISE', 'private_operator')
    assert stable(db, allow) == before
    changed, raw = fixture_record('a'); changed['claims'][0]['statement'] += ' Changed'
    reseal(changed); publish(vault, changed, raw); import_batch(db, 'demo', vault)
    db.select('demo', changed['item_id'], changed['bundle_id'], db.head('demo', changed['item_id'])['bundle'] + ':' + str(db.head('demo', changed['item_id'])['event']), 'private_operator')
    assert stable(db, allow) == before
    assert before['edges'][0]['current_applicability'] == 'not_disclosed'
    assert before['edges'][0]['review_record'] == 'accept'
    assert before['items'][0]['selected'] is None
    amended = stable(db, {**allow, 'decisions': [*allow['decisions'], revoked]})
    assert amended['edges'][0]['review_record'] == 'revoke'
    assert amended != before


def test_authorized_proposal_with_hidden_review_is_proposed_history(workspace):
    db, vault = workspace
    refs = prepare(db, vault); pid = proposal(db, refs)
    allow = {'actor': 'fixture_operator', 'items': [grant(r) for r in packages(db)], 'decisions': [pid]}
    before = stable(db, allow)
    approve(db, pid)
    assert stable(db, allow) == before
    assert before['edges'][0]['review_record'] == 'proposed'


def test_explicit_required_decision_dependency_fails_clearly(workspace):
    db, vault = workspace
    refs = prepare(db, vault); pid = proposal(db, refs); rid = approve(db, pid)
    allow = {'actor': 'fixture_operator', 'items': [grant(r) for r in packages(db)], 'decisions': [rid]}
    with pytest.raises(NewsmapError, match='disclosed dependency'):
        build(db, allow)


def test_identity_link_projection_retains_both_inspectable_endpoints(workspace):
    db,vault=workspace;refs=prepare(db,vault)
    ids=[i for i in rv.identities(packages(db)) if i['role']=='author_hint']
    rv.identity_link(db,ids[0]['id'],ids[1]['id'],'Fixture identity hypothesis',refs,'fixture_operator')
    view=build(db); edge=next(e for e in view['edges'] if e['kind']=='IDENTITY_LINK')
    known={i['id'] for i in view['identities']}
    assert edge['source'] in known and edge['target'] in known
    assert edge['source'] != edge['target'] and not edge['active_reviewed']
