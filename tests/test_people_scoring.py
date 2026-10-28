"""People importance is evidence-scoped, attributed, and reproducible without model calls."""
import json
from datetime import datetime, timezone

import pytest

from engine.llm import ModelOutputError, ModelReply
from graph import relate
from graph.attribution import from_model, validate
from graph.memory import MemoryStore, person_id
from refresher import graphview, vault
from refresher.schemas import Attribution, Extraction, Override, Relation, Span, Verdict

AT = datetime(2026, 10, 20, tzinfo=timezone.utc)


def record(key, speaker='Alice', *, author='Fixture Reporter', quality='accepted', statement='Bayesian learning improves uncertainty estimates'):
    text = f'{speaker} says: {statement}.\n' + ' '.join(f'{key}word{n}' for n in range(40))
    end = text.index('\n')
    ex = Extraction.model_validate({'stage':'processed_extraction', 'item_id':key,
        'source': {'channel':'web','source_id':key,'source_name':key,'author_hint':{'name':author} if author else None,
                   'url':f'https://example.test/{key}', 'captured_at':AT.isoformat()},
        'claims':[{'claim_id':key+'-claim','statement':statement,'kind':'fact','durability_class':'news',
                   'half_life_days':30,'confidence':0.9,'evidence':{'item_id':key,'section_id':key+'-section','start_char':0,'end_char':end}}],
        'entities':[{'entity_id':'e1','name':speaker,'kind':'person'}],
        'triage':{'keep':True,'kind':'article','durability_class':'news'},
        'quality_status':quality,'quality_gate_passed':quality=='accepted'})
    return ex, text


def relation(ex, speaker, label='NONE', target=None):
    claim = ex.claims[0]
    attr = Attribution(kind='quoted',person_name=speaker,evidence=claim.spans) if speaker else None
    v = Verdict(claim_id=claim.claim_id, label=label, target_id=target, rationale='Fixture source relation.', attribution=attr)
    return Relation(item_id=ex.item_id,relator='model',prompt_version=relate.PROMPT_VERSION,
                    candidates={claim.claim_id:[target] if target else []},verdicts=[v])


def put(store, key, speaker='Alice', *, author='Fixture Reporter', quality='accepted', label='NONE', target=None, statement=None):
    ex, text = record(key,speaker,author=author,quality=quality,**({'statement':statement} if statement else {}))
    r = relation(ex,speaker,label,target)
    store.apply(ex,text,r)
    return ex,text,r


def score(store, name, subject=None):
    return next(r for r in store.experts(AT,subject) if r['person_id']==person_id(name))


def test_quoted_speaker_is_distinct_from_writer_and_forwarder():
    store = MemoryStore()
    ex,text,r = put(store,'one')
    node = next(iter(store.claims()))
    assert node.speaker_id == person_id('Alice')
    assert store.item('one').author_id == person_id('Fixture Reporter')
    assert score(store,'Alice')['importance'] == 0.5
    assert score(store,'Fixture Reporter')['importance'] is None
    data = graphview.map_data(store,AT)
    assert data['claims'][0]['speaker'] == person_id('Alice')
    assert data['claims'][0]['attribution']['evidence'][0]['end_char'] == text.index('\n')
    assert any(p['id']==person_id('Alice') and p['statements']==['one-claim'] for p in data['people'])


def test_model_attribution_uses_known_people_and_exact_passages():
    ex,text = record('one')
    ctx=relate.ItemContext(ex.item_id,text,ex.source,ex.entities)
    attr=from_model({'kind':'quoted','person':'p1','passages':[1]},ctx,ex.claims[0])
    assert attr.person_name=='Alice' and attr.evidence==ex.claims[0].spans
    author=from_model({'kind':'author','person':'author','passages':[1]},ctx,ex.claims[0])
    assert author.person_name=='Fixture Reporter'
    for value in ({'kind':'quoted','person':'invented','passages':[1]},
                  {'kind':'quoted','person':'p1','passages':[2]},
                  {'kind':'quoted','person':'p1','passages':[999]},
                  {'kind':'author','person':'p1','passages':[1]},
                  {'kind':'unknown','person':'p1','passages':[]}):
        with pytest.raises(ModelOutputError):
            from_model(value,ctx,ex.claims[0])
    assert from_model({'kind':'unknown','person':None,'passages':[]},ctx,ex.claims[0]).person_name is None


def test_invalid_stored_attribution_cannot_award_credit():
    ex,text=record('one')
    attr=Attribution(kind='quoted',person_name='Alice',evidence=[Span(item_id='other',section_id='one-section',start_char=0,end_char=text.index('\n'))])
    with pytest.raises(ValueError):
        validate(attr,ex.source,ex.entities,ex.claims[0],text)
    r=relation(ex,'Alice').model_copy(deep=True)
    r.verdicts[0].attribution=attr
    store=MemoryStore();store.apply(ex,text,r)
    assert score(store,'Alice')['importance'] is None


def test_legacy_records_are_readable_but_not_silently_attributed():
    ex,text=record('one')
    store=MemoryStore();store.apply(ex,text,relation(ex,None))
    assert all(r['importance'] is None for r in store.experts(AT))


def test_unreviewed_statements_and_repeated_speaker_do_not_inflate_scores():
    store=MemoryStore(include_unreviewed=True)
    put(store,'one')
    put(store,'two',quality='pending',label='SAME',target='one-claim')
    assert score(store,'Alice')['source_count']==1
    put(store,'three',label='SAME',target='one-claim')
    r=score(store,'Alice')
    assert r['confirmations']==0 and r['idea_count']==1 and r['importance']==0.5
    assert any(e.type=='DUPLICATES' and e.src=='three-claim' for e in store.edges())


def test_two_journalists_quoting_the_same_person_are_not_independent():
    store=MemoryStore()
    put(store,'one',author='Reporter One')
    put(store,'two',author='Reporter Two',label='SAME',target='one-claim')
    assert score(store,'Alice')['confirmations']==0
    assert all(e.type!='SUPPORTS' for e in store.edges())


def test_different_speakers_corroborate_but_do_not_prove_independence():
    store=MemoryStore()
    put(store,'one')
    put(store,'two','Carol',label='SAME',target='one-claim')
    assert score(store,'Alice')['confirmations']==0
    assert score(store,'Alice')['corroborations']==1
    assert score(store,'Carol')['track_record']==0.5
    assert store.trust(person_id('Carol'))==0.5
    assert store.trust(person_id('Fixture Reporter'))==0.5
    assert score(store,'Fixture Reporter')['importance'] is None


def test_unresolved_disagreement_does_not_declare_both_speakers_wrong():
    store=MemoryStore()
    put(store,'one')
    put(store,'two','Carol',statement='Bayesian learning worsens uncertainty estimates',label='CONFLICTS',target='one-claim')
    for name in ('Alice','Carol'):
        r=score(store,name)
        assert r['disagreements']==1 and r['track_record']==0.5
        assert store.trust(person_id(name))==0.5


def test_profile_is_topic_specific_and_missing_profile_is_neutral():
    store=MemoryStore()
    put(store,'one')
    put(store,'two','Carol',statement='Garden soil needs regular watering')
    alice=store.person(person_id('Alice'))
    alice.enrichment_status='enriched'
    alice.domains=[{'value':'Bayesian learning','url':'https://university.test/alice'}]
    from graph.proposals import make, ProposalState
    for title,cid in [('Bayesian learning','one-claim'),('Garden soil','two-claim')]:
        p=make(store,'topic',{'title':title,'claims':[cid]},'User-approved fixture topic',AT)
        store.apply_proposal(ProposalState(p,'accepted','fixture-token'))
    bayes=next(t for t in store.topics() if t.title=='Bayesian learning')
    garden=next(t for t in store.topics() if t.title=='Garden soil')
    r=score(store,'Alice',bayes.topic_id)
    assert r['professional_relevance']>0 and r['profile_sources']
    assert score(store,'Alice',garden.topic_id)['importance'] is None
    assert score(store,'Carol',garden.topic_id)['professional_relevance'] is None
    assert score(store,'Alice')['professional_relevance'] is None  # no global prestige score
    with pytest.raises(KeyError):store.experts(AT,'invented-topic')


def test_overrides_and_votes_remain_visible():
    store=MemoryStore();put(store,'one')
    store.apply_override(Override(target='person',target_id=person_id('Alice'),trust=0.9))
    assert score(store,'Alice')['track_record']==0.9
    assert 'Trust set by you.' in score(store,'Alice')['rationale']


def test_replay_and_export_preserve_attribution_and_scores(tmp_path):
    expected=MemoryStore()
    rows=[put(expected,'one'),put(expected,'two','Carol',label='SAME',target='one-claim')]
    for ex,text,r in rows:
        (tmp_path/'processed').mkdir(exist_ok=True);(tmp_path/'raw').mkdir(exist_ok=True)
        (tmp_path/'processed'/f'{ex.item_id}.json').write_text(ex.model_dump_json())
        (tmp_path/'raw'/f'{ex.item_id}.json').write_text(json.dumps({'text':text}))
        vault.save_relation(tmp_path,r)
    loaded=vault.replay(tmp_path)
    assert loaded.experts(AT)==expected.experts(AT)
    vault.export(loaded,tmp_path,AT)
    note=(tmp_path/'wiki/people/Alice.md').read_text()
    assert '## Importance in this collection' in note and '## Statements' in note
    assert '1 corroborating speakers; 0 distinct primary origins checked by you' in note
    reverse=MemoryStore()
    for ex,text,r in reversed(rows):reverse.apply(ex,text,r)
    assert reverse.experts(AT)==expected.experts(AT)


def test_model_speaker_failure_keeps_the_relation_and_never_guesses_author():
    ex,text=record('one')
    class Model:
        model_name='test'
        def complete_json(self,prompt,content):
            return ModelReply({'verdicts':[{'claim':'c1','label':'NONE','subject':'world',
                'attribution':{'kind':'quoted','person':'not-a-person','passages':[1]}}]})
    r=relate.judge(ex,text,[],relate.ModelRelator(Model()))
    assert r.verdicts[0].attribution.kind=='unknown'
    store=MemoryStore();store.apply(ex,text,r)
    assert score(store,'Fixture Reporter')['importance'] is None


def test_unreviewed_update_cannot_remove_accepted_speaker_credit():
    store=MemoryStore(include_unreviewed=True)
    put(store,'one')
    put(store,'two','Carol',quality='pending',statement='Bayesian learning no longer improves uncertainty estimates',label='UPDATES',target='one-claim')
    assert score(store,'Alice')['importance']==0.5
    assert score(store,'Carol')['importance'] is None


def test_people_scores_respect_snapshot_time():
    from datetime import timedelta
    store=MemoryStore();put(store,'one')
    assert all(r['importance'] is None for r in store.experts(AT-timedelta(days=1)))


def test_old_model_relations_upgrade_once_to_grounded_speakers(tmp_path):
    from refresher.place import relate_pending
    ex,text=record('one')
    (tmp_path/'processed').mkdir();(tmp_path/'raw').mkdir()
    (tmp_path/'processed/one.json').write_text(ex.model_dump_json())
    (tmp_path/'raw/one.json').write_text(json.dumps({'text':text}))
    old=relation(ex,None).model_copy(update={'prompt_version':'relate-v2'})
    vault.save_relation(tmp_path,old)
    class Model:
        model_name='fixture'
        calls=0
        def complete_json(self,prompt,content):
            self.calls+=1
            return ModelReply({'verdicts':[{'claim':'c1','subject':'world','label':'NONE',
                 'attribution':{'kind':'quoted','person':'p1','passages':[1]}}]})
    model=Model()
    assert relate_pending(tmp_path,relate.ModelRelator(model)).judged==1
    assert relate_pending(tmp_path,relate.ModelRelator(model)).judged==0
    assert model.calls==1
    assert score(vault.replay(tmp_path),'Alice')['importance']==0.5


def test_dossier_uses_scoped_speaker_importance_not_writer_involvement():
    from refresher import dossier
    store=MemoryStore()
    put(store,'one')
    put(store,'two','Carol',label='SAME',target='one-claim')
    ideas=[n.idea_id for n in store.claims() if n.idea_id]
    scope=dossier.Scope('query','Bayesian learning','Bayesian learning',sorted(set(ideas)),{i:1.0 for i in ideas})
    d=dossier.build(store,scope,AT)
    first=d['nearby']['people'][0]
    assert first['id'] in {person_id('Alice'),person_id('Carol')}
    assert first['importance']>0 and first['stated']==1
    assert first['score']['subject_title']=='Bayesian learning'
    assert first['wrote']==0  # a quoted speaker need not have written the article
    assert person_id('Fixture Reporter') not in {p['id'] for p in d['nearby']['people']}


@pytest.mark.parametrize('topology', ['star', 'chain'])
@pytest.mark.parametrize('names', [('Alice', 'Bob', 'Carol'), ('Dee', 'Eve', 'Frank')])
def test_agreement_group_credit_does_not_depend_on_edge_topology(topology, names):
    store = MemoryStore()
    put(store, 'one', names[0])
    put(store, 'two', names[1], label='SAME', target='one-claim')
    put(store, 'three', names[2], label='SAME', target=('one' if topology == 'star' else 'two')+'-claim')
    rows = [score(store, name) for name in names]
    assert {row['corroborations'] for row in rows} == {2}
    assert len({row['track_record'] for row in rows}) == 1
    assert {row['rank'] for row in rows} == {1}
    assert all(not row['independence_verified'] for row in rows)


@pytest.mark.parametrize('title,domain', [
    ('AI', 'Artificial intelligence'), ('Artificial intelligence', 'AI'),
    ('ML', 'Machine learning'), ('NLP', 'Natural language processing'),
    ('Unrelated topic', 'Artificial intelligence'), ('Learning', 'Machine learning'),
])
def test_profile_evidence_never_penalizes_unknown_or_alias_match(title, domain):
    from refresher.people_scoring import score_people
    store = MemoryStore(); put(store, 'one')
    ids = {n.idea_id for n in store.claims()}
    before = next(r for r in score_people(store, AT, idea_ids=ids, subject_title=title)
                  if r['person_id'] == person_id('Alice'))
    person = store.person(person_id('Alice'))
    person.enrichment_status = 'enriched'
    person.domains = [{'value':domain, 'url':'https://university.test/alice'}]
    after = next(r for r in score_people(store, AT, idea_ids=ids, subject_title=title)
                 if r['person_id'] == person_id('Alice'))
    assert after['importance'] >= before['importance']
    if title == 'Unrelated topic':
        assert after['professional_relevance'] is None
    else:
        assert after['professional_relevance'] >= .5


def test_dossier_includes_sourced_profile_subject_without_speaker_credit():
    from refresher import dossier
    store = MemoryStore()
    ex,text = record('about','Karpathy')
    about = ex.claims[0].model_copy(update={'claim_id':'about-profile', 'statement':'Karpathy developed autoresearch.'})
    ex = ex.model_copy(update={'claims':[ex.claims[0],about]})
    rel = Relation(item_id=ex.item_id,relator='model',prompt_version=relate.PROMPT_VERSION,
                   candidates={ex.claims[0].claim_id:[],about.claim_id:[]},verdicts=[
                       Verdict(claim_id=ex.claims[0].claim_id,attribution=Attribution(kind='unknown')),
                       Verdict(claim_id=about.claim_id,subject='person',subject_name='Karpathy',attribution=Attribution(kind='unknown'))])
    store.apply(ex,text,rel)
    ideas=[n.idea_id for n in store.claims() if n.idea_id]
    scope=dossier.Scope('query','Research','Research',ideas,{i:1.0 for i in ideas})
    people=dossier.build(store,scope,AT)['nearby']['people']
    karpathy=next(p for p in people if p['id']==person_id('Karpathy'))
    assert karpathy['about']==1 and karpathy['stated']==0 and karpathy['importance'] is None
    assert person_id('Fixture Reporter') not in {p['id'] for p in people}


def test_attributed_and_profile_names_share_mains_normalized_person_identity():
    raw,base='Кости Крестникова','Костя Крестников'
    ex,text=record('normalized',raw)
    ex=ex.model_copy(update={'entities':[ex.entities[0].model_copy(update={'base_name':base})]})
    profile=ex.claims[0].model_copy(update={'claim_id':'normalized-profile'})
    ex=ex.model_copy(update={'claims':[ex.claims[0],profile]})
    rel=relation(ex,raw)
    rel=rel.model_copy(update={'candidates':{ex.claims[0].claim_id:[],profile.claim_id:[]},
        'verdicts':[*rel.verdicts,Verdict(claim_id=profile.claim_id,subject='person',subject_name=raw)]})
    store=MemoryStore();store.apply(ex,text,rel)
    person=store.person(person_id(base))
    assert person.statements=={ex.claims[0].claim_id}
    assert person.facts=={profile.claim_id}
    assert person_id(raw) not in {p.person_id for p in store.people()}
    # Provenance still points to the written form, not a fabricated source span.
    attributed=next(n for n in store.claims() if n.claim.claim_id==ex.claims[0].claim_id)
    assert attributed.attribution.person_name==raw
