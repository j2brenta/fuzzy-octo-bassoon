"""Grouping Ideas into topics, cheaply: vectors in, groups out; no model call per Idea.

At ingest a new Idea joins a known topic when it is close to the topic's centroid (attach). The hourly review links
each unsorted Idea to its k nearest neighbours above a threshold and takes the connected components (components);
each big enough component becomes a topic proposal, named by one model call or by its most shared words (title).
"""
from collections import Counter

from graph.embed import STOP, cosine, stem, thresholds, words

K = 5  # neighbours per Idea in the review's graph
TOPIC_MIN_IDEAS = 3
TOPIC_MIN_SOURCES = 2


def centroid(vectors: list[list[float]]) -> list[float]:
    total = [sum(xs) for xs in zip(*vectors)]
    norm = sum(x * x for x in total) ** 0.5
    return [x / norm for x in total] if norm else total


def attach(vector: list[float], centroids: dict[str, list[float]], threshold: float | None = None) -> str | None:
    """The closest topic at or above the threshold (the embedder's topic_attach); ties go to the smaller id."""
    threshold = thresholds().topic_attach if threshold is None else threshold
    scored = sorted((-cosine(vector, c), tid) for tid, c in centroids.items())
    return scored[0][1] if scored and -scored[0][0] >= threshold else None


def components(ids: list[str], vectors: list[list[float]], link: float | None = None, k: int = K) -> list[list[str]]:
    """Connected components of the k-nearest-neighbour graph, keeping edges at or above `link` (the embedder's
    topic_link)."""
    link = thresholds().topic_link if link is None else link
    parent = list(range(len(ids)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, v in enumerate(vectors):
        near = sorted(((cosine(v, w), j) for j, w in enumerate(vectors) if j != i), reverse=True)[:k]
        for sim, j in near:
            if sim >= link:
                parent[find(i)] = find(j)
    groups: dict[int, list[str]] = {}
    for i, idea_id in enumerate(ids):
        groups.setdefault(find(i), []).append(idea_id)
    return sorted((sorted(g) for g in groups.values()), key=lambda g: (-len(g), g[0]))


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
