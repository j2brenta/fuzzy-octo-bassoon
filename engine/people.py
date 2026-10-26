"""People enrichment: who a person in the graph is, from a few web searches, with a source for every field.

Tavily searches the web (at most three queries per person); the model reads the results and fills role, affiliation
and domains, each with the URL it came from, or nothing when the results are about someone else or say too little.
The record is a vault stage, people/<person>.json, written once and replayed by rebuilds, so they stay offline.
"""
import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from engine.llm import JsonModel, ModelOutputError

TAVILY_URL = "https://api.tavily.com/search"
SEARCHES = 3  # per person
RESULTS = 5  # per search
DOMAINS = 5
PROMPT_VERSION = "enrich-v1"


class SearchError(RuntimeError):
    """A safe, user-facing error that never embeds the key or the response body."""


@dataclass(frozen=True)
class Hit:
    title: str
    url: str
    content: str


class TavilySearch:
    name = "tavily"

    def __init__(self, api_key: str, timeout: int = 30):
        self.api_key, self.timeout = api_key, timeout

    def search(self, query: str) -> list[Hit]:
        request = Request(TAVILY_URL, method="POST",
                          data=json.dumps({"query": query, "max_results": RESULTS, "search_depth": "basic"}).encode(),
                          headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read(2_000_001))
        except HTTPError as error:
            status = error.code
            error.close()
            if status in {401, 403}:
                raise SearchError("Tavily rejected the API key.") from None
            if status in {429, 432, 433}:
                raise SearchError("Tavily rate or credit limit reached; try later.") from None
            raise SearchError(f"Tavily returned HTTP {status}.") from None
        except (URLError, TimeoutError, socket.timeout):
            raise SearchError("Could not reach Tavily within the request timeout.") from None
        except (ValueError, UnicodeError):
            raise SearchError("Tavily returned an unreadable response.") from None
        rows = data.get("results") if isinstance(data, dict) else None
        return [Hit(str(r.get("title") or ""), str(r["url"]), str(r.get("content") or ""))
                for r in rows or [] if isinstance(r, dict) and r.get("url")]


def search_from_environment() -> TavilySearch | None:
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    return TavilySearch(key) if key else None


def queries(name: str, affiliation: str | None, topic: str | None) -> list[str]:
    """Up to three searches: the name with what the vault says about them, then the name alone."""
    out = [f'"{name}" {affiliation}' if affiliation else None, f'"{name}" {topic}' if topic else None, f'"{name}"']
    return list(dict.fromkeys(q for q in out if q))[:SEARCHES]


SYSTEM_PROMPT = """You identify one person from web search results, for a personal knowledge base.

You get the person's name, what the user's own notes say about them (context), and search results.
First decide whether the results are about this person: the same name is not enough; the results must fit the context.
If they do not fit, or you are unsure, answer match false and leave every field null or empty.

Otherwise fill, using only the results:
- role: their current role or occupation, a short phrase.
- affiliation: their current organization.
- domains: up to five subjects they work on, short noun phrases.
Every value needs the url of the result it came from. Leave a field null rather than guess.

Return JSON: {"match": true, "role": {"value": "...", "url": "..."}, "affiliation": {"value": "...", "url": "..."},
"domains": [{"value": "...", "url": "..."}], "note": "one short sentence on how you matched them"}"""


def _sourced(row, urls: set[str]) -> dict | None:
    """A value with the URL it came from, or None when either is missing or the URL was not a search result."""
    if not isinstance(row, dict):
        return None
    value, url = str(row.get("value") or "").strip(), row.get("url")
    return {"value": value[:120], "url": url} if value and url in urls else None


def enrich(person_id: str, name: str, context: list[str], affiliation: str | None, topic: str | None,
           search, model: JsonModel, log=lambda message: None) -> dict:
    """Search, read, and return the people/<person>.json record. Raises SearchError or ModelError."""
    searched = []
    for query in queries(name, affiliation, topic):
        hits = search.search(query)
        log(f"tavily: {query!r}: {len(hits)} results")
        searched.append({"query": query, "results": [{"title": h.title, "url": h.url, "content": h.content[:600]}
                                                     for h in hits]})
    urls = {r["url"] for s in searched for r in s["results"]}
    record = {"schema_version": 1, "person_id": person_id, "name": name,
              "searched_at": datetime.now(timezone.utc).isoformat(), "search": search.name, "searches": searched,
              "model": model.model_name, "prompt_version": PROMPT_VERSION,
              "match": False, "role": None, "affiliation": None, "domains": [], "note": ""}
    if not urls:
        record["note"] = "The searches found nothing."
        return record
    reply = model.complete_json(SYSTEM_PROMPT, json.dumps(
        {"name": name, "context": context[:8], "results": [r for s in searched for r in s["results"]]},
        ensure_ascii=False))
    data = reply.data
    if not isinstance(data, dict) or not isinstance(data.get("match"), bool):
        raise ModelOutputError("The enrichment model did not say whether the results match the person.")
    record["note"] = str(data.get("note") or "")[:300]
    if not data["match"]:
        return record
    record.update(match=True, role=_sourced(data.get("role"), urls), affiliation=_sourced(data.get("affiliation"), urls),
                  domains=[d for d in (_sourced(row, urls) for row in data.get("domains") or []) if d][:DOMAINS])
    return record
