"""GraphStore: the one interface ingest, KM and retrieval use. Draft for Victor to own.

MemoryStore (graph/memory.py) implements it for development and tests; the Neo4j store will
implement the same methods, so callers never depend on Neo4j directly.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Protocol, runtime_checkable

from refresher.schemas import Claim, Extraction, Override, Relation, Source

EDGE_TYPES = {"AUTHORED_BY", "PUBLISHED_IN", "MENTIONS", "FROM", "DUPLICATES", "SUPPORTS",
              "SUPERSEDES", "CONTRADICTS", "ELABORATES",
              "PART_OF",  # claim -> Idea
              "ABOUT"}  # claim -> Person or Org: a profile fact, which gets no Idea


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
    hidden: bool = False  # a triage-skipped stub: listed nowhere, kept only as a target for SUPERSEDES


@dataclass
class ClaimNode:
    claim: Claim
    item_id: str
    durability: str
    half_life_days: float
    clock_start: datetime  # event time; moved to the latest independent confirmation by the derive pass
    confirmations: int = 0
    duplicate_rank: int = 0  # 0 = first source; > 0 = a repost, counted as noise
    superseded_by: str | None = None
    votes: int = 0
    idea_id: str | None = None  # world claims only
    subject_id: str | None = None  # person:… or org:… when the claim is about them; then no Idea


@dataclass
class IdeaNode:
    """The set of claims that make the same assertion. Ingest creates it provisional; the review firms it up."""
    idea_id: str
    canonical_claim_id: str
    title: str
    durability: str
    valid_at: datetime  # event time of the newest member
    members: set[str] = field(default_factory=set)  # claim ids
    status: str = "provisional"  # provisional | firm


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
    facts: set[str] = field(default_factory=set)  # claim ids about this person: their profile
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
    at: datetime | None = None  # event time of the later side, for relations between claims


@dataclass
class Placement:
    """What graph.apply tells ingest, enough for the bot reply."""
    item_id: str
    status: str  # placed | held_back | already_placed
    claims: int = 0
    author: str | None = None
    duplicate_of: str | None = None
    relations: list[Edge] = field(default_factory=list)


@dataclass
class ReviewReport:
    """What graph.review() decided. The verdicts are also written to reviews/ so a rebuild replays them."""
    at: datetime
    item_duplicates: list[Edge] = field(default_factory=list)  # later item DUPLICATES first item
    firmed: list[str] = field(default_factory=list)  # idea ids, provisional -> firm
    merged_ideas: list[tuple[str, str]] = field(default_factory=list)  # (absorbed, kept)
    topics: dict[str, list[str]] = field(default_factory=dict)  # topic title -> idea ids (3+, 2+ sources)
    merged_people: list[tuple[str, str]] = field(default_factory=list)  # (absorbed, kept)


@runtime_checkable
class GraphStore(Protocol):
    def apply(self, extraction: Extraction, text: str = "", relation: Relation | None = None) -> Placement:
        """Place one item. `relation` is its stored verdicts; without them the store relates it lexically."""
    def apply_override(self, override: Override) -> None: ...
    def items(self) -> Iterable[ItemNode]: ...
    def claims(self) -> Iterable[ClaimNode]: ...
    def people(self) -> Iterable[PersonNode]: ...
    def sources(self) -> Iterable[SourceNode]: ...
    def edges(self) -> Iterable[Edge]: ...
    def trust(self, person_id: str) -> float: ...
    def ideas(self) -> Iterable[IdeaNode]: ...
    def idea(self, idea_id: str) -> IdeaNode: ...
    def review(self, at: datetime) -> ReviewReport: ...
    def apply_review(self, report: ReviewReport) -> None: ...

    # graph.query: what the refresher gathers from.
    def claims_for(self, terms: Iterable[str], hops: int = 2) -> list[ClaimNode]:
        """Claims holding all words of a term, or about a person a term names, plus those within `hops` edges
        through Ideas, profiles and claim relations (not whole items). Visible claims, nearest first."""
    def chain(self, claim_id: str) -> list[ClaimNode]:
        """The supersession chain through this claim, oldest first, held-back originals included."""
    def people_for(self, claim_ids: Iterable[str]) -> list[PersonNode]:
        """Authors of the claims' items and the people the claims are about, most involved first."""
    def neighbours(self, item_id: str) -> list[Edge]:
        """Edges touching the item or any of its claims, without FROM span edges."""
