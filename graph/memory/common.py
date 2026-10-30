"""The constants and helpers the parts of MemoryStore share: ids, names, titles, shingles."""
import hashlib
import re
import unicodedata

from graph import relate
from engine.entities import eponym_only
from graph.names import core as name_core, display_name
from graph.relate import words as _words
from graph.store import PersonNode

REPOST_SIMILARITY = 0.8  # item text, word 3-gram Jaccard
REPOST_CLAIM_SIMILARITY = 0.6  # word-set Jaccard: a repost's claim and its match in the original
REVIEW_CLAIM_MATCH = 0.5  # word-set Jaccard for the review's item-duplicate check
REVIEW_DUPLICATE_SHARE = 0.7  # an item whose claims match one other item this much is a duplicate of it
REVIEW_MIN_CLAIMS = 3  # fewer claims are too few to call an item a duplicate; reposts and relations cover those
CONFIRM_DURABILITY = {"reference", "foundation"}  # a SUPERSEDES or CONTRADICTS onto these asks the user to confirm
WORD = relate.WORD
DURABILITY_RANK = {d: n for n, d in enumerate(("ephemeral", "news", "reference", "foundation"))}
RELATION_TYPES = {"DUPLICATES", "SUPPORTS", "SUPERSEDES", "CONTRADICTS", "ELABORATES"}
EDGE_FOR = {"SAME": "SUPPORTS", "UPDATES": "SUPERSEDES", "CONFLICTS": "CONTRADICTS", "ADDS_DETAIL": "ELABORATES"}
# Claim relations that link clusters and topics. In practice ELABORATES: the others join their claims into one Idea.
LINK_RELATIONS = {"SUPPORTS", "SUPERSEDES", "CONTRADICTS", "ELABORATES"}


def cluster_fingerprint(ideas) -> str:
    """Which Ideas a cluster holds, by their canonical claims: a stored name applies while this matches."""
    return hashlib.blake2b("\n".join(sorted(i.canonical_claim_id for i in ideas)).encode(), digest_size=8).hexdigest()


def person_id(name: str) -> str:
    # "Last; First" is the same person as "First Last".
    name = display_name(name)
    # Accents fold to ASCII; a name with no Latin letters (Cyrillic) keeps its own words, else all such names share "person:".
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "person:" + "-".join(WORD.findall(folded.casefold()) or WORD.findall(name.casefold()))


def _eponym(name: str | None, text: str) -> bool:
    """A name written only inside a law or theorem in this text ("третьего закона Ньютона"): no person here. A word
    may carry a case ending, so "Ньютон" finds "Ньютона"; a name not found at all is kept."""
    if not name or not name.strip():
        return False
    # Every word may be inflected: "Исаак Ньютон" finds "Исаака Ньютона".
    pattern = r"\b" + r"\w*\s+".join(re.escape(w) for w in name.split()) + r"\w*"
    return eponym_only(text, (m.span() for m in re.finditer(pattern, text)))


def _name_rank(p: PersonNode):
    """Which name a merge keeps, last: no affiliation in brackets ("Herman Daly (University of Maryland)"), more
    words, then no stray punctuation ("Krestnikov; Konstantin"), then the longer (a full first name over a short
    form), then the id."""
    name, bracket = name_core(p.name)
    return (bracket is None, len(_words(name)), -len(re.findall(r"[^\w\s.'-]", p.name)), len(p.name), p.person_id)


def _profile_pages(p: PersonNode) -> set[str]:
    """The pages enrichment cited for who they are: their role, affiliation and domains."""
    return {f["url"] for f in [p.role, p.affiliation, *p.domains] if isinstance(f, dict) and f.get("url")}


def org_id(name: str) -> str:
    return "org:" + "-".join(_words(name))


def _id_hash(item_id: str) -> int:
    return int.from_bytes(hashlib.blake2b(item_id.encode(), digest_size=8).digest(), "big")


def _shingles(text: str, n: int = 3) -> set[tuple[str, ...]]:
    w = _words(text)
    return {tuple(w[i:i + n]) for i in range(max(1, len(w) - n + 1))}


# Telegram Desktop "Copy" header, kept in raw text by capture: "Kirill R, [7 Oct 2026 at 12:13:38]:"
_TG_HEADER = re.compile(r"^.+, \[\d{1,2} \w{3} \d{4} at [\d:]+\]:$")


# Headers ingestion writes above text it read from a picture or PDF page: never a title.
_READING_HEADERS = ("Visible text (automatic OCR):", "Visual description (model-generated):")
_TITLE_FIELD = re.compile(r"^Title:\s*(.+)$")  # arXiv and similar pages state it under a breadcrumb


def _title(text: str, fallback: str) -> str:
    lines = [line.strip().lstrip("#").strip() for line in text.splitlines()]
    lines = [line for line in lines if line and not _TG_HEADER.match(line) and line not in _READING_HEADERS]
    for line in lines[:5]:
        stated = _TITLE_FIELD.match(line)
        if stated:
            return stated.group(1).strip()[:80]
    return lines[0][:80] if lines else fallback
