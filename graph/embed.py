"""Text similarity for candidates and clustering: one seam, two embedders.

HashedEmbedder is the offline default: word stems hashed into a fixed-size vector, so "hallucination" and
"hallucinate" meet without a model. NebiusEmbedder calls the Nebius embeddings endpoint when NEBIUS_EMBED_MODEL is
set; its vectors are cached in <vault>/.cache/embeddings.jsonl (a cache, never the truth), keyed by model and text.
"""
import hashlib
import json
import math
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Protocol

DIMENSIONS = 1024
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


def _unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else vector


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # vectors are unit length


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashedEmbedder:
    name = "hashed-stems-v1"

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

    def __init__(self, api_key: str, model: str, base_url: str, cache: Path | None = None, batch: int = 64):
        self.api_key, self.model, self.base_url, self.cache, self.batch = api_key, model, base_url, cache, batch
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
            data = _nebius_request(self.api_key, self.base_url, "/embeddings",
                                   {"model": self.model, "input": chunk}, 60)
            rows = sorted(data.get("data") or [], key=lambda r: r.get("index", 0))
            if len(rows) != len(chunk):
                raise ModelOutputError("The embeddings endpoint returned the wrong number of vectors.")
            fresh = {self._key(t): _unit([float(x) for x in r["embedding"]]) for t, r in zip(chunk, rows)}
            self._known.update(fresh)
            if self.cache is not None:
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                with self.cache.open("a", encoding="utf-8") as f:
                    for key, vector in fresh.items():
                        f.write(json.dumps({"model": self.model, "key": key, "vector": vector}) + "\n")
        return [self._known[self._key(t)] for t in texts]


_current: Embedder = HashedEmbedder()


def current() -> Embedder:
    return _current


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
