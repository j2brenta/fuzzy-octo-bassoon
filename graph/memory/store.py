"""MemoryStore: the in-memory graph, one class made of its parts (see graph/memory/__init__.py)."""
from graph.store import (Affiliation, ClaimNode, ClusterNode, Edge, IdeaNode, ItemNode, Link, OrgNode, PersonNode,
                         SourceNode, TopicNode)
from refresher.schemas import Verdict
from graph.memory.placing import Placing
from graph.memory.decisions import Decisions
from graph.memory.resolution import Resolution
from graph.memory.grouping import Grouping
from graph.memory.profiles import Profiles
from graph.memory.reads import Reads
from graph.memory.review import Review


class MemoryStore(Placing, Decisions, Resolution, Grouping, Profiles, Reads, Review):
    """The graph replayed from processed records. Its parts: placing an item, replaying decisions, resolving
    verdicts, grouping Ideas, profiles, reads and the hourly review. Every part reads the state set here."""

    def __init__(self, include_unreviewed: bool = False):
        # Place items that failed or have not finished ingestion's quality gate, marked by review_status.
        # Items triage chose not to keep become hidden stubs: out of every listing, but their claims can be
        # superseded, so a correction has something to replace.
        self.include_unreviewed = include_unreviewed
        self._items: dict[str, ItemNode] = {}
        self._claims: dict[str, ClaimNode] = {}
        self._ideas: dict[str, IdeaNode] = {}
        self._people: dict[str, PersonNode] = {}
        self._sources: dict[str, SourceNode] = {}
        self._orgs: dict[str, OrgNode] = {}
        self._affiliations: list[Affiliation] = []  # stated in items; enrichment ones are added after replay
        self._entities: dict[str, frozenset[str]] = {}  # item id -> casefolded entity names, for candidates
        self._verdicts: dict[str, Verdict] = {}  # claim id -> verdict
        self._edges: list[Edge] = []  # what an item brings: source, author, mentions, reposts, spans
        self._relations: list[Edge] = []  # what the verdicts imply; recomputed by _resolve
        self._firm: set[str] = set()  # idea ids the review firmed up
        # What accepted (or rejected) proposals say; graph/proposals.py decides which apply.
        self._merges: list[tuple[str, str]] = []  # claim pairs whose Ideas the user merged
        self._suppressed: set[frozenset] = set()  # claim pairs whose relation the user rejected
        self._topic_specs: dict[str, tuple[str, list[str]]] = {}  # topic id -> (title, claim ids)
        self._topics: dict[str, TopicNode] = {}
        self._clusters: dict[str, ClusterNode] = {}
        self._cluster_key = None  # what the clusters were computed from; unchanged Ideas skip the work
        self._links: list[Link] = []
        self._cluster_names: dict[str, str] = {}  # cluster fingerprint -> name a model gave it
        self._themes: dict[str, dict] = {}  # canonical claim id -> where a model placed that Idea (use_themes)
        self._grouped = True  # clusters, topics and links match the Ideas; built on first read (_groups)
        self._vectors: dict[tuple[str, str], list[float]] = {}  # (embedder, Idea title) -> vector
        self._aliases: dict[str, str] = {}  # absorbed person id -> kept person id
        self._splits: dict[str, list[str]] = {}  # a record naming several people -> their names, as accepted
        self.proposals: dict = {}  # proposal id -> ProposalState, filled by vault.replay
        self.held_back: dict[str, str] = {}  # item id -> reason
        # SAME verdicts of held-back claims: a verdict judged with unreviewed items in the pool may target one,
        # and a strict rebuild follows this to the placed claim it repeats.
        self._held_same: dict[str, str] = {}
        self._shingle_cache: dict[str, set] = {}  # item id -> word 3-grams of its text, for the repost check
        self._deferred = self._dirty = False  # see deferred()
        self._placed_hash = 0  # XOR of hashes of placed item ids: an order-free fingerprint of what is placed
