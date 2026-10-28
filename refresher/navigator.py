"""The Thread Navigator's signals over a scored subject (docs/plans/thread-navigator.md, Phase A).

Everything here is derived: recomputed from the replayed store with the subject, cached with it by app.State, never
written back to extraction or relation records.

- Depth, 1-5: words (log scale), Ideas and specific claims (a figure or a name the source mentions), averaged after
  scaling each by the corpus maximum, then cut at corpus quintiles so all five levels are used. A repost is 1.
- Trust: a word from Olga's people scoring (store.experts, refresher/people_scoring.py) for the source's writer in
  this subject. A writer it leaves unscored (no attributed statements), or no writer at all, reads "unknown", never
  low. No other trust is computed here.
- Stance per Idea: "states" when the source asserts it (its attribution names the author, or no earlier source
  holds it or a near-duplicate and none of its claims relates to an earlier one); "discusses" when it quotes someone
  else or comes after an earlier source saying it.
- Roles per cluster: a greedy cover of the cluster's in-scope Ideas (all its Ideas, equally, when none is in scope),
  candidates ordered by stance, coverage, depth, trust, then earliest. The first pick anchors the cluster, a later
  pick that adds an Idea is a perspective, the rest are noise; a repost is always noise.
- Home: exactly one placement per source with Ideas: a cluster it shares with another source before one only it is
  in, then best role, then "states", then most Ideas there, then the most relevance to the subject. A repost lives
  with its original where it can.
- Links between sources, from the later to the earlier one, one type per pair by precedence (LINK_TYPES): claim
  relations, a repost, a link or a named author with something shared, the same writer, a shared Idea. The later
  one is by event time, except that a source linking or naming another is later than it by definition (a paper
  captured without its publication date would otherwise look newer than the post that links it).
"""
import math
import re
from collections import defaultdict
from datetime import datetime

import numpy as np

# Thresholds, tunable here without touching the code below.
TRUSTED = 0.6  # a scored writer's track record from which the source reads "trusted"
TRUSTED_EVIDENCE = {"developing", "established"}  # and the evidence level it needs: one source item is too little
TRUST_RANK = {"trusted": 2, "known": 1, "unknown": 0}
NEAR_DUPLICATE = {"qwen": 0.85, "hashed": 0.8}  # two Ideas this close say the same thing (stance)
ROLE_RANK = {"anchor": 0, "perspective": 1, "noise": 2}
RELATION_TYPE = {"CONTRADICTS": "disputes", "SUPPORTS": "supports", "SUPERSEDES": "updates", "ELABORATES": "builds_on"}
# One type per source pair, the first that applies; a pair keeps the others in "also".
LINK_TYPES = ("disputes", "retells", "based_on", "supports", "updates", "builds_on", "cites", "same_author", "shares")
LINK_WORDS = {"disputes": "disputes", "retells": "retells", "based_on": "based on", "supports": "supported by",
              "updates": "updates", "builds_on": "builds on", "cites": "cites", "same_author": "same author",
              "shares": "shares an Idea"}
NAME_PREFIX = 6  # letters of a surname that must match, across scripts and case endings
_FIGURE = re.compile(r"\d")
_CYRILLIC = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                     ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s",
                      "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))


def _latin(text: str) -> str:
    return "".join(_CYRILLIC.get(ch, ch) for ch in text.casefold())


def _surnames(name: str) -> set[str]:
    """Name words long enough to be a surname, in Latin letters, cut to NAME_PREFIX."""
    return {w[:NAME_PREFIX] for w in re.findall(r"[a-z]+", _latin(name)) if len(w) >= NAME_PREFIX}


def _url(url: str) -> str:
    u = re.sub(r"^[a-z]+://", "", url.strip().casefold())
    u = re.sub(r"^www\.", "", u)
    return re.sub(r"[?#].*$", "", u).rstrip("/")


def depth(store, items: dict, ideas_of: dict, claims: list, reposts: set) -> dict:
    """item -> {"depth": 1..5, "inputs": {"words", "ideas", "specific"}}."""
    names = {p.person_id: p.name for p in store.people()} | {o.org_id: o.name for o in store.organizations()}
    mentioned: dict[str, set[str]] = defaultdict(set)
    for e in store.edges():
        if e.type == "MENTIONS" and e.dst in names:
            mentioned[e.src].add(names[e.dst].casefold())
    specific: dict[str, int] = defaultdict(int)
    for c in claims:
        if c.idea_id:
            text = c.claim.statement.casefold()
            if _FIGURE.search(text) or any(n and n in text for n in mentioned[c.item_id]):
                specific[c.item_id] += 1
    inputs = {i: {"words": len(item.text.split()), "ideas": len(ideas_of.get(i, ())), "specific": specific[i]}
              for i, item in items.items()}
    top = {k: max([x[k] for x in inputs.values()], default=0) or 1 for k in ("words", "ideas", "specific")}
    raw = {i: (math.log1p(x["words"]) / math.log1p(top["words"]) + x["ideas"] / top["ideas"]
               + x["specific"] / top["specific"]) / 3 for i, x in inputs.items()}
    n = len(raw)
    out = {}
    for i, value in raw.items():
        below = sum(v < value - 1e-12 for v in raw.values())
        level = 1 if i in reposts else 1 + min(4, 5 * below // max(1, n))
        out[i] = {"depth": level, "inputs": inputs[i]}
    return out


def trust(store, items: dict, at: datetime, idea_ids, title: str) -> dict:
    """item -> {"trust", "trust_score", "trust_basis"} from the people scoring of its writer in this subject."""
    rows = {r["person_id"]: r for r in store.experts(at, None, idea_ids=sorted(idea_ids), subject_title=title)}
    out = {}
    for i, item in items.items():
        row = rows.get(item.author_id) if item.author_id else None
        if row is None or row["importance"] is None:
            out[i] = {"trust": "unknown", "trust_score": None, "trust_basis": "none"}
            continue
        word = "trusted" if row["track_record"] >= TRUSTED and row["evidence_level"] in TRUSTED_EVIDENCE else "known"
        out[i] = {"trust": word, "trust_score": row["track_record"], "trust_basis": "author"}
    return out


def near_pairs(vectors: dict, holders: dict, threshold: float, block: int = 512) -> dict[tuple[str, str], float]:
    """Idea pairs held by different sources at least this close, both orders, with their cosine. In blocks of rows,
    so 5,000 Ideas never hold a 5,000 x 5,000 matrix."""
    ids = sorted(vectors)
    if len(ids) < 2:
        return {}
    m = np.array([vectors[i] for i in ids], dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-12
    out = {}
    for start in range(0, len(ids), block):
        sims = m[start:start + block] @ m.T
        for a, b in np.argwhere(sims >= threshold):
            row = start + a
            if b <= row:
                continue
            x, y = ids[row], ids[b]
            if holders[x] != holders[y] or len(holders[x]) > 1:
                out[(x, y)] = out[(y, x)] = float(sims[a, b])
    return out


def stances(store, items: dict, claims: list, ideas_of: dict, near: set, when: dict) -> dict:
    """(item, idea) -> "states" | "discusses"."""
    holders: dict[str, set[str]] = defaultdict(set)
    for i, held in ideas_of.items():
        for x in held:
            holders[x].add(i)
    twins: dict[str, set[str]] = defaultdict(set)
    for a, b in near:
        twins[a].add(b)
    of_claim = {c.claim.claim_id: c for c in claims}
    later_relation = set()  # (item, idea) whose claim relates to a claim in an earlier source
    for e in store.edges():
        a, b = of_claim.get(e.src), of_claim.get(e.dst)
        if e.type in RELATION_TYPE and a and b and a.item_id != b.item_id and a.idea_id:
            for node, other in ((a, b), (b, a)):
                if node.idea_id and when[other.item_id] < when[node.item_id]:
                    later_relation.add((node.item_id, node.idea_id))
    said: dict[tuple[str, str], set[str]] = defaultdict(set)  # attribution kinds of its claims in the Idea
    for c in claims:
        if c.idea_id and c.attribution is not None:
            said[(c.item_id, c.idea_id)].add(c.attribution.kind)
    out = {}
    for i, held in ideas_of.items():
        for x in held:
            kinds = said.get((i, x), set())
            if "author" in kinds:
                out[(i, x)] = "states"
            elif "quoted" in kinds:
                out[(i, x)] = "discusses"
            else:
                others = {o for y in {x} | twins.get(x, set()) for o in holders[y]} - {i}
                earlier = any(when[o] < when[i] for o in others)
                out[(i, x)] = "discusses" if earlier or (i, x) in later_relation else "states"
    return out


def _cover(held: dict, weight: dict, key) -> tuple[list[tuple[str, set[str]]], set[str]]:
    """Greedy cover: the picks in order with what each added, and the sources left out."""
    covered, picks, pool = set(), [], set(held)
    while pool:
        gains = {i: sum(weight.get(x, 0.0) for x in held[i] - covered) for i in pool}
        live = [i for i in pool if gains[i] > 1e-12]
        if not live:
            break
        best = max(live, key=lambda i: key(i, gains[i]))
        added = {x for x in held[best] - covered if weight.get(x, 0.0) > 0}
        picks.append((best, added))
        covered |= added
        pool.discard(best)
    return picks, pool


def placements(cluster_of: dict, ideas_of: dict, weight: dict, relevance: dict, stance: dict, deep: dict,
               trusted: dict, when: dict, reposts: set, originals: dict, first_passage: dict) -> tuple[dict, dict]:
    """(cluster -> [placement], item -> home cluster). A placement is one source in one cluster it has Ideas in."""
    in_cluster: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for i, held in ideas_of.items():
        for x in held:
            in_cluster[cluster_of[x]][i].add(x)
    out: dict[str, list[dict]] = {}
    for cid, held in in_cluster.items():
        ideas = {x for xs in held.values() for x in xs}
        scoped = {x: weight[x] for x in ideas if weight.get(x, 0.0) > 0}
        level = {x: 1.0 for x in ideas}  # the cluster on its own, whatever the subject
        stance_of = {i: "states" if sum(stance[(i, x)] == "states" for x in xs) * 2 >= len(xs) else "discusses"
                     for i, xs in held.items()}
        key = lambda i, gain: (stance_of[i] == "states", round(gain, 9), deep[i]["depth"],
                               TRUST_RANK[trusted[i]["trust"]], -when[i].timestamp(), i)
        candidates = {i: xs for i, xs in held.items() if i not in reposts}
        roles = {}
        for name, w in (("role", scoped or level), ("cluster_role", level)):
            picks, _ = _cover(candidates, w, key)
            for n, (i, added) in enumerate(picks):
                roles.setdefault(i, {})[name] = ("anchor" if n == 0 else "perspective", added)
        rows = []
        for i, xs in held.items():
            role, added = roles.get(i, {}).get("role", ("noise", set()))
            rows.append({"source": i, "cluster": cid, "role": role,
                         "cluster_role": roles.get(i, {}).get("cluster_role", ("noise", set()))[0],
                         "stance": stance_of[i], "ideas": sorted(xs, key=lambda x: (-relevance.get(x, 0.0), x)),
                         "new_ideas": sorted(added, key=lambda x: (-relevance.get(x, 0.0), x)),
                         "new_idea_count": len(added),  # the passage of its first Idea here, in text order
                         "passage": min((first_passage[(i, x)] for x in xs if (i, x) in first_passage), default=None),
                         "relevance": round(sum(weight.get(x, 0.0) for x in xs), 4), "home": False})
        rows.sort(key=lambda r: (ROLE_RANK[r["role"]], -r["relevance"], -len(r["ideas"]), r["source"]))
        out[cid] = rows
    by_source: dict[str, list[dict]] = defaultdict(list)
    for rows in out.values():
        for r in rows:
            by_source[r["source"]].append(r)
    # A cluster only this source is in says what the source covers, not where it meets the others (a slide's footer
    # Ideas must not make its home): such a placement is home only when the source has nothing shared.
    alone = {cid: len(rows) == 1 for cid, rows in out.items()}
    home = {}
    for i in sorted(by_source, key=lambda i: (i in originals, i)):  # originals first, so their reposts can follow
        places = by_source[i]
        best = min(places, key=lambda r: (alone[r["cluster"]], ROLE_RANK[r["role"]], r["stance"] != "states",
                                           -len(r["ideas"]), -r["relevance"], r["cluster"]))
        original = originals.get(i)
        if original in home:  # a repost lives with its original where it can
            best = next((r for r in places if r["cluster"] == home[original]), best)
        home[i] = best["cluster"]
        best["home"] = True
    return out, home


def links(store, items: dict, claims: list, ideas_of: dict, cluster_of: dict, near: set, when: dict,
          idea_title) -> list[dict]:
    """Typed links between sources, from the later to the earlier one."""
    found: dict[tuple[str, str], dict[str, list]] = defaultdict(lambda: defaultdict(list))

    def add(a: str, b: str, kind: str, evidence, label: str, ordered: bool = False):
        """From the later source to the earlier one by event time; `ordered` keeps a -> b, for a link or a name:
        the source that links or names the other is the later one, whatever a missing publication date says."""
        if a == b or a not in items or b not in items:
            return
        later, earlier = (a, b) if ordered or (when[a], a) > (when[b], b) else (b, a)
        found[(later, earlier)][kind].append((evidence, label))

    of_claim = {c.claim.claim_id: c for c in claims}
    for e in store.edges():
        a, b = of_claim.get(e.src), of_claim.get(e.dst)
        if e.type in RELATION_TYPE and a and b and a.item_id != b.item_id:
            add(a.item_id, b.item_id, RELATION_TYPE[e.type], e.src,
                e.rationale or (idea_title(a.idea_id) if a.idea_id else a.claim.statement))
    holders: dict[str, set[str]] = defaultdict(set)
    for i, held in ideas_of.items():
        for x in held:
            holders[x].add(i)
    shared: dict[tuple[str, str], set[str]] = defaultdict(set)
    for x, hold in holders.items():
        for a in hold:
            for b in hold - {a}:
                shared[(a, b)].add(x)
    for x, y in near:
        for a in holders[x]:
            for b in holders[y] - {a}:
                shared[(a, b)].add(x)
    for (a, b), xs in shared.items():
        if a < b or (b, a) not in shared:
            best = max(xs, key=lambda x: (len(holders[x]), x))
            add(a, b, "shares", sorted(xs), f"shares “{idea_title(best)}”")
    for i, item in items.items():
        if item.duplicate_of in items:
            add(i, item.duplicate_of, "retells", i, "repeats its text")
    by_author: dict[str, list[str]] = defaultdict(list)
    for i, item in items.items():
        if item.author_id:
            by_author[item.author_id].append(i)
    names = {p.person_id: p.name for p in store.people()}
    for pid, own in by_author.items():
        for n, a in enumerate(own):
            for b in own[n + 1:]:
                add(a, b, "same_author", pid, f"both by {names.get(pid, pid)}")
    # A link to the other source, or its writer named, plus something they share: based on; a link alone: cites.
    extras = getattr(store, "extras", {})
    urls = {_url(item.source.url): i for i, item in items.items() if item.source.url}
    clusters_of = {i: {cluster_of[x] for x in held} for i, held in ideas_of.items()}
    mentioned: dict[str, set[str]] = defaultdict(set)
    people = {p.person_id: p for p in store.people()}
    for e in store.edges():
        if e.type == "MENTIONS" and e.dst in people:
            mentioned[e.src] |= _surnames(people[e.dst].name)
    for i, item in items.items():
        targets = {urls[u] for u in map(_url, extras.get(i, {}).get("links", [])) if u in urls}
        for j, other in items.items():
            if j == i:
                continue
            linked = j in targets or other.linked_from == i  # i carries a link to j, or j was captured from i's
            author = people.get(other.author_id) if other.author_id else None
            named = bool(author and _surnames(author.name) & mentioned[i])
            if not (linked or named):
                continue
            common = (shared.get((i, j), set()) | shared.get((j, i), set()))
            same_cluster = clusters_of.get(i, set()) & clusters_of.get(j, set())
            how = "links it" if linked else f"names {author.name}"
            if common or same_cluster:
                what = f"“{idea_title(max(common))}”" if common else "a cluster"
                add(i, j, "based_on", sorted(common) or sorted(same_cluster), f"{how} and shares {what}", ordered=True)
            elif linked:
                add(i, j, "cites", j, how, ordered=True)
    out = []
    for (later, earlier), kinds in found.items():
        kind = next(k for k in LINK_TYPES if k in kinds)
        evidence = [ev for ev, _ in kinds[kind]]
        flat = sorted({x for ev in evidence for x in (ev if isinstance(ev, list) else [ev])})
        out.append({"from": later, "to": earlier, "type": kind, "word": LINK_WORDS[kind], "label": kinds[kind][0][1],
                    "evidence": flat, "strength": len(flat), "also": [k for k in LINK_TYPES if k in kinds and k != kind]})
    out.sort(key=lambda r: (LINK_TYPES.index(r["type"]), r["from"], r["to"]))
    return out
