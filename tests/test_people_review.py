"""Review provenance, name disambiguation, checked origins and bounded paid work."""
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from engine.llm import ModelError, ModelReply
from graph.memory import MemoryStore
from refresher import people_backfill as backfill, people_review as review, vault
from tests.test_people_scoring import AT, record, relation
from tests.test_triage import FakeModel


def write(root,key,speaker='Alice',label='NONE',target=None):
    ex,text=record(key,speaker)
    for folder in ('processed','raw'):(root/folder).mkdir(exist_ok=True)
    (root/'processed'/f'{key}.json').write_text(ex.model_dump_json())
    (root/'raw'/f'{key}.json').write_text(json.dumps({'text':text}))
    vault.save_relation(root,relation(ex,speaker,label,target))
    return ex,text


def save(root,key,profile=None,origin=None,kind='quoted',person='p1',passages=None):
    ctx=review.detail(root,key+'-claim')
    return review.save(root,key+'-claim',{'token':ctx['token'],'action':'review',
        'review':{'kind':kind,'person':person,'passages':passages if passages is not None else [1],
                  'identity_url':profile,'origin_url':origin,'origin_checked':bool(origin)}})


def ranked(root):
    return [r for r in vault.replay(root).experts(AT) if r['importance'] is not None]


def test_review_is_reversible_and_does_not_rewrite_model_or_original(tmp_path):
    write(tmp_path,'one');before=(tmp_path/'relations/one.json').read_bytes();raw=(tmp_path/'raw/one.json').read_bytes()
    save(tmp_path,'one',kind='unknown',person=None,passages=[])
    assert ranked(tmp_path)==[]
    ctx=review.detail(tmp_path,'one-claim')
    review.save(tmp_path,'one-claim',{'token':ctx['token'],'action':'reset'})
    assert len(ranked(tmp_path))==1
    assert (tmp_path/'relations/one.json').read_bytes()==before
    assert (tmp_path/'raw/one.json').read_bytes()==raw
    assert len(list((tmp_path/'people-reviews').rglob('*.json')))==2


def test_same_name_people_can_be_split_and_profile_aliases_can_be_joined(tmp_path):
    write(tmp_path,'one');write(tmp_path,'two');write(tmp_path,'three','Alice Example')
    save(tmp_path,'one','https://university.test/alice-one/')
    save(tmp_path,'two','https://university.test/alice-two')
    save(tmp_path,'three','https://university.test/alice-one?utm_source=mail#bio')
    rows=ranked(tmp_path)
    assert len(rows)==2
    first=next(r for r in rows if r['identity_url']=='https://university.test/alice-one')
    assert set(first['claim_ids'])=={'one-claim','three-claim'}
    assert all(r['attribution_status']=='reviewed' for r in rows)
    store=vault.replay(tmp_path)
    vault.export(store,tmp_path,AT)
    names=vault.note_names(store)
    ids=[r['person_id'] for r in rows]
    assert names[ids[0]] != names[ids[1]]
    assert all((tmp_path/'wiki/people'/f'{names[i]}.md').exists() for i in ids)


def test_stale_source_or_model_cannot_reuse_human_attribution(tmp_path):
    ex,text=write(tmp_path,'one');save(tmp_path,'one')
    old=review.detail(tmp_path,'one-claim')
    (tmp_path/'raw/one.json').write_text(json.dumps({'text':text+'\nNew source material.'}))
    assert review.detail(tmp_path,'one-claim')['stale_review']
    assert ranked(tmp_path)[0]['attribution_status']=='model_inferred'
    with pytest.raises(vault.StaleToken):review.save(tmp_path,'one-claim',{'token':old['token'],'action':'reset'})


@pytest.mark.parametrize('url', ['javascript:alert(1)','https://user:secret@example.test/a','http://127.0.0.1/a','http://localhost/a','https://example.test/a b'])
def test_profile_and_origin_reject_unsafe_urls(tmp_path,url):
    write(tmp_path,'one')
    with pytest.raises(ValueError):save(tmp_path,'one',url)


def test_review_rejects_unsupported_speaker_or_missing_claim_passage(tmp_path):
    write(tmp_path,'one')
    for person,passages in [('p9',[1]),('p1',[2]),('p1',[])]:
        with pytest.raises(ValueError):save(tmp_path,'one',person=person,passages=passages)


@pytest.mark.parametrize('shared', [True,False])
def test_only_checked_distinct_primary_origins_increase_track_record(tmp_path,shared):
    write(tmp_path,'one');write(tmp_path,'two','Carol','SAME','one-claim')
    assert {r['track_record'] for r in ranked(tmp_path)}=={.5}
    save(tmp_path,'one',origin='https://primary.test/release')
    save(tmp_path,'two',origin='https://primary.test/release?utm_source=other' if shared else 'https://research.test/study')
    rows=ranked(tmp_path)
    assert {r['corroborations'] for r in rows}=={1}
    assert {r['confirmations'] for r in rows}==({0} if shared else {1})
    assert {r['track_record'] for r in rows}==({.5} if shared else {.6667})
    for r in rows:
        assert round(vault.replay(tmp_path).trust(r['person_id']),4)==r['track_record']


def test_unknown_speaker_cannot_carry_origin_or_profile(tmp_path):
    write(tmp_path,'one')
    with pytest.raises(ValueError):save(tmp_path,'one','https://profile.test/alice',kind='unknown',person=None,passages=[])


class Queue:
    def __init__(self):self.tasks=[]
    def submit(self,*args):self.tasks.append(args)


def old_records(root,n=4):
    for i in range(n):
        write(root,'doc'+str(i))
        (root/'relations'/f'doc{i}.json').unlink()


def response():
    return ModelReply({'verdicts':[{'claim':'c1','label':'NONE','target':None,'rationale':'No prior match.',
                                  'attribution':{'kind':'quoted','person':'p1','passages':[1]}}]},usage={'total_tokens':100})


def body(preview,n=1):
    return {'token':preview['token'],'item_ids':[r['item_id'] for r in preview['documents'][:n]],'confirm_paid':True}


def test_preview_spends_nothing_and_rejects_unconfirmed_or_oversized_batch(tmp_path):
    old_records(tmp_path);model=FakeModel([response()]);worker=Queue()
    with patch.object(backfill,'judge_client_from_environment',return_value=model):
        p=backfill.preview(tmp_path)
        assert len(p['documents'])==4 and p['max_documents']==3
        for b in [dict(body(p),confirm_paid=False),body(p,4),dict(body(p),item_ids=['invented'])]:
            with pytest.raises(ValueError):backfill.start(tmp_path,b,worker)
    assert model.calls==[] and worker.tasks==[]


def test_exact_selected_documents_only_are_upgraded_and_progress_persists(tmp_path):
    old_records(tmp_path);model=FakeModel([response(),response()])
    with patch.object(backfill,'judge_client_from_environment',return_value=model),ThreadPoolExecutor(max_workers=1) as worker:
        p=backfill.preview(tmp_path);request=body(p,2)
        job=backfill.start(tmp_path,request,worker)
        worker.shutdown(wait=True)
        done=backfill.job(tmp_path,job['id'])
        assert done['status']=='complete' and done['calls']==2
        assert set(done['completed'])==set(request['item_ids'])
        assert done['spent_tokens']==200
        assert len(backfill.preview(tmp_path)['documents'])==2
    assert len(model.calls)==2
    assert not (tmp_path/'relations/doc2.json').exists()


def test_preview_token_and_single_active_guard_prevent_duplicate_spend(tmp_path):
    old_records(tmp_path);model=FakeModel();worker=Queue()
    with patch.object(backfill,'judge_client_from_environment',return_value=model):
        p=backfill.preview(tmp_path)
        with pytest.raises(vault.StaleToken):backfill.start(tmp_path,dict(body(p),token='old'),worker)
        job=backfill.start(tmp_path,body(p),worker)
        with pytest.raises(vault.StaleToken):backfill.start(tmp_path,body(p),worker)
    assert len(worker.tasks)==1 and model.calls==[]
    backfill.recover(tmp_path)
    assert backfill.job(tmp_path,job['id'])['status']=='interrupted'
    assert model.calls==[]


def test_failed_batch_keeps_completed_work_and_does_not_retry(tmp_path):
    old_records(tmp_path,2);model=FakeModel([response(),ModelError('fixture failure')])
    with patch.object(backfill,'judge_client_from_environment',return_value=model),ThreadPoolExecutor(max_workers=1) as worker:
        job=backfill.start(tmp_path,body(backfill.preview(tmp_path),2),worker)
        worker.shutdown(wait=True)
    done=backfill.job(tmp_path,job['id'])
    assert done['status']=='failed' and done['completed']==['doc0'] and done['calls']==2
    assert (tmp_path/'relations/doc0.json').exists() and not (tmp_path/'relations/doc1.json').exists()


def test_changed_selected_source_stops_before_paid_call(tmp_path):
    old_records(tmp_path,1);model=FakeModel([response()]);worker=Queue()
    with patch.object(backfill,'judge_client_from_environment',return_value=model):
        job=backfill.start(tmp_path,body(backfill.preview(tmp_path)),worker)
        (tmp_path/'raw/doc0.json').write_text(json.dumps({'text':'changed source'}))
        backfill.run(tmp_path,job['id'])
    assert backfill.job(tmp_path,job['id'])['status']=='failed' and model.calls==[]


def test_checked_origin_requires_explicit_acknowledgement(tmp_path):
    write(tmp_path,'one');ctx=review.detail(tmp_path,'one-claim')
    body={'kind':'quoted','person':'p1','passages':[1],'identity_url':None,
          'origin_url':'https://primary.test/study','origin_checked':False}
    with pytest.raises(ValueError):review.save(tmp_path,'one-claim',{'token':ctx['token'],'action':'review','review':body})
    assert review.detail(tmp_path,'one-claim')['review'] is None


def test_backfill_token_budget_refuses_call_before_spending(tmp_path):
    old_records(tmp_path,1);model=FakeModel([response()]);model.max_tokens=backfill.MAX_TOKENS+1
    with patch.object(backfill,'judge_client_from_environment',return_value=model),ThreadPoolExecutor(max_workers=1) as worker:
        job=backfill.start(tmp_path,body(backfill.preview(tmp_path)),worker)
        worker.shutdown(wait=True)
    assert backfill.job(tmp_path,job['id'])['status']=='failed'
    assert model.calls==[]


def test_profile_merge_cannot_undo_user_identity_separation(tmp_path):
    write(tmp_path,'one');write(tmp_path,'two')
    save(tmp_path,'one','https://profiles.test/first');save(tmp_path,'two','https://profiles.test/second')
    store=vault.replay(tmp_path);ids=[r['person_id'] for r in ranked(tmp_path)]
    store._merge_person(ids[0],ids[1])
    assert all(store.person(i).identity_url for i in ids)
    assert len([r for r in store.experts(AT) if r['importance'] is not None])==2


def test_legacy_review_fingerprint_survives_new_optional_entity_fields(tmp_path):
    from engine.artifacts import fingerprint
    ex,text=write(tmp_path,'legacy')
    claim=ex.claims[0];rel=vault.load_relation(tmp_path,ex)
    old_entities=[{k:v for k,v in e.model_dump(mode='json').items() if k not in {'base_name','org_type'}} for e in ex.entities]
    expected=fingerprint({'claim':claim.model_dump(mode='json'),'text':text,
        'source':ex.source.model_dump(mode='json'),'entities':old_entities,
        'model_attribution':rel.verdicts[0].attribution.model_dump(mode='json')})
    assert review.context(ex,text,claim,rel)==expected
    save(tmp_path,'legacy','https://profiles.test/legacy')
    assert ranked(tmp_path)[0]['attribution_status']=='reviewed'
