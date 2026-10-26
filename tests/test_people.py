import io
import json
import shutil
from pathlib import Path
from urllib.error import HTTPError

import pytest

from engine import llm, people
from engine.llm import ModelOutputError, ModelReply
from engine.people import Hit, SearchError, TavilySearch, enrich, queries
from refresher import scoring, vault
from refresher.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
OXFORD = "https://www.cs.ox.ac.uk/people/yarin.gal/"


class FakeSearch:
    name = "fake-search"

    def __init__(self, hits=None):
        self.hits = [Hit("Yarin Gal", OXFORD, "Associate Professor of Machine Learning at Oxford, leads OATML.")] \
            if hits is None else hits
        self.queries = []

    def search(self, query):
        self.queries.append(query)
        return self.hits


class FakeModel:
    model_name = "fake-super"

    def __init__(self, data):
        self.data, self.calls = data, []

    def complete_json(self, system_prompt, content):
        self.calls.append(json.loads(content))
        return ModelReply(self.data)


MATCH = {"match": True, "role": {"value": "Associate Professor", "url": OXFORD},
         "affiliation": {"value": "University of Oxford", "url": OXFORD},
         "domains": [{"value": "Bayesian deep learning", "url": OXFORD},
                     {"value": "invented", "url": "https://elsewhere.example/"}],
         "note": "Oxford page fits the semantic entropy context."}


def test_queries_use_the_vault_hints_and_stay_at_three():
    assert queries("Yarin Gal", "OATML", "Semantic entropy") == ['"Yarin Gal" OATML', '"Yarin Gal" Semantic entropy',
                                                                 '"Yarin Gal"']
    assert queries("Yarin Gal", None, None) == ['"Yarin Gal"']


def test_every_field_keeps_its_source_and_unsourced_values_are_dropped():
    model = FakeModel(MATCH)
    record = enrich("person:yarin-gal", "Yarin Gal", ["Yarin Gal leads the OATML group at Oxford."], "OATML",
                    "Semantic entropy", FakeSearch(), model)
    assert record["match"] and record["role"] == {"value": "Associate Professor", "url": OXFORD}
    assert [d["value"] for d in record["domains"]] == ["Bayesian deep learning"]  # the other URL was no result
    assert len(record["searches"]) == 3 and record["model"] == "fake-super"
    assert model.calls[0]["context"] == ["Yarin Gal leads the OATML group at Oxford."]


def test_a_different_person_with_the_same_name_fills_nothing():
    record = enrich("person:yarin-gal", "Yarin Gal", [], None, None, FakeSearch(),
                    FakeModel({**MATCH, "match": False, "note": "A footballer."}))
    assert not record["match"] and record["role"] is None and record["domains"] == []


def test_no_results_make_no_model_call():
    model = FakeModel(MATCH)
    record = enrich("person:x", "X", [], None, None, FakeSearch(hits=[]), model)
    assert not model.calls and record["note"] == "The searches found nothing."


def test_an_answer_without_a_match_decision_is_rejected():
    with pytest.raises(ModelOutputError):
        enrich("person:x", "X", [], None, None, FakeSearch(), FakeModel({"role": None}))


def test_tavily_errors_never_show_the_key(monkeypatch):
    def refuse(request, timeout):
        assert request.headers["Authorization"] == "Bearer tvly-secret"
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"tvly-secret"))
    monkeypatch.setattr(people, "urlopen", refuse)
    with pytest.raises(SearchError, match="rejected the API key") as error:
        TavilySearch("tvly-secret").search('"Yarin Gal"')
    assert "tvly-secret" not in str(error.value)


def test_cli_enriches_once_and_people_notes_show_sources_and_trust(tmp_path, monkeypatch, capsys):
    shutil.copytree(FIXTURES / "vault", tmp_path / "vault")
    search = FakeSearch()
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)  # keep the real .env out of the tests
    monkeypatch.setattr(people, "search_from_environment", lambda: search)
    monkeypatch.setattr(llm, "client_from_environment", lambda: FakeModel(MATCH))
    v = tmp_path / "vault"
    assert main(["enrich", "--vault", str(v), "--person", "Yarin Gal"]) == 0
    assert vault.enriched(v, "person:yarin-gal") and "tavily: '\"Yarin Gal\"'" in capsys.readouterr().out
    assert main(["rebuild", "--vault", str(v)]) == 0
    note = next((v / "wiki" / "people").glob("Yarin Gal.md")).read_text()
    assert f"[Associate Professor]({OXFORD}) at [University of Oxford]({OXFORD})" in note
    assert "## Works on" in note and "enrichment_status: enriched" in note
    assert "Trust 0.50: 0 independent confirmations, 0 contradictions" in note
    # Without --person, people already looked up are skipped.
    assert main(["enrich", "--vault", str(v), "--limit", "50"]) == 0
    assert '"Yarin Gal"' not in "".join(search.queries[3:])


def test_trust_rationale_names_the_evidence_or_the_user():
    assert scoring.trust_rationale(2, 0, 1, 0) == "0.80: 2 independent confirmations, 0 contradictions, 1 up, 0 down from you"
    assert scoring.trust_rationale(pinned=0.9) == "0.90: set by you"
