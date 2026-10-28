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


def test_legacy_eponym_does_not_spend_an_enrichment_lookup(tmp_path):
    from graph.memory import MemoryStore, person_id
    from refresher.consolidate import pick_people
    from tests.test_people_scoring import record
    ex, _ = record('eponym', speaker='Ньютона', author=None)
    store = MemoryStore()
    store.apply(ex, 'Объяснение третьего закона Ньютона.')
    assert person_id('Ньютона') in {p.person_id for p in store.people()}
    assert pick_people(tmp_path, store, 10) == []


def test_actual_person_still_eligible_for_enrichment(tmp_path):
    from graph.memory import MemoryStore, person_id
    from refresher.consolidate import pick_people
    from tests.test_people_scoring import record
    ex, _ = record('person', speaker='Newton', author=None)
    store = MemoryStore()
    store.apply(ex, "Newton studied motion. Newton's third law describes forces.")
    assert person_id('Newton') in {p.person_id for p in pick_people(tmp_path, store, 10)}
