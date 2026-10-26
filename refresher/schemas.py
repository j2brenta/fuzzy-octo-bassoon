"""Shared contracts between channels, ingestion, the graph module and retrieval."""
import hashlib
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthorHint(_Model):
    """What the channel knows about a person. Extraction and the review job resolve it to a Person."""
    name: str | None = None
    handle: str | None = None
    email: str | None = None
    url: str | None = None


class Attachment(_Model):
    mime: str
    path: str  # relative to the raw store (or the seed corpus root)
    name: str | None = None


class RawItem(_Model):
    """One captured thing, as every channel adapter emits it. Everything after capture is channel-agnostic."""
    schema_version: int = 1
    channel: Literal["telegram", "email", "import", "app"]
    source_id: str  # unique within the channel: Telegram message id, Message-ID, seed:<stem>#<n>
    source_name: str | None = None  # chat, mailing list (List-Id), site
    author_hint: AuthorHint | None = None  # who wrote the content
    forwarded_by: AuthorHint | None = None  # who shared it with us
    captured_at: AwareDatetime
    published_at: AwareDatetime | None = None
    url: str | None = None
    # Input shape for routing (pipeline stage 2), not the triage kind (post, article, digest).
    kind: Literal["text", "link", "file", "image", "voice", "video"]
    text: str = ""
    attachments: list[Attachment] = []

    @property
    def content_hash(self) -> str:
        """Exact-duplicate key for capture. Near-duplicates are the relation check's job."""
        norm = " ".join(self.text.split()).casefold()
        return hashlib.sha256(f"{norm}\n{self.url or ''}".encode()).hexdigest()


# --- Extraction: the handoff from ingestion (Olga's processed/<item_id>.json) to the graph and KM ---
# These models read the provisional `processed_extraction` record. They ignore fields they don't use,
# so ingestion can add fields without breaking the readers.

Durability = Literal["foundation", "reference", "news", "ephemeral"]


class _Handoff(BaseModel):
    model_config = ConfigDict(extra="ignore")


def _as_author_hint(value):
    """Capture stores author_hint as a free-form dict and forwarded_by as a name string."""
    if isinstance(value, str):
        return {"name": value}
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if k in AuthorHint.model_fields}
    return value


class Span(_Handoff):
    item_id: str
    section_id: str
    start_char: int
    end_char: int


class Claim(_Handoff):
    claim_id: str
    statement: str
    kind: str  # fact, announcement, opinion, how-to, principle
    durability_class: Durability
    half_life_days: float
    confidence: float
    support_status: str | None = None
    evidence: Span | list[Span]

    @property
    def spans(self) -> list[Span]:
        return self.evidence if isinstance(self.evidence, list) else [self.evidence]


class Entity(_Handoff):
    entity_id: str
    name: str
    kind: Literal["person", "organization", "concept"]
    identity_status: str | None = None


class Source(_Handoff):
    channel: str
    source_id: str
    source_name: str | None = None
    message_id: str | None = None
    captured_at: AwareDatetime
    published_at: AwareDatetime | None = None
    author_hint: AuthorHint | None = None
    forwarded_by: AuthorHint | None = None
    url: str | None = None
    kind: str | None = None

    @field_validator("author_hint", "forwarded_by", mode="before")
    @classmethod
    def _hint(cls, value):
        return _as_author_hint(value)

    @property
    def event_time(self):
        """When it happened, not when we saw it."""
        return self.published_at or self.captured_at


class Triage(_Handoff):
    keep: bool
    kind: str
    durability_class: Durability
    split_digest: bool = False
    reason: str = ""


class Extraction(_Handoff):
    schema_version: int = 1
    stage: Literal["processed_extraction"]
    item_id: str
    raw_path: str | None = None
    source: Source
    sections: list[dict] = []
    links: list[dict] = []
    claims: list[Claim] = []
    entities: list[Entity] = []
    triage: Triage | None = None
    quality_status: str
    quality_gate_passed: bool


# --- Relations: the graph's verdict stage, relations/<item_id>.json, written once and replayed ---

RelationLabel = Literal["SAME", "UPDATES", "CONFLICTS", "ADDS_DETAIL", "NONE"]


class Verdict(_Model):
    """How one claim relates to the candidates it was shown. The label is symmetric: event time decides which
    claim supersedes which, and provenance decides SUPPORTS versus DUPLICATES."""
    claim_id: str
    subject: Literal["person", "organization", "world"] = "world"
    subject_name: str | None = None  # the person or organization a profile fact is about
    label: RelationLabel = "NONE"
    target_id: str | None = None  # a candidate claim id; None exactly when the label is NONE
    rationale: str = ""

    @model_validator(mode="after")
    def _consistent(self):
        if (self.label == "NONE") != (self.target_id is None):
            raise ValueError("target_id is required for a relation and must be empty for NONE")
        if (self.subject != "world") != bool(self.subject_name):
            raise ValueError("subject_name is required for a person or organization subject, and only then")
        return self


class Relation(_Model):
    stage: Literal["relations"] = "relations"
    item_id: str
    relator: str  # lexical | model
    model: str | None = None
    prompt_version: str
    candidates: dict[str, list[str]]  # claim id -> candidate claim ids, as the relator saw them
    verdicts: list[Verdict]

    @model_validator(mode="after")
    def _targets_were_candidates(self):
        for v in self.verdicts:
            if v.claim_id not in self.candidates:
                raise ValueError(f"verdict for {v.claim_id} has no candidate list")
            if v.target_id is not None and v.target_id not in self.candidates[v.claim_id]:
                raise ValueError(f"verdict for {v.claim_id} targets {v.target_id}, which was not a candidate")
        if len({v.claim_id for v in self.verdicts}) != len(self.verdicts):
            raise ValueError("one verdict per claim")
        return self

    def matches(self, extraction: "Extraction") -> bool:
        """False when ingestion re-extracted the item since: the verdicts are stale and need judging again."""
        return self.item_id == extraction.item_id and \
            {v.claim_id for v in self.verdicts} == {c.claim_id for c in extraction.claims}


class Override(_Model):
    """The user's word, stored in the vault so it survives a rebuild. Beats model judgements."""
    target: Literal["claim", "item", "person"]
    target_id: str
    durability_class: Durability | None = None  # claim or item: pins the half-life class
    trust: float | None = Field(default=None, ge=0, le=1)  # person: pins trust
    vote: Literal[-1, 1] | None = None  # claim or person: thumbs down or up
    at: AwareDatetime | None = None
    note: str | None = None
