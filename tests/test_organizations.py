"""Organizations with a type, people tied to them, and names that dedupe people across case, script and short forms."""
import json
from datetime import datetime, timezone

import pytest

from engine.entities import (EntityError, extract_entities, review_entities, supported_affiliations,
                             validate_entity_review)
from engine.llm import ModelReply
from engine.parse.article import chrome_start, citation_authors
from graph.memory import MemoryStore
from graph.memory import person_id
from graph.names import display_name, same_person, split_names
from refresher.graphview import map_data
from refresher.schemas import Extraction
from store.raw import capture_text
from tests.test_relate import item
from tests.test_triage import FakeModel

TEXT = "Вот статья Кости Крестникова, её приняли на NeurIPS.\nНьютон тут ни при чём.\nСбер выпустил модель."
REPLY = {"people": [{"name": "Кости Крестникова", "base_name": "Костя Крестников", "aliases": []}],
         "organizations": [{"name": "NeurIPS", "type": "venue", "aliases": []},
                           {"name": "Сбер", "base_name": "Сбер", "type": "company", "aliases": []}],
         "affiliations": [{"person": "Кости Крестникова", "organization": "NeurIPS", "relation": "published_at"}]}


@pytest.fixture
def raw(tmp_path):
    return capture_text(TEXT, tmp_path, channel="file", source_id="fixture:orgs", source_name="Org fixture")


def extract(raw, reply):
    return extract_entities(raw.path, FakeModel([ModelReply(reply)]))


def test_people_and_organizations_come_back_with_base_names_types_and_ties(raw):
    result = extract(raw, REPLY)
    kinds = {e["name"]: e for e in result.entities}
    assert kinds["Кости Крестникова"]["base_name"] == "Костя Крестников"
    assert kinds["NeurIPS"]["org_type"] == "venue" and kinds["Сбер"]["org_type"] == "company"
    [tie] = result.affiliations
    assert (tie["person"], tie["organization"], tie["relation"]) == (
        kinds["Кости Крестникова"]["entity_id"], kinds["NeurIPS"]["entity_id"], "published_at")
    assert tie["evidence_passage_ids"] == [1]
    assert extract(raw, {"entities": []}).reused  # the stored record, ties included, is read back unchanged
    assert extract_entities(raw.path, FakeModel([])).affiliations == result.affiliations


def test_a_bad_base_name_type_or_tie_is_repaired_instead_of_failing_the_reply(raw):
    reply = {"people": [{"name": "Кости Крестникова", "base_name": "Константин Иванов", "aliases": []}],
             "organizations": [{"name": "NeurIPS", "type": "conference", "aliases": []},
                               {"name": "Сбер", "type": "company", "aliases": []}],
             "affiliations": [{"person": "Кости Крестникова", "organization": "Сбер", "relation": "works_at"},
                              {"person": "Маша", "organization": "NeurIPS", "relation": "works_at"},
                              {"person": "Кости Крестникова", "organization": "NeurIPS", "relation": "invented"}]}
    result = extract(raw, reply)
    kinds = {e["name"]: e for e in result.entities}
    assert "base_name" not in kinds["Кости Крестникова"]  # other words than the written name
    assert kinds["NeurIPS"]["org_type"] == "other"
    # Сбер is two lines away from the name, Маша is not listed, "invented" is no relation: all dropped.
    assert result.affiliations == []


def test_the_review_judges_every_tie_and_a_rejected_tie_stays_out(raw):
    result = extract(raw, REPLY)
    [tie] = result.affiliations
    rows = [{"entity_id": e["entity_id"], "verdict": "supported", "reason": "ok"} for e in result.entities]
    base = {"entities": rows, "omitted_entities": [], "summary": "ok"}
    with pytest.raises(EntityError):
        validate_entity_review(base, result.entities, TEXT, raw.item_id, f"{raw.item_id}:section:1", result.affiliations)
    seen = []

    def reviewer(content):
        seen.append(content)
        return {**base, "affiliations": [{"affiliation_id": "A1", "verdict": "needs_revision", "reason": "Not stated."}]}

    class Reviewer(FakeModel):
        def complete_json(self, prompt, content):
            return ModelReply(reviewer(json.loads(content)))
    review = review_entities(raw.path, Reviewer([]), result).review
    assert seen[0]["affiliations"][0]["person"] == "Кости Крестникова"
    assert review["status"] == "accepted"  # a tie does not hold the item back
    assert supported_affiliations(result.affiliations, review) == []
    assert supported_affiliations(result.affiliations, None) == [tie]


def test_citation_authors_are_read_first_name_first():
    page = b'<html><head><meta name="citation_author" content="Krestnikov, Konstantin" />' \
           b'<meta name="citation_author" content="Gal, Yarin" /><meta name="citation_author" content="Plato" /></head></html>'
    assert citation_authors(page) == ["Konstantin Krestnikov", "Yarin Gal", "Plato"]


@pytest.mark.parametrize("a, b, same", [
    ("Костя Крестников", "Konstantin Krestnikov", True),
    ("Krestnikov; Konstantin", "Konstantin Krestnikov", True),
    ("Kostya Krestnikov", "Konstantin Krestnikov", True),
    ("Alexey Ivanov", "Алексей Иванов", True),
    ("Y. Gal", "Yarin Gal", True),
    ("Анна Иванова", "Иван Иванов", False),
    ("Ivan Petrov", "Petr Ivanov", False),
    ("Anna Smith", "Bob Smith", False),
])
def test_names_match_across_script_short_forms_and_order(a, b, same):
    assert same_person(a, b) is same


def post(item_id, day, author, entities, affiliations=(), parent=None):
    record = item(item_id, day, [(f"{item_id}-c", f"Claim from {item_id}.")], author=author).model_dump(mode="json")
    return Extraction.model_validate({**record, "entities": entities, "affiliations": list(affiliations),
                                      "parent": {"item_id": parent} if parent else None})


def store_with_post_and_paper():
    store = MemoryStore()
    store.apply(post("post", 20, "Kirill R", [
        {"entity_id": "kostya", "name": "Кости Крестникова", "base_name": "Костя Крестников", "kind": "person"},
        {"entity_id": "neurips", "name": "NeurIPS", "kind": "organization", "org_type": "venue"}],
        [{"affiliation_id": "t1", "person": "kostya", "organization": "neurips", "relation": "published_at"}]))
    store.apply(post("paper", 21, "Konstantin Krestnikov", [
        {"entity_id": "n2", "name": "NeurIPS", "kind": "organization", "org_type": "venue"}], parent="post"))
    return store


def test_people_take_their_base_name_and_organizations_their_type_and_ties():
    store = store_with_post_and_paper()
    assert "person:костя-крестников" in {p.person_id for p in store.people()}
    neurips = store.organization("org:neurips")
    assert neurips.type == "venue" and neurips.mentioned_in == {"post", "paper"}
    [tie] = store.affiliations()
    assert (tie.person_id, tie.org_id, tie.relation, tie.item_id) == ("person:костя-крестников", "org:neurips",
                                                                     "published_at", "post")


def test_a_person_named_in_a_post_and_the_author_of_the_page_it_links_to_are_proposed_as_one():
    store = store_with_post_and_paper()
    merges = [p for p in store.review(datetime(2026, 10, 22, tzinfo=timezone.utc)).proposals if p.kind == "merge_people"]
    assert [p.payload["people"] for p in merges] == [["person:костя-крестников", "person:konstantin-krestnikov"]]
    assert "the page it links to" in merges[0].rationale
    store._merge_person(*merges[0].payload["people"])
    assert [t.person_id for t in store.affiliations()] == ["person:konstantin-krestnikov"]


def test_enrichment_ties_a_person_to_an_organization_no_item_names():
    store = store_with_post_and_paper()
    store.add_enrichment("person:konstantin-krestnikov", {"value": "SaluteDevices", "url": "https://example.com/k"})
    assert store.organization("org:salutedevices").mentioned_in == set()
    assert ("person:konstantin-krestnikov", "org:salutedevices", "affiliated", None, "https://example.com/k") in \
        [(a.person_id, a.org_id, a.relation, a.item_id, a.url) for a in store.affiliations()]


def test_the_graph_lists_organizations_apart_from_people():
    data = map_data(store_with_post_and_paper(), datetime(2026, 10, 22, tzinfo=timezone.utc))
    assert all(p["kind"] == "person" for p in data["people"])
    [org] = data["organizations"]
    assert (org["id"], org["type"], org["named_in"]) == ("org:neurips", "venue", ["paper", "post"])
    assert org["ties"][0]["id"] == "person:костя-крестников" and org["ties"][0]["label"] == "published at"
    post_row = next(i for i in data["items"] if i["id"] == "post")
    assert {m["id"]: m["kind"] for m in post_row["mentions"]} == {"org:neurips": "organization",
                                                                  "person:костя-крестников": "person"}


@pytest.mark.parametrize("cited, shown", [
    ("Krestnikov; Konstantin", "Konstantin Krestnikov"), ("van der Berg, Jan", "Jan van der Berg"), ("Gal, Y.", "Y. Gal"),
    ("Ada Lovelace; Alan Turing", "Ada Lovelace; Alan Turing"), ("Ivan Petrov, CEO", "Ivan Petrov, CEO"),
    ("мама, папа", "мама, папа"),
])
def test_cited_names_read_first_name_first_and_share_an_id(cited, shown):
    assert display_name(cited) == shown
    if shown != cited:
        assert person_id(cited) == person_id(shown)


def test_an_arxiv_page_ends_where_its_tool_list_starts():
    text = "Abstract: truth is a compression artifact.\nBibliographic and Citation Tools\narXivLabs: projects\n"
    assert text[:chrome_start(text)] == "Abstract: truth is a compression artifact.\n"
    assert chrome_start("No chrome here.") == len("No chrome here.")


def test_entity_extraction_does_not_read_the_arxiv_tool_list(tmp_path):
    raw = capture_text("Konstantin Krestnikov trains models on Wikipedia.\nBibliographic and Citation Tools\n"
                       "arXivLabs is a framework for new arXiv features.", tmp_path, channel="file",
                       source_id="fixture:arxiv", source_name="arXiv fixture")
    model = FakeModel([ModelReply({"people": [], "organizations": [], "affiliations": []})])
    extract_entities(raw.path, model)
    assert "arXivLabs" not in model.calls[0][1] and "Wikipedia" in model.calls[0][1]


def merges_of(store):
    return [p for p in store.review(datetime(2026, 10, 22, tzinfo=timezone.utc)).proposals if p.kind == "merge_people"]


def test_an_affiliation_in_brackets_is_not_part_of_the_name():
    store = MemoryStore()
    store.apply(post("panel", 20, "Kirill R", [
        {"entity_id": "a", "name": "Herman Daly", "kind": "person"},
        {"entity_id": "b", "name": "Herman Daly (University of Maryland)", "kind": "person"},
        {"entity_id": "c", "name": "Abhijit Banerjee and Esther Duflo (MIT)", "kind": "person"},
        {"entity_id": "d", "name": "Esther Duflo", "kind": "person"},
        {"entity_id": "e", "name": "Ha‑Joon Chang (Cambridge)", "kind": "person"},
        {"entity_id": "f", "name": "Ha-Joon Chang", "kind": "person"},
        {"entity_id": "u", "name": "University of Maryland", "kind": "organization", "org_type": "university"}]))
    merges = {tuple(p.payload["people"]): p.rationale for p in merges_of(store)}
    assert set(merges) == {("person:herman-daly-university-of-maryland", "person:herman-daly"),
                           ("person:hajoon-chang-cambridge", "person:ha-joon-chang")}  # the clean name is kept
    assert "same name apart from “University of Maryland”" in merges[("person:herman-daly-university-of-maryland",
                                                                       "person:herman-daly")]
    store._merge_person("person:herman-daly-university-of-maryland", "person:herman-daly")
    assert [(t.person_id, t.org_id) for t in store.affiliations()] == [("person:herman-daly", "org:university-of-maryland")]
    store._merge_person("person:hajoon-chang-cambridge", "person:ha-joon-chang")
    assert len(store.affiliations()) == 1  # no organization is named Cambridge here, so none is made


def test_the_same_name_in_unrelated_posts_is_proposed_and_a_reviewed_identity_is_not():
    store = MemoryStore()
    store.apply(post("one", 20, "Kirill R", [{"entity_id": "a", "name": "Dani Rodrik", "kind": "person"}]))
    store.apply(post("two", 21, "Kirill R", [{"entity_id": "b", "name": "Dani Rodrik (Harvard)", "kind": "person"}]))
    [merge] = merges_of(store)
    assert merge.payload["people"] == ["person:dani-rodrik-harvard", "person:dani-rodrik"]
    store._people["person:dani-rodrik"].identity_url = "https://example.com/rodrik"
    assert merges_of(store) == []


def test_profiles_citing_the_same_page_make_namesakes_one_proposal():
    store = MemoryStore()
    store.apply(post("one", 20, "Yarin Gal", []))
    store.apply(post("two", 21, "Kirill R", [{"entity_id": "a", "name": "Y. Gal", "kind": "person"}]))
    assert merges_of(store) == []  # a surname and an initial, nothing in common
    for pid in ("person:yarin-gal", "person:y-gal"):
        store._people[pid].role = {"value": "Professor", "url": "https://example.com/gal"}
    [merge] = merges_of(store)
    assert "profiles cite the same page" in merge.rationale


@pytest.mark.parametrize("name, parts", [
    ("Abhijit Banerjee and Esther Duflo (MIT)", ["Abhijit Banerjee", "Esther Duflo"]),
    ("Abhijit Banerjee, Esther Duflo, and Michael Kremer", ["Abhijit Banerjee", "Esther Duflo", "Michael Kremer"]),
    ("Ada Lovelace; Alan Turing", ["Ada Lovelace", "Alan Turing"]),
    ("Анна Иванова и Иван Петров", ["Анна Иванова", "Иван Петров"]),
    ("Jean-Paul Sartre and Simone de Beauvoir", ["Jean-Paul Sartre", "Simone de Beauvoir"]),
    ("Procter and Gamble", []), ("Bill and Melinda Gates", []), ("Gal, Yarin", []), ("Herman Daly (Maryland)", []),
])
def test_a_record_naming_several_people_is_read_as_each(name, parts):
    assert split_names(name) == parts


def test_a_record_of_two_people_is_proposed_split_and_stays_split():
    store = MemoryStore()
    store.apply(post("panel", 20, "Kirill R", [
        {"entity_id": "a", "name": "Abhijit Banerjee and Esther Duflo (MIT)", "kind": "person"},
        {"entity_id": "m", "name": "MIT", "kind": "organization", "org_type": "university"}],
        [{"affiliation_id": "t1", "person": "a", "organization": "m", "relation": "works_at"}]))
    store.apply(post("talk", 21, "Esther Duflo", []))
    [split] = [p for p in store.review(datetime(2026, 10, 22, tzinfo=timezone.utc)).proposals if p.kind == "split_person"]
    assert split.payload == {"person": "person:abhijit-banerjee-and-esther-duflo-mit",
                             "name": "Abhijit Banerjee and Esther Duflo (MIT)", "names": ["Abhijit Banerjee", "Esther Duflo"]}
    assert "Esther Duflo is already here" in split.rationale
    store._splits[split.payload["person"]] = split.payload["names"]
    store._refresh()
    people = {p.person_id: p for p in store.people()}
    assert split.payload["person"] not in people
    assert people["person:abhijit-banerjee"].mentioned_in == {"panel"}
    assert people["person:esther-duflo"].mentioned_in == {"panel"} and people["person:esther-duflo"].authored == {"talk"}
    assert {(t.person_id, t.org_id) for t in store.affiliations()} == {("person:abhijit-banerjee", "org:mit"),
                                                                       ("person:esther-duflo", "org:mit")}
    store.apply(post("later", 22, "Kirill R", [{"entity_id": "a", "name": "Abhijit Banerjee and Esther Duflo (MIT)",
                                                "kind": "person"}]))  # named as one again: split again
    people = {p.person_id: p for p in store.people()}
    assert split.payload["person"] not in people and "later" in people["person:esther-duflo"].mentioned_in
    assert not [p for p in store.review(datetime(2026, 10, 23, tzinfo=timezone.utc)).proposals if p.kind == "split_person"]
