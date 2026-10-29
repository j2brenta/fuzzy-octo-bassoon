"""The Context Engine: what has survived to now on a subject, as major ideas with a base to read.

A major idea is a group of theses (refresher/theses.py) that the thesis relation stage (refresher/thesis_relate.py)
tied together across sources: the same point, a point built on another, a newer answer to the same question. Inside
it every source has a role:

    base        the source to read in depth: pointed at by the others (linked, captured from, its writer named),
                then the most original stance, then the earliest, then the most claims
    builder     builds on the base, or restates it with evidence of its own
    rewrite     restates it and adds nothing (a retelling, a digest), or reposts a member's text
    superseded  an older answer a member updated, or a thesis whose claims a newer claim replaced
    adjacent    its thesis lives elsewhere, but some of its claims bear on this idea: listed with the facts it lends

Relevance is the cosine of a thesis to the subject. A phrase's vector is the phrase plus the mean of its three nearest
theses: alone, a short phrase sits near hub statements (measured on Qwen3, 29 October: "wealth" drew a thesis about
search tools at 0.47 against the wealth post's 0.59); expanded, a subject's own theses score 0.65-0.79 and others
under 0.6. A thesis is in scope from the higher of FLOOR and SPAN under the best one. An idea is as relevant as its
most relevant member, and in scope when one current member is.

Embeddings never merge theses: related pairs scored 0.60-0.78 and unrelated ones on one subject up to 0.56, so only
the model's relations group them.

Ranking: relevance x freshness x independent support, where freshness is the newest current claim's
(refresher/scoring.py) and support counts distinct writers or channels among base and builders. Pure over the
replayed store, cached per vault state by the app.
"""
import math
from collections import defaultdict
from datetime import datetime

from graph import cluster as grouping
from graph import embed
from refresher import dossier, scoring, summarize, theses, thesis_relate

FLOOR = {"qwen": 0.45, "hashed": 0.08}
SPAN = {"qwen": 0.2, "hashed": 0.3}
EXPAND = 3  # nearest theses averaged into a phrase's vector
EXPAND_SPAN = {"qwen": 0.1, "hashed": 0.15}  # and only those this close to the phrase's best match
ADJACENT = {"qwen": 0.6, "hashed": 0.4}  # a claim this close to an idea lends it a fact,
ADJACENT_SPAN = 0.1  # if it is also on the subject: no further under the floor than this
FACTS_PER_SOURCE, ADJACENT_SOURCES = 3, 5
MAJOR_MAX = 6  # ideas a brief writes about; the rest are listed
STANCE_RANK = {"original": 0, "commentary": 1, "summary": 2, "retelling": 3}
ROLE_RANK = {"base": 0, "builder": 1, "rewrite": 2, "superseded": 3}
GROUPING = ("SAME", "BUILDS_ON", "UPDATES")
WORDS_PER_MINUTE = 220


def _family() -> str:
    return "hashed" if embed.thresholds() is embed.HASHED else "qwen"


def resolve(store, pool: thesis_relate.Pool, key: str | None, query: str | None) -> dict | None:
    """The subject: {kind, id, title, vector}. A topic or cluster is its centroid; a phrase is expanded by its nearest
    theses."""
    if key:
        scope = dossier.resolve(store, key=key)
        if scope is None or not scope.center:
            return None
        return {"kind": scope.kind, "id": scope.key, "title": scope.title, "vector": list(scope.center)}
    if not query or not query.strip() or not pool.rows:
        return None
    phrase = embed.vectors([query.strip()])[0]
    ranked = sorted(((embed.cosine(v, phrase), n) for n, v in enumerate(pool.vector.values())), reverse=True)
    vectors = list(pool.vector.values())
    nearest = [vectors[n] for c, n in ranked[:EXPAND] if c >= ranked[0][0] - EXPAND_SPAN[_family()]]
    return {"kind": "query", "id": query.strip(), "title": query.strip(),
            "vector": grouping.centroid([phrase, grouping.centroid(nearest)])}


class _Union:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def _later(a: str, b: str, when: dict, points_at: dict) -> bool:
    """Is item a later than item b: a source that links or names another is later by definition, else event time."""
    if b in points_at.get(a, ()):
        return True
    if a in points_at.get(b, ()):
        return False
    return (when[a], a) > (when[b], b)


def _author(store, item) -> str | None:
    if item.author_id:
        try:
            return store.person(item.author_id).name
        except KeyError:
            pass
    return item.source.author_hint.name if item.source.author_hint else None


def build(store, key: str | None = None, query: str | None = None, at: datetime | None = None,
          pool: thesis_relate.Pool | None = None) -> dict | None:
    """The context on a subject: ranked major ideas with roles, history and lent facts, and the sources they cite."""
    from datetime import timezone
    at = at or datetime.now(timezone.utc)
    pool = pool or thesis_relate.Pool(store)
    subject = resolve(store, pool, key, query)
    if subject is None:
        return None
    family = _family()
    items = {i.item_id: i for i in store.items()}
    of_item = {i: rows for i, rows in pool.of_item.items() if i in items}
    thesis = {t["id"]: (i, t) for i, rows in of_item.items() for t in rows}
    when = {i: items[i].source.event_time for i in items}
    relevance = {t: max(0.0, embed.cosine(pool.vector[t], subject["vector"])) for t in thesis}
    floor = max(FLOOR[family], max(relevance.values(), default=0.0) - SPAN[family])
    edges = thesis_relate.edges(getattr(store, "thesis_relations", {}) or {}, of_item)

    # Groups and supersession.
    union = _Union()
    for t in thesis:
        union.find(t)
    superseded: dict[str, dict] = {}
    for e in edges:
        a, b = e["thesis"], e["target"]
        if e["label"] in GROUPING:
            union.join(a, b)
        if e["label"] == "UPDATES":
            ia, ib = thesis[a][0], thesis[b][0]
            new, old = (a, b) if _later(ia, ib, when, pool.points_at) else (b, a)
            superseded[old] = {"by": new, "rationale": e["rationale"]}
    claims = {c.claim.claim_id: c for c in store.claims()}
    thesis_of_claim = {c: t for t, (_, row) in thesis.items() for c in row["claims"]}
    for t, (i, row) in thesis.items():  # a thesis whose every claim a newer claim replaced is replaced too
        own = [claims[c] for c in row["claims"] if c in claims]
        if t not in superseded and own and all(c.superseded_by for c in own):
            by = thesis_of_claim.get(own[0].superseded_by)
            if by and by != t:
                union.join(t, by)
                superseded[t] = {"by": by, "rationale": "Its claims were superseded by newer ones."}

    def fresh(t: str) -> float:
        i, row = thesis[t]
        own = [claims[c] for c in row["claims"] if c in claims]
        if own:
            return max(scoring.freshness(c.half_life_days, c.clock_start, at, superseded=bool(c.superseded_by))
                       for c in own)
        half = scoring.HALF_LIFE_DAYS.get(row.get("durability") or items[i].durability or "reference", 180)
        return scoring.freshness(half, when[i], at)

    groups: dict[str, list[str]] = defaultdict(list)
    for t in sorted(thesis):
        groups[union.find(t)].append(t)
    duplicates: dict[str, list[str]] = defaultdict(list)  # original item -> its reposts
    for i, item in items.items():
        if item.duplicate_of:
            duplicates[item.duplicate_of].append(i)

    edge_of: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        edge_of[e["thesis"]].append(e)
        edge_of[e["target"]].append(e)

    ideas = []
    for root, members in groups.items():
        current = [t for t in members if t not in superseded]
        if not current:
            continue
        rel = max(relevance[t] for t in members)
        member_items = {thesis[t][0] for t in members}
        pointed = {t: sum(1 for j in member_items if j != thesis[t][0] and thesis[t][0] in pool.points_at.get(j, ()))
                   for t in current}
        base = min(current, key=lambda t: (-pointed[t], STANCE_RANK.get(thesis[t][1]["stance"], 2),
                                           when[thesis[t][0]], -len(thesis[t][1]["claims"]), t))
        roles = {}
        for t in members:
            if t in superseded:
                roles[t] = "superseded"
            elif t == base:
                roles[t] = "base"
            elif thesis[t][0] == thesis[base][0]:
                roles[t] = "base"  # another thesis of the base source
            else:
                # The judge of a SAME pair said whether its own thesis adds to the other's; from the other side,
                # the stance stands in for it.
                mine = [e for e in edge_of[t] if e["thesis"] == t and e["label"] == "SAME"]
                same = mine or [e for e in edge_of[t] if e["target"] == t and e["label"] == "SAME"]
                adds = any(e["adds"] for e in mine) if mine else thesis[t][1]["stance"] in ("original", "commentary")
                builds = any(e["label"] in ("BUILDS_ON", "UPDATES") for e in edge_of[t])
                roles[t] = "builder" if builds or (same and adds) else "rewrite" if same else \
                    "rewrite" if thesis[t][1]["stance"] == "retelling" else "builder"
        sources: dict[str, str] = {}
        for t in members:
            i = thesis[t][0]
            if i not in sources or ROLE_RANK[roles[t]] < ROLE_RANK[sources[i]]:
                sources[i] = roles[t]
        for i in list(sources):
            for j in duplicates.get(i, []):
                sources.setdefault(j, "rewrite")
        writers = {_author(store, items[i]) or items[i].source.source_name or i
                   for i, r in sources.items() if r in ("base", "builder")}
        freshness = max(fresh(t) for t in current)
        support = len(writers)
        score = rel * (0.5 + 0.5 * freshness) * (1 + 0.25 * math.log(support)) if support else 0.0
        history = []
        for t in sorted(members, key=lambda t: (when[thesis[t][0]], t)):
            earlier = [e for e in edge_of[t] if (e["target"] if e["thesis"] == t else e["thesis"]) in members
                       and _later(thesis[t][0], thesis[e["target"] if e["thesis"] == t else e["thesis"]][0],
                                  when, pool.points_at)]
            step = "first stated"
            if earlier:
                e = earlier[0]
                step = {"SAME": "restated" if roles[t] != "rewrite" else "retold", "BUILDS_ON": "built on",
                        "UPDATES": "updated", "CONFLICTS": "contradicted"}[e["label"]]
            history.append({"thesis": t, "item": thesis[t][0], "when": when[thesis[t][0]].isoformat(),
                            "statement": thesis[t][1]["statement"], "step": step, "role": roles[t],
                            "rationale": earlier[0]["rationale"] if earlier else ""})
        previously = [{"old": t, "old_item": thesis[t][0], "old_statement": thesis[t][1]["statement"],
                       "old_when": when[thesis[t][0]].isoformat(), "new": superseded[t]["by"],
                       "new_item": thesis[superseded[t]["by"]][0],
                       "new_statement": thesis[superseded[t]["by"]][1]["statement"],
                       "durability": thesis[t][1].get("durability"), "rationale": superseded[t]["rationale"]}
                      for t in members if t in superseded]
        ideas.append({"id": root, "title": thesis[base][1]["statement"], "base": thesis[base][0], "base_thesis": base,
                      "relevance": round(rel, 4), "in_scope": any(relevance[t] >= floor for t in current),
                      "freshness": round(freshness, 4), "support": support, "score": round(score, 4),
                      "members": [{"thesis": t, "item": thesis[t][0], "role": roles[t],
                                   "stance": thesis[t][1]["stance"], "statement": thesis[t][1]["statement"],
                                   "claims": thesis[t][1]["claims"], "relevance": round(relevance[t], 4)}
                                  for t in sorted(members, key=lambda t: (ROLE_RANK[roles[t]], when[thesis[t][0]], t))],
                      "sources": sources, "history": history, "previously": previously,
                      "conflicts": [{"thesis": e["thesis"], "target": e["target"], "rationale": e["rationale"]}
                                    for e in edges if e["label"] == "CONFLICTS" and (e["thesis"] in members or e["target"] in members)]})
    ideas.sort(key=lambda x: (not x["in_scope"], -x["score"], x["id"]))
    scoped = [x for x in ideas if x["in_scope"]]
    major = scoped[:MAJOR_MAX]
    _lend(store, pool, major, subject["vector"], floor - ADJACENT_SPAN, family)
    involved = {i for x in scoped for i in x["sources"]} | {a["item"] for x in major for a in x["adjacent"]}
    summaries = getattr(store, "summaries", {}) or {}
    sources = {}
    for i in sorted(involved):
        item = items[i]
        sources[i] = {"title": item.title, "source": item.source.source_name, "author": _author(store, item),
                      "author_id": item.author_id, "when": when[i].isoformat(), "url": item.source.url,
                      "captured": item.source.captured_at.isoformat(),
                      "minutes": max(1, round(len(item.text.split()) / WORDS_PER_MINUTE)),
                      "hook": (summaries.get(i) or {}).get("hook") or "", "repost_of": item.duplicate_of}
    return {"scope": {k: v for k, v in subject.items() if k != "vector"}, "at": at.isoformat(), "floor": round(floor, 4),
            "major": major, "also": scoped[MAJOR_MAX:], "sources": sources,
            "counts": {"ideas": len(scoped), "sources": len({i for x in scoped for i in x["sources"]}),
                       "rewrites": sum(1 for x in scoped for r in x["sources"].values() if r == "rewrite"),
                       "superseded": sum(len(x["previously"]) for x in scoped)}}


def _lend(store, pool, major: list[dict], center_of_subject: list[float], on_subject: float, family: str) -> None:
    """Each major idea's adjacent sources: claims of other sources close to the idea and on the subject, not
    already its own (a claim near the idea but off the subject is a coincidence of wording)."""
    if not major:
        return
    rows = [c for c in store.claims() if not c.superseded_by]
    vectors = dict(zip((c.claim.claim_id for c in rows), embed.vectors([c.claim.statement for c in rows]))) if rows else {}
    for idea in major:
        center = grouping.centroid([pool.vector[m["thesis"]] for m in idea["members"] if m["role"] != "superseded"])
        found: dict[str, list[tuple[float, str, str]]] = defaultdict(list)
        for c in rows:
            if c.item_id in idea["sources"] or c.claim.claim_id not in vectors:
                continue
            close = embed.cosine(vectors[c.claim.claim_id], center)
            if close >= ADJACENT[family] and embed.cosine(vectors[c.claim.claim_id], center_of_subject) >= on_subject:
                found[c.item_id].append((close, c.claim.claim_id, c.claim.statement))
        best = sorted(found.items(), key=lambda kv: -max(x[0] for x in kv[1]))[:ADJACENT_SOURCES]
        idea["adjacent"] = [{"item": i, "facts": [{"claim": cid, "statement": s, "closeness": round(x, 3)}
                                                  for x, cid, s in sorted(facts, reverse=True)[:FACTS_PER_SOURCE]]}
                            for i, facts in best]
