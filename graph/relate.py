"""Relation stage: how each new claim relates to what the graph already holds.

A relator labels each claim against its candidates: SAME, UPDATES, CONFLICTS, ADDS_DETAIL or NONE (the default),
and says whether the claim is about a person, an organization or the world. The label is all the relator decides:
event time decides which claim supersedes which, and provenance decides SUPPORTS versus DUPLICATES (graph/memory.py).

A model's verdicts are not reproducible, so they are a vault stage, relations/<item_id>.json, written once by
`refresher relate` and replayed by every rebuild. Items without a verdict file are related lexically on the fly,
which keeps rebuilds and tests offline.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from pydantic import ValidationError

from engine.llm import JsonModel, ModelOutputError
from refresher.schemas import Claim, Entity, Extraction, Relation, Source, Verdict

CANDIDATES = 5
CLAIM_SIMILARITY = 0.8  # claim statement, word-set Jaccard: the lexical relator's SAME
ENTITY_BOOST = 0.1  # per entity both items name
WORD = re.compile(r"\w+", re.UNICODE)


def words(text: str) -> list[str]:
    return WORD.findall(text.casefold())


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


@dataclass(frozen=True)
class ItemContext:
    item_id: str
    text: str
    source: Source
    entities: list[Entity] = field(default_factory=list)


@dataclass(frozen=True)
class Candidate:
    """A claim already in the graph, as a relator sees it."""
    claim_id: str
    statement: str
    item_id: str
    event_time: datetime
    source_name: str | None = None
    author: str | None = None
    idea_id: str | None = None
    entities: frozenset[str] = frozenset()  # casefolded names the candidate's item mentions


class Relator(Protocol):
    name: str
    prompt_version: str
    model_name: str | None

    def relate(self, item: ItemContext, claims: list[Claim], candidates: dict[str, list[Candidate]]) -> list[Verdict]: ...


def candidates(claim: Claim, entities: set[str], pool: list[Candidate], k: int = CANDIDATES) -> list[Candidate]:
    """Top k by word overlap, boosted by shared entities, one per Idea. Embeddings can replace the score here."""
    mine = set(words(claim.statement))
    scored = []
    for c in pool:
        overlap = jaccard(mine, set(words(c.statement)))
        if overlap > 0:
            scored.append((-(overlap + ENTITY_BOOST * len(entities & c.entities)), c.event_time, c.claim_id, c))
    out, ideas = [], set()
    for *_, c in sorted(scored, key=lambda row: row[:3]):
        if c.idea_id is not None and c.idea_id in ideas:
            continue
        ideas.add(c.idea_id)
        out.append(c)
        if len(out) == k:
            break
    return out


def judge(extraction: Extraction, text: str, pool: list[Candidate], relator: Relator) -> Relation:
    """Relate every claim of one item; the Relation is what relations/<item_id>.json stores."""
    entities = {e.name.casefold() for e in extraction.entities}
    shown = {c.claim_id: candidates(c, entities, pool) for c in extraction.claims}
    item = ItemContext(extraction.item_id, text, extraction.source, list(extraction.entities))
    verdicts = relator.relate(item, list(extraction.claims), shown)
    return Relation(item_id=extraction.item_id, relator=relator.name, model=relator.model_name,
                    prompt_version=relator.prompt_version,
                    candidates={cid: [c.claim_id for c in cands] for cid, cands in shown.items()},
                    verdicts=verdicts)


def subject_of(claim: Claim, entities: list[Entity]) -> tuple[str, str | None]:
    """Lexical stand-in until the extractor labels subjects: a claim that opens with a person or organization
    the item names is about them."""
    statement = claim.statement.casefold()
    for e in sorted(entities, key=lambda e: -len(e.name)):
        name = e.name.casefold()
        if e.kind in ("person", "organization") and statement.startswith(name) \
                and not statement[len(name):len(name) + 1].isalnum():
            return e.kind, e.name
    return "world", None


class LexicalRelator:
    """Today's baseline: a near-identical statement is SAME, anything else NONE. No network."""
    name = "lexical"
    prompt_version = "lexical-v1"
    model_name = None

    def relate(self, item, claims, candidates):
        out = []
        for claim in claims:
            subject, subject_name = subject_of(claim, item.entities)
            mine = set(words(claim.statement))
            same = next((c for c in candidates[claim.claim_id]
                         if jaccard(mine, set(words(c.statement))) >= CLAIM_SIMILARITY), None)
            out.append(Verdict(claim_id=claim.claim_id, subject=subject, subject_name=subject_name,
                               label="SAME" if same and subject == "world" else "NONE",
                               target_id=same.claim_id if same and subject == "world" else None,
                               rationale="Near-identical statement." if same and subject == "world" else ""))
        return out


PROMPT_VERSION = "relate-v1"
SYSTEM_PROMPT = """You relate the claims of one newly captured item to claims already in a personal knowledge graph.

You get the whole item (its text and source) and, for each of its claims, up to five candidate claims from other items.
Read the whole item first: a correction that says "previously 14:00" refers to the old time, it does not repeat it.

For each claim answer:
- subject: "person" or "organization" when the claim is a fact about that specific person or organization (their role,
  work, opinion, biography); then give subject_name exactly as the item names them. Otherwise "world".
- label, against the single best candidate:
  SAME: the same assertion, possibly worded differently.
  UPDATES: the same subject and question, but the value has changed (a new time, figure, version or status).
  CONFLICTS: the same subject and question, and the two cannot both be true, with no sign that one replaces the other.
  ADDS_DETAIL: about the same thing, and adds detail the candidate lacks without changing it.
  NONE: anything else. NONE is the default; use it whenever you are unsure, and for every person or organization fact.
- target: the candidate id for SAME, UPDATES, CONFLICTS or ADDS_DETAIL; null for NONE. Only ids from that claim's list.
- rationale: one short sentence.

Do not decide which claim is newer or who is independent; the graph decides that from dates and authors.

Return JSON: {"verdicts": [{"claim": "c1", "subject": "world", "subject_name": null, "label": "NONE",
"target": null, "rationale": "..."}]} with one entry per claim."""


class ModelRelator:
    """One call per item with the whole item as context; short ids so the model copies them reliably."""
    name = "model"
    prompt_version = PROMPT_VERSION

    def __init__(self, model: JsonModel):
        self.model = model
        self.model_name = model.model_name

    def relate(self, item, claims, candidates):
        claim_ids = {f"c{n}": c.claim_id for n, c in enumerate(claims, 1)}
        to_short: dict[str, str] = {}
        for cands in candidates.values():
            for c in cands:
                to_short.setdefault(c.claim_id, f"k{len(to_short) + 1}")
        by_id = {c.claim_id: c for cands in candidates.values() for c in cands}
        content = json.dumps({
            "item": {"text": item.text, "source": item.source.source_name, "channel": item.source.channel,
                     "author": item.source.author_hint.name if item.source.author_hint else None,
                     "date": item.source.event_time.isoformat()},
            "claims": [{"id": short, "statement": c.statement,
                        "candidates": [to_short[k.claim_id] for k in candidates[c.claim_id]]}
                       for short, c in zip(claim_ids, claims)],
            "candidates": {short: {"statement": by_id[cid].statement, "source": by_id[cid].source_name,
                                   "author": by_id[cid].author, "date": by_id[cid].event_time.isoformat()}
                           for cid, short in to_short.items()},
        }, ensure_ascii=False)
        reply = self.model.complete_json(SYSTEM_PROMPT, content)
        rows = reply.data.get("verdicts")
        if not isinstance(rows, list):
            raise ModelOutputError("The relation model did not return a verdicts list.")
        out: dict[str, Verdict] = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("claim") not in claim_ids:
                raise ModelOutputError("The relation model returned a verdict for an unknown claim.")
            claim_id = claim_ids[row["claim"]]
            target = row.get("target")
            allowed = {to_short[c.claim_id]: c.claim_id for c in candidates[claim_id]}
            if target in (None, "", "null", "NONE"):
                target_id = None
            elif target in allowed:
                target_id = allowed[target]
            else:
                raise ModelOutputError(f"The relation model targeted {target!r}, which was not a candidate.")
            label, subject = row.get("label") or "NONE", row.get("subject") or "world"
            try:
                out[claim_id] = Verdict(claim_id=claim_id, subject=subject,
                                        subject_name=(row.get("subject_name") or None) if subject != "world" else None,
                                        label=label, target_id=target_id if label != "NONE" else None,
                                        rationale=str(row.get("rationale") or ""))
            except ValidationError as error:
                raise ModelOutputError(f"The relation model returned an invalid verdict: {error.errors()[0]['msg']}") from None
        return [out.get(c.claim_id) or Verdict(claim_id=c.claim_id, rationale="The model gave no verdict.")
                for c in claims]
