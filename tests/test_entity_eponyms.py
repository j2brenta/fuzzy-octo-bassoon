"""An eponym alone is not a person participating in the article."""
import pytest
from engine.entities import validate_entities, validate_entity_review
from tests.test_entities import entity_review

@pytest.mark.parametrize('text,name', [
    ('Объяснение третьего закона Ньютона.', 'Ньютона'),
    ('Используем теорему Байеса.', 'Байеса'),
    ("Apply Newton's third law.", 'Newton'),
    ('Use Bayes’ theorem.', 'Bayes'),
    ('The law of Newton applies.', 'Newton'),
])
def test_reviewer_cannot_approve_eponym_only_person(text, name):
    entities = validate_entities({'entities':[{'name':name,'kind':'person','evidence_passage_ids':[1]}]}, text, 'item', 'section')
    review = validate_entity_review(entity_review(entities), entities, text, 'item', 'section')
    assert review['status'] == 'needs_revision'
    assert review['entities'][0]['verdict'] == 'needs_revision'
    assert 'eponymous' in review['entities'][0]['reason']

@pytest.mark.parametrize('text,name', [
    ("Newton formulated his third law. Newton's law applies.", 'Newton'),
    ('Ньютон изучал движение. Закон Ньютона описывает силы.', 'Ньютон'),
    ("Bayes described a method of inference.", 'Bayes'),
])
def test_separate_person_discussion_is_retained(text, name):
    entities = validate_entities({'entities':[{'name':name,'kind':'person','evidence_passage_ids':[1]}]}, text, 'item', 'section')
    assert validate_entity_review(entity_review(entities), entities, text, 'item', 'section')['status'] == 'accepted'


def test_a_legacy_eponym_is_no_person_and_spends_no_enrichment_lookup(tmp_path):
    from graph.memory import MemoryStore, person_id
    from refresher.consolidate import pick_people
    from tests.test_people_scoring import record, relation
    ex, _ = record('eponym', speaker='Ньютона', author=None)
    store = MemoryStore()
    store.apply(ex, 'Объяснение третьего закона Ньютона.', relation(ex, None))  # a record kept from before the rule
    assert person_id('Ньютона') not in {p.person_id for p in store.people()}
    assert pick_people(tmp_path, store, 10) == []


def test_a_claim_about_a_name_inside_a_law_is_about_the_world():
    from graph.memory import MemoryStore
    from tests.test_people_scoring import record, relation
    ex, _ = record('eponym', speaker='Ньютона', author=None)
    rel = relation(ex, None)
    rel = rel.model_copy(update={'verdicts': [rel.verdicts[0].model_copy(update={'subject': 'person',
                                                                                 'subject_name': 'Ньютона'})]})
    store = MemoryStore()
    store.apply(ex, 'Объяснение третьего закона Ньютона.', rel)
    [claim] = [c for c in store.claims() if c.item_id == ex.item_id]
    assert claim.subject_id is None


def test_extraction_leaves_out_a_name_only_inside_a_law(tmp_path):
    from engine.entities import extract_entities
    from engine.llm import ModelReply
    from store.raw import capture_text
    from tests.test_triage import FakeModel
    raw = capture_text('Костя Крестников объясняет третий закон Ньютона.', tmp_path, channel='file',
                       source_id='fixture:eponym', source_name='Eponym fixture')
    reply = {'people': [{'name': 'Костя Крестников', 'base_name': 'Костя Крестников', 'aliases': []},
                        {'name': 'Ньютона', 'base_name': 'Ньютон', 'aliases': []}],
             'organizations': [], 'affiliations': []}
    result = extract_entities(raw.path, FakeModel([ModelReply(reply)]))
    assert [e['name'] for e in result.entities] == ['Костя Крестников']


def test_actual_person_still_eligible_for_enrichment(tmp_path):
    from graph.memory import MemoryStore, person_id
    from refresher.consolidate import pick_people
    from tests.test_people_scoring import record
    ex, _ = record('person', speaker='Newton', author=None)
    store = MemoryStore()
    store.apply(ex, "Newton studied motion. Newton's third law describes forces.")
    assert person_id('Newton') in {p.person_id for p in pick_people(tmp_path, store, 10)}


def test_a_full_name_inside_a_law_is_no_person_when_every_word_is_inflected():
    from graph.memory import _eponym
    assert _eponym('Исаак Ньютон', 'Объяснение третьего закона Исаака Ньютона.')
    assert not _eponym('Исаак Ньютон', 'Исаак Ньютон изучал движение.')
