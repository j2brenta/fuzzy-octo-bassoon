"""GraphStore: the one interface ingest, KM and retrieval use. Draft for Victor to own.

MemoryStore (graph/memory.py) implements it for development and tests; the Neo4j store will
implement the same methods, so callers never depend on Neo4j directly.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Protocol

from refresher.schemas import Claim, Extraction, Override, Source

EDGE_TYPES = {"AUTHORED_BY", "PUBLISHED_IN", "MENTIONS", "FROM", "DUPLICATES", "SUPPORTS",
              "SUPERSEDES", "CONTRADICTS", "ELABORATES"}


@dataclass
class ItemNode:
    item_id: str
    source: Source
    title: str
    text: str = ""
    author_id: str | None = None
    source_key: str | None = None
    durability: str | None = None  # triage class for the whole item
    duplicate_of: str | None = None  # earliest item it reposts
    review_status: str = "accepted"  # ingestion quality_status; other values only with include_unreviewed


@dataclass
class ClaimNode:
    claim: Claim
    item_id: str
    durability: str
    half_life_days: float
    clock_start: datetime  # event time; reset by an independent confirmation
    confirmations: int = 0
    duplicate_rank: int = 0  # 0 = first source; > 0 = a repost, counted as noise
    superseded_by: str | None = None
    votes: int = 0


@dataclass
class PersonNode:
    person_id: str
    name: str
    handles: set[str] = field(default_factory=set)
    authored: set[str] = field(default_factory=set)  # item ids
    mentioned_in: set[str] = field(default_factory=set)
    confirmations: int = 0
    contradictions: int = 0
    up: int = 0
    down: int = 0
    trust_pinned: float | None = None
    enrichment_status: str = "pending"


@dataclass
class SourceNode:
    key: str
    name: str
    channel: str
    items: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class Edge:
    src: str
    type: str
    dst: str
    rationale: str = ""


@dataclass
class Placement:
    """What graph.apply tells ingest, enough for the bot reply."""
    item_id: str
    status: str  # placed | held_back | already_placed
    claims: int = 0
    author: str | None = None
    duplicate_of: str | None = None
    relations: list[Edge] = field(default_factory=list)


class GraphStore(Protocol):
    def apply(self, extraction: Extraction, text: str = "") -> Placement: ...
    def apply_override(self, override: Override) -> None: ...
    def items(self) -> Iterable[ItemNode]: ...
    def claims(self) -> Iterable[ClaimNode]: ...
    def people(self) -> Iterable[PersonNode]: ...
    def sources(self) -> Iterable[SourceNode]: ...
    def edges(self) -> Iterable[Edge]: ...
    def trust(self, person_id: str) -> float: ...
