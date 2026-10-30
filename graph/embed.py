"""Text similarity for candidates and clustering: one seam, two embedders.

HashedEmbedder is the offline default: word stems hashed into a fixed-size vector, so "hallucination" and
"hallucinate" meet without a model. NebiusEmbedder calls the Nebius embeddings endpoint when NEBIUS_EMBED_MODEL is
set; its vectors are cached in <vault>/.cache/embeddings.jsonl (a cache, never the truth), keyed by model and text.
"""
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

DIMENSIONS = 512
STOP = frozenset("""a an and are as at be by for from has have in is it its of on or that the this to was were will
with not no but if than then so can may also into about over under more most less their there these those which who
what when where how all any each other such only own same very just""".split())
SUFFIXES = ("ization", "isation", "ations", "ation", "ating", "ities", "ity", "ions", "ion", "ings", "ing", "ments",
            "ment", "ness", "ates", "ated", "ate", "ers", "er", "ies", "ied", "es", "ed", "ly", "s")


WORD = re.compile(r"\w+", re.UNICODE)


def words(text: str) -> list[str]:
    return WORD.findall(text.casefold())


def stem(word: str) -> str:
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


@dataclass(frozen=True)
class Thresholds:
    """Cosines mean different things for different embedders, so each carries its own cut-offs."""
    candidate: float  # below this a claim is not a relation candidate
    recheck: float  # a new candidate this close makes stored verdicts worth judging again
    topic_link: float  # edge between two unsorted Ideas when clustering topics
    topic_attach: float  # a new Idea joins an accepted topic at this cosine to its centroid
    idea_merge: float  # two Ideas this close are proposed for a merge (the model confirms when there is one)
    cluster_link: float  # edge between two Ideas in a cluster: tighter than topic_link, so topics hold whole clusters
    topic_neighbour: float  # two clusters (or topics) whose centroids are this close are linked without a relation


HASHED = Thresholds(candidate=0.1, recheck=0.5, topic_link=0.3, topic_attach=0.35, idea_merge=0.75,
                    cluster_link=0.4, topic_neighbour=0.12)
# Measured on Qwen/Qwen3-Embedding-8B, 26 October: unrelated statements 0.30-0.46, same topic 0.73-0.93, a paraphrase
# 0.98, but two different assertions on one topic 0.91 and 14:00 against 16:00 0.99, so merges need the model's check.
# 27 October, three articles (German vocational training, Swiss apprenticeships, hallucination detection): Ideas of
# one article 0.30-0.69 median; at 0.7 each article splits into clusters of 2-7, at 0.6 the two neighbouring articles
# join and the third stays apart. Cluster centroids on one subject 0.61-0.71, across subjects at most 0.43.
# 28 October, the live Telegram vault (54 English Ideas from 4 Russian posts): median nearest neighbour 0.57;
# Ideas grouped at 0.70: 8, at 0.60: 24 in 7 groups of 2-10, at 0.55: 35 in 6 groups, at 0.50 one chain of 33.
QWEN3 = Thresholds(candidate=0.5, recheck=0.8, topic_link=0.55, topic_attach=0.6, idea_merge=0.95,
                   cluster_link=0.6, topic_neighbour=0.55)


def _unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # vectors are unit length


class Embedder(Protocol):
    name: str
    thresholds: Thresholds

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashedEmbedder:
    name = "hashed-stems-v1"
    thresholds = HASHED

    def embed(self, texts):
        return [self._one(t) for t in texts]

    @staticmethod
    @lru_cache(maxsize=20000)
    def _vector(text: str) -> tuple[float, ...]:
        vector = [0.0] * DIMENSIONS
        for w in words(text):
            if w in STOP or len(w) < 2:
                continue
            digest = hashlib.blake2b(stem(w).encode(), digest_size=8).digest()
            vector[int.from_bytes(digest[:4], "big") % DIMENSIONS] += 1.0 if digest[4] & 1 else -1.0
        return tuple(_unit(vector))

    def _one(self, text: str) -> list[float]:
        return list(self._vector(text))


class NebiusEmbedder:
    """OpenAI-compatible /embeddings on Nebius, with a disk cache so rebuilds stay offline after the first run."""

    def __init__(self, api_key: str, model: str, base_url: str, cache: Path | None = None, batch: int = 64,
                 thresholds: Thresholds = QWEN3):
        self.api_key, self.model, self.base_url, self.cache, self.batch = api_key, model, base_url, cache, batch
        self.thresholds = thresholds  # calibrated for Qwen3-Embedding-8B; another model needs its own
        self.name = f"nebius:{model}"
        self._known: dict[str, list[float]] = {}
        if cache is not None and cache.exists():
            for line in cache.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                if row.get("model") == model:
                    self._known[row["key"]] = row["vector"]

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:32]

    def embed(self, texts):
        from engine.llm import ModelOutputError, _nebius_request
        todo = list(dict.fromkeys(t for t in texts if self._key(t) not in self._known))
        for start in range(0, len(todo), self.batch):
            chunk = todo[start:start + self.batch]
            started = time.monotonic()
            data = _nebius_request(self.api_key, self.base_url, "/embeddings",
                                   {"model": self.model, "input": chunk}, getattr(_wait, "seconds", None) or 60)
            tokens = (data.get("usage") or {}).get("total_tokens") if isinstance(data.get("usage"), dict) else None
            logger.info("%s: embedded %d texts (%d cached), %s tokens, %.1f s", self.model, len(chunk),
                        len(texts) - len(todo), f"{tokens:,}" if isinstance(tokens, int) else "?",
                        time.monotonic() - started)
            rows = sorted(data.get("data") or [], key=lambda r: r.get("index", 0))
            if len(rows) != len(chunk):
                raise ModelOutputError("The embeddings endpoint returned the wrong number of vectors.")
            fresh = {self._key(t): _unit([float(x) for x in r["embedding"]]) for t, r in zip(chunk, rows)}
            self._known.update(fresh)
            if self.cache is not None:
                try:
                    self.cache.parent.mkdir(parents=True, exist_ok=True)
                    with self.cache.open("a", encoding="utf-8") as f:
                        for key, vector in fresh.items():
                            f.write(json.dumps({"model": self.model, "key": key, "vector": vector}) + "\n")
                except OSError as error:  # a read-only vault (the MCP server): keep the vectors in memory only
                    logger.warning("embeddings cache not written (%s); keeping vectors in memory", type(error).__name__)
                    self.cache = None
        return [self._known[self._key(t)] for t in texts]


_current: Embedder = HashedEmbedder()
_wait = threading.local()  # this thread's wait for the embeddings service, when shorter than a rebuild's


@contextmanager
def waiting(seconds: float):
    """Wait at most this long for the embeddings service inside the block: a reader is waiting on a page."""
    before = getattr(_wait, "seconds", None)
    _wait.seconds = seconds
    try:
        yield
    finally:
        _wait.seconds = before


def current() -> Embedder:
    return _current


def thresholds() -> Thresholds:
    return _current.thresholds


def use(embedder: Embedder) -> None:
    """Swap the process-wide embedder (the CLI does this when NEBIUS_EMBED_MODEL is set)."""
    global _current
    _current = embedder
    _similar.cache_clear()


def from_environment(vault: Path | None = None, env=None) -> Embedder:
    """NebiusEmbedder when NEBIUS_API_KEY and NEBIUS_EMBED_MODEL are set, else the offline HashedEmbedder."""
    from engine.llm import NEBIUS_BASE_URL
    env = os.environ if env is None else env
    key, model = (env.get("NEBIUS_API_KEY") or "").strip(), (env.get("NEBIUS_EMBED_MODEL") or "").strip()
    if not (key and model):
        return HashedEmbedder()
    cache = vault / ".cache" / "embeddings.jsonl" if vault is not None else None
    return NebiusEmbedder(key, model, (env.get("NEBIUS_BASE_URL") or "").strip() or NEBIUS_BASE_URL, cache)


@lru_cache(maxsize=200000)
def _similar(a: str, b: str) -> float:
    va, vb = _current.embed([a, b])
    return cosine(va, vb)


def similarity(a: str, b: str) -> float:
    return _similar(*sorted((a, b)))


def vectors(texts: list[str]) -> list[list[float]]:
    return _current.embed(texts)
