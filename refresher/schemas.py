"""Shared contracts between channels, ingestion, the graph module and retrieval."""
import hashlib
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict


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

