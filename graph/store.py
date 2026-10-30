"""GraphStore: the one interface ingest, KM and retrieval use. Draft for Victor to own.

MemoryStore (graph/memory/) implements it for development and tests; the Neo4j store will
implement the same methods, so callers never depend on Neo4j directly.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Protocol, runtime_checkable

from refresher.schemas import Attribution, Claim, Extraction, Override, Relation, Source

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
    linked_from: str | None = None  # the item whose link this page was captured from
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
    attribution_reviewed: bool = False
    identity_url: str | None = None
    evidence_origin: str | None = None
    speaker_name: str | None = None  # source-grounded dictionary form; attribution keeps the exact written name
    speaker_id: str | None = None
    attribution: Attribution | None = None
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
    disagreements: int = 0  # unresolved conflict, not evidence that either person is wrong
    up: int = 0
    down: int = 0
    trust_pinned: float | None = None
    statements: set[str] = field(default_factory=set)  # claims attributed to this speaker
    facts: set[str] = field(default_factory=set)  # claim ids about this person: their profile
    enrichment_status: str = "pending"  # pending | enriched | no_match (the web results were about someone else)
    role: dict | None = None  # {"value", "url"} from enrichment, like affiliation and each domain
    affiliation: dict | None = None
    domains: list[dict] = field(default_factory=list)
    identity_url: str | None = None


@dataclass
class OrgNode:
    org_id: str
    name: str
    types: dict[str, int] = field(default_factory=dict)  # org type -> how many items call it that
    mentioned_in: set[str] = field(default_factory=set)  # item ids
    facts: set[str] = field(default_factory=set)  # claim ids about it: its profile

    @property
    def type(self) -> str:
        """The type most items give it; "other" when none says."""
        return min(self.types, key=lambda t: (-self.types[t], t)) if self.types else "other"


@dataclass(frozen=True)
class Affiliation:
    """A person's tie to an organization: stated in an item, or found by web enrichment (item_id None, with url)."""
    person_id: str
    org_id: str
    relation: str  # works_at | leads | founded | studied_at | member_of | published_at | affiliated
    item_id: str | None = None
    url: str | None = None


AFFILIATION_WORDS = {"works_at": "works at", "leads": "leads", "founded": "founded", "studied_at": "studied at",
                     "member_of": "member of", "published_at": "published at", "affiliated": "affiliated with"}


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
    ideas: list[str] = field(default_factory=list)  # Idea ids its claims joined, in claim order


@dataclass
class TopicNode:
    """Ideas about one subject. A proposal the user accepted makes it; ingest attaches new Ideas provisionally."""
    topic_id: str
    title: str
    ideas: set[str] = field(default_factory=set)  # accepted members
    attached: set[str] = field(default_factory=set)  # provisional members: close to its centroid, or in its clusters
    clusters: set[str] = field(default_factory=set)  # clusters mostly inside it


@dataclass
class ClusterNode:
    """A tight group of Ideas from any number of sources: one article's sub-theme, or Ideas several articles share.
    Recomputed with the graph, never decided; a topic proposal is made of clusters."""
    cluster_id: str
    title: str
    ideas: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)  # source keys of its visible items
    centroid: list[float] = field(default_factory=list, repr=False)
    fingerprint: str = ""  # of its Ideas' canonical claims: a stored name applies while this matches
    items: set[str] = field(default_factory=set)  # visible items (posts) its Ideas come from
    assigned: set[str] = field(default_factory=set)  # Ideas a model placed here, not close enough by embedding


@dataclass(frozen=True)
class Link:
    """Two clusters, or two topics, that belong near each other: claim relations crossing them, Ideas they share,
    and how close their centroids are. Recomputed with the graph; the relations are its evidence."""
    level: str  # cluster | topic
    a: str
    b: str
    weight: float  # relations + shared Ideas + centroid cosine
    similarity: float
    relations: tuple[tuple[str, str, str], ...] = ()  # (claim, type, claim) as in the graph
    shared: tuple[str, ...] = ()  # idea ids in both (topics only; clusters do not overlap)


PROPOSAL_KINDS = ("merge_ideas", "merge_people", "split_person", "topic", "relation")


@dataclass(frozen=True)
class Proposal:
    """A change the review suggests and the user decides. The payload names claims (or people), not Ideas,
    because Idea ids move as members join. The fingerprint is what the claims said when it was proposed."""
    proposal_id: str
    kind: str  # one of PROPOSAL_KINDS
    payload: dict  # merge_ideas/topic: {"claims": [...], "title"?}; relation: {"claims": [src, dst], "type"};
                   # merge_people: {"people": [absorbed, kept]};
                   # split_person: {"person": id, "name": as written, "names": [one per person]}
    rationale: str
    at: datetime
    fingerprint: str


@dataclass(frozen=True)
class Decision:
    """The user's answer to a proposal, bound to the fingerprint they saw. Append-only: the latest one counts."""
    proposal_id: str
    action: str  # accept | reject | defer
    token: str
    fingerprint: str
    rationale: str
    at: datetime


@dataclass
class ReviewReport:
    """What graph.review() decided. The automatic part is written to graph-reviews/ and replayed; proposals wait
    for the user in graph-reviews/proposals/."""
    at: datetime
    item_duplicates: list[Edge] = field(default_factory=list)  # later item DUPLICATES first item
    firmed: list[str] = field(default_factory=list)  # idea ids, provisional -> firm
    merged_ideas: list[tuple[str, str]] = field(default_factory=list)  # (absorbed, kept)
    topics: dict[str, list[str]] = field(default_factory=dict)  # topic title -> idea ids (3+, 2+ sources)
    merged_people: list[tuple[str, str]] = field(default_factory=list)  # (absorbed, kept)
    proposals: list[Proposal] = field(default_factory=list)


@runtime_checkable
class GraphStore(Protocol):
    def apply(self, extraction: Extraction, text: str = "", relation: Relation | None = None, *,
              attribution_reviews: dict | None = None) -> Placement:
        """Place one item. `relation` is its stored verdicts; without them the store relates it lexically."""
    def apply_override(self, override: Override) -> None: ...
    def items(self) -> Iterable[ItemNode]: ...
    def claims(self) -> Iterable[ClaimNode]: ...
    def people(self) -> Iterable[PersonNode]: ...
    def organizations(self) -> Iterable[OrgNode]: ...
    def affiliations(self) -> list[Affiliation]:
        """Person-to-organization ties, stated in items or found by enrichment."""
    def sources(self) -> Iterable[SourceNode]: ...
    def edges(self) -> Iterable[Edge]: ...
    def trust(self, person_id: str) -> float: ...
    def experts(self, at: datetime, subject_id: str | None = None, *,
                idea_ids: Iterable[str] | None = None, subject_title: str | None = None) -> list[dict]: ...
    def ideas(self) -> Iterable[IdeaNode]: ...
    def idea(self, idea_id: str) -> IdeaNode: ...
    def review(self, at: datetime) -> ReviewReport: ...
    def apply_review(self, report: ReviewReport) -> None: ...
    def clusters(self) -> Iterable[ClusterNode]: ...
    def topics(self) -> Iterable[TopicNode]: ...
    def links(self, level: str | None = None) -> list[Link]:
        """Links between clusters ("cluster") or topics ("topic"), heaviest first; both levels when None."""

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
