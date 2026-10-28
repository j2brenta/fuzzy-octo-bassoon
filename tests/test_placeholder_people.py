"""Role labels cannot become people; source-identified authors stay separate from quoted speakers."""
from types import SimpleNamespace

import pytest

from engine.llm import ModelOutputError
from graph.attribution import from_model
from graph.memory import MemoryStore, person_id
from refresher import graphview, people_review, vault
from refresher.schemas import Attribution, Verdict
from tests.test_people_scoring import AT, record, relation


@pytest.mark.parametrize('name',['Writer',' writer ','Author','The author','Unknown author','Anonymous'])
def test_placeholder_author_does_not_create_person_or_speaker_credit(name):
    ex,text=record('one',author=name)
    rel=relation(ex,None)
    rel=rel.model_copy(update={'verdicts':[Verdict(claim_id=ex.claims[0].claim_id,
        attribution=Attribution(kind='author',person_name=name,evidence=ex.claims[0].spans))]})
    store=MemoryStore();store.apply(ex,text,rel)
    assert store.item('one').author_id is None
    assert all(p.name != name for p in store.people())
    assert next(iter(store.claims())).speaker_id is None
    data=graphview.map_data(store,AT)
    assert all(p['name'] != name for p in data['people'])
    assert not any(e.type=='AUTHORED_BY' for e in store.edges())


def test_real_source_author_is_used_without_guessing_a_topic_author():
    ex,text=record('one',author='Jane Example')
    item=SimpleNamespace(source=ex.source,entities=ex.entities,text=text)
    attr=from_model({'kind':'author','person':'author','passages':[1]},item,ex.claims[0])
    rel=relation(ex,None).model_copy(update={'verdicts':[Verdict(claim_id=ex.claims[0].claim_id,attribution=attr)]})
    store=MemoryStore();store.apply(ex,text,rel)
    assert store.item('one').author_id==person_id('Jane Example')
    assert next(iter(store.claims())).speaker_id==person_id('Jane Example')
    assert store.person(person_id('Jane Example')).authored=={'one'}


def test_placeholder_entity_and_profile_subject_do_not_create_person():
    ex,text=record('one','Writer',author=None)
    rel=relation(ex,None).model_copy(update={'verdicts':[Verdict(claim_id=ex.claims[0].claim_id,
        subject='person',subject_name='Writer')]})
    store=MemoryStore();store.apply(ex,text,rel)
    assert list(store.people())==[]
    assert next(iter(store.claims())).idea_id  # preserve the claim as world knowledge
    assert len(store.candidate_pool('another-item'))==1


def test_model_and_manual_review_cannot_attribute_to_placeholder(tmp_path):
    import json
    ex,text=record('one','Writer',author='Writer')
    for folder in ('processed','raw'):(tmp_path/folder).mkdir()
    (tmp_path/'processed/one.json').write_text(ex.model_dump_json())
    (tmp_path/'raw/one.json').write_text(json.dumps({'text':text}))
    vault.save_relation(tmp_path,relation(ex,None))
    detail=people_review.detail(tmp_path,'one-claim')
    assert detail['people']==[]
    item=SimpleNamespace(source=ex.source,entities=ex.entities,text=text)
    for kind,person in [('author','author'),('quoted','p1')]:
        with pytest.raises(ModelOutputError):from_model({'kind':kind,'person':person,'passages':[1]},item,ex.claims[0])
