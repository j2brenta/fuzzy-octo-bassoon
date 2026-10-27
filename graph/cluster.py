"""Grouping Ideas into clusters and topics, cheaply: vectors in, groups out; no model call per Idea.

Two levels from one k-nearest-neighbour graph. Clusters keep its edges at the tight cluster_link: any group of 2+
Ideas, from any number of sources, recomputed with the graph. Topics keep edges at the looser topic_link and never
split a cluster (components with `joined`); a big enough group from 2+ sources becomes a topic proposal, named by one
model call or by its most shared words (title). At ingest an Idea joins every accepted topic whose centroid is close
(attach_all), up to MAX_TOPICS. Similarities are matrix products in blocks, so thousands of Ideas stay fast.
"""
from collections import Counter

import numpy as np

from graph.embed import STOP, stem, thresholds, words

K = 5  # neighbours per Idea in the k-NN graph
CLUSTER_MIN_IDEAS = 2
TOPIC_MIN_IDEAS = 3
TOPIC_MIN_SOURCES = 2
MAX_TOPICS = 3  # topics one Idea can sit in
BLOCK = 1024  # rows per matrix product, to bound memory


def matrix(vectors) -> np.ndarray:
    return np.asarray(vectors, dtype=np.float64).reshape(len(vectors), -1) if len(vectors) else np.zeros((0, 1))


def centroid(vectors) -> list[float]:
    total = matrix(vectors).sum(axis=0)
    norm = float(np.linalg.norm(total))
    return (total / norm).tolist() if norm else total.tolist()


def attach_all(vector, centroids: dict[str, list[float]], threshold: float | None = None,
               limit: int = MAX_TOPICS) -> list[str]:
    """Topics at or above the threshold (the embedder's topic_attach), closest first; ties go to the smaller id."""
    threshold = thresholds().topic_attach if threshold is None else threshold
    if not centroids:
        return []
    ids = sorted(centroids)
    sims = matrix([centroids[t] for t in ids]) @ np.asarray(vector, dtype=np.float64)
    return [t for s, t in sorted(zip((-sims).tolist(), ids)) if -s >= threshold][:limit]


def attach(vector, centroids: dict[str, list[float]], threshold: float | None = None) -> str | None:
    """The closest topic at or above the threshold; ties go to the smaller id."""
    best = attach_all(vector, centroids, threshold, limit=1)
    return best[0] if best else None


def nearest(vectors, k: int = K) -> list[tuple[int, int, float]]:
    """(i, j, cosine) for each row's k nearest other rows; ties go to the smaller j."""
    m = matrix(vectors)
    out = []
    for start in range(0, len(m), BLOCK):
        sims = m[start:start + BLOCK] @ m.T
        for row, i in enumerate(range(start, min(start + BLOCK, len(m)))):
            sims[row, i] = -np.inf
            order = np.argsort(-sims[row], kind="stable")[:k]
            out += [(i, int(j), float(sims[row, j])) for j in order if j != i and np.isfinite(sims[row, j])]
    return out


def pairs_above(vectors, threshold: float) -> list[tuple[int, int, float]]:
    """(i, j, cosine) with i < j for every pair at or above the threshold."""
    m = matrix(vectors)
    out = []
    for start in range(0, len(m), BLOCK):
        sims = m[start:start + BLOCK] @ m.T
        rows, cols = np.nonzero(sims >= threshold)
        out += [(start + int(r), int(c), float(sims[r, c])) for r, c in zip(rows, cols) if start + r < c]
    return sorted(out)


def components(ids: list[str], vectors, link: float | None = None, k: int = K, joined=(),
               near: list[tuple[int, int, float]] | None = None) -> list[list[str]]:
    """Connected components of the k-nearest-neighbour graph, keeping edges at or above `link` (the embedder's
    topic_link). Ids in one `joined` group always end up together; `near` reuses a nearest() result."""
    link = thresholds().topic_link if link is None else link
    parent = list(range(len(ids)))
    index = {idea_id: n for n, idea_id in enumerate(ids)}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, j, sim in nearest(vectors, k) if near is None else near:
        if sim >= link:
            parent[find(i)] = find(j)
    for group in joined:
        members = [index[g] for g in group if g in index]
        for other in members[1:]:
            parent[find(other)] = find(members[0])
    groups: dict[int, list[str]] = {}
    for i, idea_id in enumerate(ids):
        groups.setdefault(find(i), []).append(idea_id)
    return sorted((sorted(g) for g in groups.values()), key=lambda g: (-len(g), g[0]))


def central(ids: list[str], vectors, center) -> str:
    """The id whose vector is closest to the centroid; ties go to the smaller id."""
    sims = matrix(vectors) @ np.asarray(center, dtype=np.float64)
    return min(zip((-sims).tolist(), ids))[1]


def shorten(text: str, limit: int = 70) -> str:
    """At a word boundary, with an ellipsis when cut."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:")
    return (cut or text[:limit]) + "…"


def title(statements: list[str], limit: int = 4) -> str:
    """The words most statements share, in their usual form: the lexical name until a model names the topic."""
    seen: Counter = Counter()
    forms: dict[str, Counter] = {}
    for s in statements:
        for w in set(words(s)):
            if w in STOP or len(w) < 3 or w.isdigit():
                continue
            seen[stem(w)] += 1
            forms.setdefault(stem(w), Counter())[w] += 1
    top = [st for st, n in sorted(seen.items(), key=lambda kv: (-kv[1], kv[0])) if n >= 2][:limit]
    return " · ".join(forms[st].most_common(1)[0][0] for st in top).capitalize() or "Unnamed topic"
