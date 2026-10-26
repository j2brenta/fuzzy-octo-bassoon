import pytest
from newsmap_sidecar import review as rv
from newsmap_sidecar.automation import assess
from newsmap_sidecar.metering import Ledger, MeteredModel
from newsmap_sidecar.runner import SyntheticWire
from newsmap_sidecar.projection import build
from newsmap_sidecar.intake import packages, import_batch
from test_review import prepare
from conftest import fixture_record, reseal, publish


def test_saved_automatic_grouping_relation_human_override_and_revision(workspace, tmp_path):
    db,vault=workspace;refs=prepare(db,vault)
    ledger=Ledger(tmp_path/'budget'/'cost.sqlite','fixture');ledger.stage('first',cap_micro=1_000_000)
    wire=SyntheticWire()
    client=MeteredModel(ledger,'first','map','fixture-model',wire,context={'stage':'automation'})
    try:
        assert assess(db,client,mode='MANUAL')['model_calls']==0
        result=assess(db,client,mode='NEBIUS')
        assert len(wire.calls)==1 and result['status']=='MODEL_ASSESSED'
        view=build(db)
        assert all(i['topics'] for i in view['items']) and view['stories']
        edge=next(e for e in view['edges'] if e['kind']=='DUPLICATES')
        assert edge['active_model_assessed'] and not edge['active_reviewed']
        pid=next(e['id'] for e in rv.events(db) if e['kind']=='proposal')
        rv.review(db,pid,'reject',rv.show(db,pid)['token'],'Human rejection','fixture_operator')
        reused=assess(db,client,mode='NEBIUS')
        assert reused['reused'] and len(wire.calls)==1
        assert not rv.show(db,pid)['active_model_assessed']
        # Human topic edit wins over automatic assignment without erasing its event.
        target=rv.reference(packages(db)[0])
        rv.assign(db,'topic_assign',target,'safety','Human correction',[refs[1]],'fixture_operator')
        assert 'safety' in next(i for i in build(db)['items'] if i['id']=='item-1')['topics']
        newer,raw=fixture_record('a');newer['claims'][0]['statement']+=' Changed conditions.';reseal(newer)
        publish(vault,newer,raw);import_batch(db,'demo',vault)
        row=next(r for r in packages(db) if r['bundle']==newer['bundle_id'])
        db.select('demo',row['item'],row['bundle'],row['head_token'],'fixture_operator')
        assert rv.show(db,pid)['historical'] and not rv.show(db,pid)['active_model_assessed']
    finally:ledger.close()


def test_abstention_is_retained_without_semantic_edge(workspace,tmp_path):
    db,vault=workspace
    prepare(db,vault)
    changed,raw=fixture_record('a');changed['claims'][0]['statement']='Different mobile dataset and conditions.';reseal(changed)
    publish(vault,changed,raw);import_batch(db,'demo',vault)
    row=next(r for r in packages(db) if r['bundle']==changed['bundle_id']);db.select('demo',row['item'],row['bundle'],row['head_token'],'fixture_operator')
    ledger=Ledger(tmp_path/'budget'/'cost.sqlite','fixture');ledger.stage('first',cap_micro=1_000_000)
    try:
        result=assess(db,MeteredModel(ledger,'first','map','fixture-model',SyntheticWire(),context={'stage':'automation'}),mode='NEBIUS')
        assert result['abstentions'][0]['relation']=='not_comparable'
        assert not [e for e in build(db)['edges'] if e['kind'] in rv.RELATIONS]
    finally:ledger.close()
