"""A subject scored source by source: the topic page's model (GET /api/subject, /api/source/<id>/context).

Everything is scored against the subject, not inherited from accepted-topic membership, so a source sits where its
own Ideas put it:

1. Subject vector: a topic's (or cluster's) centroid; for a phrase, its embedding plus the mean of the ten Ideas
   nearest to it, so one word stands for what the vault says about it.
2. Idea relevance: cosine to the subject vector. Ideas under the floor are out of scope: they cover nothing. The
   floor is the higher of FLOOR and SPAN under the most relevant Idea: on Qwen3, 28 October, unrelated statements
   scored 0.30-0.46 against a subject and on-subject sources peaked at 0.70-0.78, so no fixed 0.35 told them apart.
3. Cluster relevance: the relevance of its in-scope Ideas over all its Ideas. Main is the cluster holding the most
   in-scope relevance (where most of the subject lives: a small pure cluster can tie a large one on the average);
   adjacent shares an Idea with main or reaches ADJACENT; the rest are far.
4. A source belongs to the cluster holding most of its Ideas, whatever the subject (its relevance to the subject
   decides only how near that cluster is, so a source stays in one place across subjects). A theme (Ideas a model gave a name to,
   no cluster close enough) folds into the cluster it links to most, and a cluster only this source's Ideas make
   counts half: it says what the source covers, not where it meets the others (a slide's footer and header Ideas
   must not outweigh its argument). Inside a cluster the anchor covers the most; a perspective adds an in-scope
   Idea the anchor lacks; noise adds none (reposts and sources without Ideas too).
5. Coverage order: a greedy cover of in-scope relevance; what you read counts as covered (skimmed as half). The plan
   is its head, up to TARGET coverage or PLAN_MAX sources, and every source's number is its marginal share there.
6. Edges between clusters: in-scope Idea pairs one in each that are close (SHARED, the embedder's cluster-neighbour
   cut-off: clustering already joins anything past cluster_link, so no pair across clusters is a near-duplicate) or
   related by a claim relation; labelled by the pair's Idea most relevant to the subject.

Pure functions over the replayed store: subject() is fixed for a vault state and cached by the app; plan() and
context() take what you read.
"""
import re
from collections import defaultdict
from datetime import datetime

import numpy as np

from graph import cluster as grouping
from graph import embed
from graph.memory import MemoryStore
from graph.store import AFFILIATION_WORDS
from refresher import dossier, navigator, summarize

FLOOR = {"qwen": 0.35, "hashed": 0.08}  # cosine under which an Idea is always out of scope, per embedder
SPAN = {"qwen": 0.2, "hashed": 0.3}  # and the floor is at most this far under the most relevant Idea
ADJACENT = 0.5  # cluster relevance at which a cluster is adjacent without sharing an Idea with main
SHARED = {"qwen": 0.55, "hashed": 0.5}  # two Ideas in different clusters this close are shared between them
QUERY_EXPAND = 10
TARGET, PLAN_MAX = 0.8, 6
SKIM = 0.5  # what a skimmed source counts for in coverage
GRAPH_CAP = 8  # clusters drawn; beyond, the far ones merge into "other"
RELATIONS = ("SUPPORTS", "CONTRADICTS", "ELABORATES", "SUPERSEDES", "DUPLICATES")
CONNECTED_MAX = 12


def _theme(cluster_id: str) -> bool:
    return cluster_id.startswith("cluster:theme-")


def _family() -> str:
    return "hashed" if embed.thresholds() is embed.HASHED else "qwen"


def _vector(store: MemoryStore, scope: dossier.Scope) -> list[float]:
    if scope.kind != "query" or not scope.center:
        return list(scope.center)
    ideas = sorted({c.idea_id for c in store.claims() if c.idea_id})
    vectors = embed.vectors([store.idea(i).title for i in ideas])
    nearest = sorted(vectors, key=lambda v: -embed.cosine(v, scope.center))[:QUERY_EXPAND]
    if not nearest:
        return list(scope.center)
    mean = grouping.centroid(nearest)
    return grouping.centroid([scope.center, mean])  # the phrase and what the vault says about it, equally


def subject(store: MemoryStore, scope: dossier.Scope, at: datetime) -> dict:
    """The fixed part of a subject: Ideas with relevance, clusters with bands and roles, edges, sources."""
    family = _family()
    near_dup = SHARED[family]
    center = _vector(store, scope)
    items = {i.item_id: i for i in store.items()}
    claims = [c for c in store.claims() if c.item_id in items]
    ideas_of: dict[str, set[str]] = defaultdict(set)
    for c in claims:
        if c.idea_id:
            ideas_of[c.item_id].add(c.idea_id)
    idea_ids = sorted({i for held in ideas_of.values() for i in held})
    vectors = dict(zip(idea_ids, embed.vectors([store.idea(i).title for i in idea_ids]))) if idea_ids else {}
    dots = (np.array([vectors[i] for i in idea_ids]) @ np.array(center)).tolist() if idea_ids and center else []
    relevance = {i: max(0.0, min(1.0, dot)) for i, dot in zip(idea_ids, dots)} if dots else {i: 0.0 for i in idea_ids}  # unit vectors
    floor = max(FLOOR[family], max(relevance.values(), default=0.0) - SPAN[family])
    weight = {i: r for i, r in relevance.items() if r >= floor}

    clusters = {c.cluster_id: c for c in store.clusters()}
    fold = {}  # theme -> the real cluster it links to most
    for link in store.links("cluster"):
        for theme, other in ((link.a, link.b), (link.b, link.a)):
            if _theme(theme) and not _theme(other) and other in clusters and \
                    (theme not in fold or link.weight > fold[theme][0]):
                fold[theme] = (link.weight, other)
    cluster_of = {i: fold[cid][1] if cid in fold else cid for cid, c in sorted(clusters.items()) for i in c.ideas
                  if i in relevance}
    members: dict[str, set[str]] = defaultdict(set)
    for i, cid in cluster_of.items():
        members[cid].add(i)
    for i in idea_ids:  # an Idea no cluster holds is a cluster of its own, so every source has a home
        if i not in cluster_of:
            cluster_of[i] = "idea:" + i.split(":", 1)[-1]
            members[cluster_of[i]].add(i)
    cluster_rel = {cid: sum(weight.get(i, 0.0) for i in held) / len(held) for cid, held in members.items()}

    # Every Idea pair at least this close, once, in blocks (refresher/navigator.py): edges, stance and links read it.
    holders: dict[str, set[str]] = defaultdict(set)
    for item_id, held in ideas_of.items():
        for i in held:
            holders[i].add(item_id)
    every = navigator.near_pairs(vectors, None, min(near_dup, navigator.NEAR_DUPLICATE[family]))

    # Idea pairs shared between clusters: near-duplicates among the in-scope Ideas, and claim relations.
    pairs: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for (a, b), cos in every.items():
        if a < b and cos >= near_dup and a in weight and b in weight and cluster_of[a] != cluster_of[b]:
            pairs[tuple(sorted((cluster_of[a], cluster_of[b])))].add((a, b))
    idea_of_claim = {c.claim.claim_id: c.idea_id for c in claims if c.idea_id}
    for e in store.edges():
        a, b = idea_of_claim.get(e.src), idea_of_claim.get(e.dst)
        if e.type in RELATIONS and a and b and cluster_of[a] != cluster_of[b]:
            pairs[tuple(sorted((cluster_of[a], cluster_of[b])))].add(tuple(sorted((a, b))))
    edges = []
    for (a, b), found in sorted(pairs.items()):
        shared = {i for pair in found for i in pair}
        label = max(shared, key=lambda i: (relevance[i], i))
        edges.append({"a": a, "b": b, "count": len(found), "label": label})

    # The Thread Navigator's signals (refresher/navigator.py): depth, trust from the people scoring, stance, a role
    # in every cluster a source has Ideas in, one home among them, and typed links between sources.
    reposts = {i for i, item in items.items() if item.duplicate_of in items}
    when = {i: items[i].source.event_time for i in items}
    close = {(a, b): cos for (a, b), cos in every.items() if holders[a] != holders[b] or len(holders[a]) > 1}  # across sources
    same = {p for p, cos in close.items() if cos >= navigator.NEAR_DUPLICATE[family]}
    shared_pairs = {p for p, cos in close.items() if cos >= near_dup}
    deep = navigator.depth(store, items, ideas_of, claims, reposts)
    trusted = navigator.trust(store, items, at, sorted(weight) or scope.ideas, scope.title)
    stance = navigator.stances(store, items, claims, ideas_of, same, when)
    first_passage = {}
    for c in claims:
        if c.idea_id:
            for span in c.claim.spans:
                key = (c.item_id, c.idea_id)
                if key not in first_passage or span.start_char < first_passage[key][0]:
                    first_passage[key] = [span.start_char, span.end_char]
    placed, home = navigator.placements(cluster_of, ideas_of, weight, relevance, stance, deep, trusted, when, reposts,
                                        {i: items[i].duplicate_of for i in reposts}, first_passage)
    role = {i: next(r["role"] for r in placed[home[i]] if r["source"] == i) if home.get(i) else "noise" for i in items}
    home = {i: home.get(i) for i in items}
    homed = {cid for cid in home.values() if cid}
    mass = {cid: sum(weight.get(i, 0.0) for i in members[cid]) for cid in members}
    main = max((cid for cid in homed if mass[cid] > 0), key=lambda cid: (mass[cid], cluster_rel[cid], cid), default=None)
    with_main = {e["b"] if e["a"] == main else e["a"]: e for e in edges if main in (e["a"], e["b"])}
    band = {cid: "main" if cid == main else "adjacent" if cluster_rel[cid] > 0 and (cid in with_main
            or cluster_rel[cid] >= ADJACENT) else "far" for cid in members}
    standalone = {i: sum(weight.get(x, 0.0) for x in ideas_of[i]) for i in items}

    summaries = getattr(store, "summaries", {})
    topic_ideas = {t.topic_id: t.ideas | t.attached for t in store.topics()}
    terms = sorted(set(re.findall(r"[^\W\d_]{3,}", scope.title.casefold()))) if scope.kind == "query" else []
    statements: dict[str, list[str]] = defaultdict(list)
    for c in claims:
        statements[c.item_id].append(c.claim.statement)
    words_of = lambda i: set(re.findall(r"[^\W\d_]+", " ".join([items[i].text, *statements[i]]).casefold()))
    sources = {}
    for item_id, item in items.items():
        lines = dossier._lines(store, item)
        hook, gist, kind = _summary(summaries, item, lines)
        sources[item_id] = {
            "id": item_id, "title": item.title, "source": item.source.source_name, "author": dossier._name(store, item.author_id),
            "time": item.source.event_time.isoformat(), "url": item.source.url,
            "minutes": max(1, round(sum(len(ln.split()) for ln in lines) / dossier.WORDS_PER_MINUTE)),
            "hook": hook, "gist": gist, "summary": kind, "cluster": home[item_id], "role": role[item_id],
            "repost_of": item.duplicate_of if item.duplicate_of in items else None,
            "ideas": sorted(ideas_of[item_id] & set(weight), key=lambda i: -relevance[i]),
            "other_ideas": len(ideas_of[item_id] - set(weight)),
            "depth": deep[item_id]["depth"], "depth_inputs": deep[item_id]["inputs"], **trusted[item_id],
            "trust_rank": navigator.TRUST_RANK[trusted[item_id]["trust"]],
            "type": getattr(store, "extras", {}).get(item_id, {}).get("kind") or item.source.channel,
            "places": sorted(({"cluster": r["cluster"], "role": r["role"], "home": r["home"]}
                              for rows in placed.values() for r in rows if r["source"] == item_id),
                             key=lambda p: (not p["home"], navigator.ROLE_RANK[p["role"]], p["cluster"])),
            "all_ideas": sorted(ideas_of[item_id], key=lambda i: (first_passage.get((item_id, i), [1e9])[0], i)),
            "topics": sorted((t for t in topic_ideas if topic_ideas[t] & ideas_of[item_id]),
                             key=lambda t: (-len(topic_ideas[t] & ideas_of[item_id]), t)),
            "matched_terms": [t for t in terms if any(_stem(t) in w for w in words_of(item_id))]}

    # Sources that share an Idea (the same one, or a near-duplicate), with the shared Idea most relevant here.
    twins: dict[str, set[str]] = defaultdict(set)
    for found in pairs.values():
        for a, b in found:
            twins[a].add(b)
            twins[b].add(a)
    holders: dict[str, set[str]] = defaultdict(set)
    for item_id, held in ideas_of.items():
        for i in held:
            holders[i].add(item_id)
    for item_id in items:
        best: dict[str, str] = {}
        count: dict[str, int] = defaultdict(int)
        for i in ideas_of[item_id]:
            for other in {o for j in {i} | twins.get(i, set()) for o in holders[j]} - {item_id}:
                count[other] += 1
            for j in {i} | twins.get(i, set()):
                for other in holders[j] - {item_id}:
                    pick = i if relevance[i] >= relevance[j] else j
                    if other not in best or relevance[pick] > relevance[best[other]]:
                        best[other] = pick
        ranked = sorted(best.items(), key=lambda kv: (-count[kv[0]], -relevance[kv[1]], kv[0]))[:CONNECTED_MAX]
        sources[item_id]["shares"] = [{"source": o, "idea": i, "count": count[o]} for o, i in ranked]

    order = sorted(members, key=lambda cid: ({"main": 0, "adjacent": 1, "far": 2}[band[cid]], -cluster_rel[cid], cid))
    titled = {cid: clusters[cid].title if cid in clusters else store.idea(next(iter(members[cid]))).title for cid in members}
    cluster_rows = []
    for cid in order:
        rows = placed.get(cid, [])
        if not rows:
            continue
        by_role = lambda name: [r["source"] for r in rows if r["role"] == name]
        cluster_rows.append({
            "id": cid, "title": titled[cid], "band": band[cid], "relevance": round(cluster_rel[cid], 3),
            "ideas": len(members[cid]), "in_scope": len(members[cid] & set(weight)),
            "anchor": next(iter(by_role("anchor")), None), "perspectives": by_role("perspective"), "noise": by_role("noise"),
            "also_here": [r["source"] for r in rows if not r["home"]],
            "placements": rows, "shares_with_main": with_main[cid]["count"] if cid in with_main else 0})
    shown = {c["id"] for c in cluster_rows}
    title_of = lambda i: store.idea(i).title
    return {"scope": {"kind": scope.kind, "id": scope.key, "title": scope.title, "note": scope.note}, "at": at.isoformat(),
            "floor": floor, "clusters": cluster_rows,
            "edges": [e for e in edges if e["a"] in shown and e["b"] in shown],
            "links": navigator.links(store, items, claims, ideas_of, cluster_of, shared_pairs, when, title_of),
            "sources": sources, "no_ideas": sorted(i for i in items if not ideas_of[i]),
            "ideas": {i: {"title": store.idea(i).title, "relevance": round(relevance[i], 3), "cluster": cluster_of[i],
                          "in_scope": i in weight} for i in idea_ids},
            "weights": {i: round(w, 4) for i, w in weight.items()},
            "topics": [{"id": t.topic_id, "title": t.title} for t in sorted(store.topics(), key=lambda t: t.topic_id)]}


def _stem(word: str) -> str:
    """Enough of a query word to find its inflections ("hallucination" in "hallucinations")."""
    return word[:max(4, len(word) - 3)]


def plan(s: dict, marks: dict[str, str]) -> dict:
    """What you read applied: coverage now, every source's marginal share in coverage order, and the plan (its head:
    up to TARGET coverage or PLAN_MAX sources) with cumulative coverage."""
    weight = s["weights"]
    total = sum(weight.values()) or 1.0
    cov: dict[str, float] = {}
    for item_id, state in marks.items():
        src = s["sources"].get(item_id)
        if src and state in ("read", "skimmed"):
            for i in src["ideas"]:
                cov[i] = max(cov.get(i, 0.0), 1.0 if state == "read" else SKIM)
    now = sum(weight[i] * cov.get(i, 0.0) for i in weight) / total
    left = {i for i, src in s["sources"].items() if i not in marks and src["ideas"]}
    order, share, seen, reached = [], {}, dict(cov), now
    while left:
        gains = {i: sum(weight[x] * (1 - seen.get(x, 0.0)) for x in s["sources"][i]["ideas"]) for i in left}
        best = max(gains, key=lambda i: (gains[i], s["sources"][i]["trust_rank"], s["sources"][i]["depth"],
                                         s["sources"][i]["time"], i))
        if gains[best] <= 1e-9:
            break
        order.append(best)
        share[best] = round(gains[best] / total, 4)
        reached += gains[best] / total
        for x in s["sources"][best]["ideas"]:
            seen[x] = 1.0
        left.discard(best)
    steps, cumulative = [], now
    for item_id in order:
        if len(steps) >= PLAN_MAX or cumulative >= TARGET - 1e-9:
            break
        cumulative += share[item_id]
        steps.append({"source": item_id, "cumulative": round(min(1.0, cumulative), 4)})
    for item_id in s["sources"]:
        share.setdefault(item_id, 0.0)
    minutes = sum(s["sources"][x["source"]]["minutes"] for x in steps)
    return {"coverage": round(now, 4), "plan": steps, "share": share, "minutes": minutes,
            "reachable": round(min(1.0, reached), 4), "marks": dict(marks)}


def context(store: MemoryStore, s: dict, item_id: str, marks: dict[str, str]) -> dict | None:
    """One source in reading mode: its text, Ideas (new to you or covered, with passages), people, organisations
    and connected sources, each with a one-line relation to this source."""
    src = s["sources"].get(item_id)
    doc = dossier.document(store, item_id)
    if src is None or doc is None:
        return None
    read_elsewhere = {i for other, state in marks.items() if other != item_id and state in ("read", "skimmed")
                      for i in s["sources"].get(other, {}).get("ideas", [])}
    passages: dict[str, list[list[int]]] = defaultdict(list)
    for span in doc["spans"]:
        if span["idea"]:
            passages[span["idea"]].append([span["start"], span["end"]])
    ideas = []
    for i in sorted({x["idea"] for x in doc["spans"] if x["idea"]}, key=lambda i: -s["ideas"].get(i, {}).get("relevance", 0)):
        meta = s["ideas"].get(i, {"title": store.idea(i).title, "in_scope": False, "cluster": None})
        also = sorted(o for o, row in s["sources"].items() if o != item_id and i in row["ideas"])
        ideas.append({"id": i, "title": meta["title"], "in_scope": meta["in_scope"], "new": i not in read_elsewhere,
                      "cluster": meta["cluster"], "passages": passages[i], "also": also})

    item = store.item(item_id)
    claims = [c for c in store.claims() if c.item_id == item_id]
    mentioned = {e.dst for e in store.edges() if e.type == "MENTIONS" and e.src == item_id}
    elsewhere = lambda found: len(found - {item_id})
    people = []
    for p in store.people():
        about = [c for c in claims if c.claim.claim_id in p.facts]
        how = "wrote this" if item.author_id == p.person_id else "discussed here" if about else \
            "mentioned here" if p.person_id in mentioned else None
        if not how:
            continue
        found = set(p.authored) | set(p.mentioned_in) | {c.item_id for c in store.claims() if c.claim.claim_id in p.facts}
        people.append({"id": p.person_id, "name": p.name, "relation": how, "elsewhere": elsewhere(found),
                       "role": (p.role or {}).get("value"), "affiliation": (p.affiliation or {}).get("value"),
                       "domains": [d.get("value") for d in p.domains if isinstance(d, dict) and d.get("value")],
                       "said": [c.claim.statement for c in about][:3]})
    people.sort(key=lambda p: (["wrote this", "discussed here", "mentioned here"].index(p["relation"]), -p["elsewhere"], p["name"]))
    names = {p.person_id: p.name for p in store.people()}
    org_facts = store.org_facts()
    orgs = []
    for o in store.organizations():
        ties = [a for a in store.affiliations() if a.org_id == o.org_id and a.item_id == item_id]
        about = [c for c in claims if c.claim.claim_id in org_facts.get(o.org_id, set())]
        if ties:
            how = "; ".join(f"{names.get(a.person_id, a.person_id)} {AFFILIATION_WORDS.get(a.relation, a.relation)} it"
                            for a in ties[:2])
        elif about:
            how = "discussed here"
        elif o.org_id in mentioned:
            how = "mentioned here"
        else:
            continue
        orgs.append({"id": o.org_id, "name": o.name, "type": o.type, "relation": how,
                     "elsewhere": elsewhere(set(o.mentioned_in))})
    orgs.sort(key=lambda o: (-o["elsewhere"], o["name"]))
    connected = [{"source": x["source"], "idea": x["idea"], "idea_title": s["ideas"][x["idea"]]["title"], "count": x["count"],
                  "same_cluster": s["sources"][x["source"]]["cluster"] == src["cluster"]} for x in src["shares"]]
    placed = {r["cluster"]: r for c in s["clusters"] for r in c["placements"] if r["source"] == item_id}
    for i in ideas:
        here = placed.get(i["cluster"])
        i.update(role=here["role"] if here else None, stance=here["stance"] if here else None)
    links, retold = _links(s, item_id)
    return {**doc, "gist": src["gist"], "hook": src["hook"], "summary": src["summary"], "cluster": src["cluster"],
            "role": src["role"], "ideas": ideas, "people": people, "orgs": orgs, "connected": connected,
            "placements": list(placed.values()), "links": links, "retold_by": retold}


def _links(s: dict, item_id: str) -> tuple[list[dict], list[str]]:
    """This source's typed links, seen from here: each other source once, with the type, a reason from the data,
    which way it points and whether it goes deeper. Retellings of this source fold into retold_by, and a retelling
    of another linked source folds into that source's link."""
    src = s["sources"][item_id]
    original = lambda i: s["sources"][i]["repost_of"] or i
    retold = sorted(i for i, x in s["sources"].items() if x["repost_of"] == item_id)
    out: dict[str, dict] = {}
    for link in s["links"]:
        if item_id not in (link["from"], link["to"]):
            continue
        other = link["to"] if link["from"] == item_id else link["from"]
        if other in retold:
            continue
        shown = original(other) if original(other) != item_id and link["type"] != "retells" else other
        row = {"source": shown, "type": link["type"], "word": link["word"], "label": link["label"],
               "direction": "earlier" if link["from"] == item_id else "later", "also": link["also"],
               "deeper": s["sources"][shown]["depth"] > src["depth"] or s["sources"][shown]["trust_rank"] > src["trust_rank"],
               "retold": sorted(i for i, x in s["sources"].items() if x["repost_of"] == shown and i != item_id)}
        best = out.get(shown)
        if best is None or navigator.LINK_TYPES.index(row["type"]) < navigator.LINK_TYPES.index(best["type"]):
            out[shown] = row
    rows = sorted(out.values(), key=lambda r: (navigator.LINK_TYPES.index(r["type"]), r["direction"], r["source"]))
    return rows, retold


GROUPS = ("cluster_home", "cluster_all", "role", "trust", "type", "topic")
ROLES = ("anchor", "perspective", "noise")


def results(s: dict, p: dict, group_by: str = "cluster_home", topic: str | None = None,
            only_matches: bool = True) -> dict:
    """Every source of the subject as one list, sliced by group_by (GET /api/subject/results). A source matches when it
    holds an Idea in scope. Rows sort by role, then their place in coverage order (the plan first), then trust, depth
    and freshness. Grouped by "cluster_all", a source is a row in every cluster it has Ideas in."""
    if group_by not in GROUPS:
        raise ValueError(f"group_by is one of {', '.join(GROUPS)}")
    sources = s["sources"]
    if topic and topic not in {t["id"] for t in s["topics"]}:
        raise KeyError(topic)
    picked = [i for i in sources if not topic or topic in sources[i]["topics"]]
    match = {i: bool(sources[i]["ideas"]) for i in picked}
    if only_matches:
        picked = [i for i in picked if match[i]]
    plan_at = {x["source"]: n for n, x in enumerate(p["plan"])}
    coverage_at = {i: n for n, i in enumerate(sorted(sources, key=lambda i: (-p["share"].get(i, 0.0), i)))}
    titles = {c["id"]: c["title"] for c in s["clusters"]}
    cluster_order = {c["id"]: n for n, c in enumerate(s["clusters"])}
    topic_titles = {t["id"]: t["title"] for t in s["topics"]}
    placement = {(r["source"], c["id"]): r for c in s["clusters"] for r in c["placements"]}

    def rows_of(i: str) -> list[tuple[str, str, dict | None]]:
        """(group key, group title, the placement it stands for) for each row this source makes."""
        x = sources[i]
        home = (x["cluster"], titles.get(x["cluster"], "No Ideas extracted")) if x["cluster"] else ("none", "No Ideas extracted")
        if group_by == "cluster_all":
            return [(pl["cluster"], titles.get(pl["cluster"], pl["cluster"]), placement.get((i, pl["cluster"])))
                    for pl in x["places"]] or [(home[0], home[1], None)]
        here = placement.get((i, x["cluster"])) if x["cluster"] else None
        key = {"cluster_home": home,
               "role": (x["role"], x["role"]), "trust": (x["trust"], x["trust"]), "type": (x["type"], x["type"]),
               "topic": (x["topics"][0], topic_titles[x["topics"][0]]) if x["topics"] else ("none", "In no topic")}[group_by]
        return [(key[0], key[1], here)]

    rows = []
    for i in picked:
        x = sources[i]
        for key, title, pl in rows_of(i):
            role = pl["role"] if pl else x["role"]
            rows.append({"source": i, "group": key, "group_title": title, "match": match[i], "role": role,
                         "placement": {k: pl[k] for k in ("cluster", "role", "stance", "home", "new_idea_count", "passage")} if pl else None,
                         "depth": x["depth"], "depth_inputs": x["depth_inputs"], "trust": x["trust"], "type": x["type"],
                         "matched_terms": x["matched_terms"], "matched_ideas": len(x["ideas"]), "places": x["places"],
                         "plan": plan_at[i] + 1 if i in plan_at else None})
    group_rank = {"cluster_home": lambda k: cluster_order.get(k, 999), "cluster_all": lambda k: cluster_order.get(k, 999),
                  "role": ROLES.index, "trust": lambda k: -navigator.TRUST_RANK[k], "type": lambda k: 0,
                  "topic": lambda k: 999 if k == "none" else 0}[group_by]
    size: dict[str, int] = defaultdict(int)
    for r in rows:
        size[r["group"]] += 1
    rows.sort(key=lambda r: (group_rank(r["group"]), -size[r["group"]] if group_by in ("type", "topic") else 0, r["group"],
                             not r["match"], ROLES.index(r["role"]), plan_at.get(r["source"], len(plan_at)),
                             coverage_at[r["source"]], -sources[r["source"]]["trust_rank"],
                             -sources[r["source"]]["depth"], -datetime.fromisoformat(sources[r["source"]]["time"]).timestamp(),
                             r["source"]))
    slices = []
    for r in rows:
        if not slices or slices[-1]["group"] != r["group"]:
            slices.append({"group": r["group"], "title": r["group_title"], "count": 0, "roles": {k: 0 for k in ROLES}})
        slices[-1]["count"] += 1
        slices[-1]["roles"][r["role"]] += 1
    head = {"matching": sum(match.values()), "total": len(match), "rows": len(rows),
            "sources": len({r["source"] for r in rows}), "subject": s["scope"]["title"], "kind": s["scope"]["kind"]}
    if group_by == "cluster_all":
        head["placements"] = len(rows)
    return {"header": head, "group_by": group_by, "topic": topic, "only_matches": only_matches,
            "clusters": [{"id": c["id"], "title": c["title"], "band": c["band"]} for c in s["clusters"]],
            "slices": slices, "rows": rows}


def path(s: dict, cluster: str | None = None, source: str | None = None, idea: str | None = None) -> list[dict]:
    """The depth rail: All > subject > cluster > source > Idea, each level with its count and its position among its
    siblings ("3 of 8"). A source without a cluster is placed in its home; an Idea in the source's text order."""
    topics = s["topics"]
    scope = s["scope"]
    levels = [{"level": "all", "id": None, "name": "All topics", "count": len(topics), "noun": "topic",
               "position": None, "of": None}]
    at = next((n for n, t in enumerate(topics) if t["id"] == scope["id"]), None)
    matching = sum(bool(x["ideas"]) for x in s["sources"].values())
    levels.append({"level": "subject", "id": scope["id"], "name": scope["title"], "kind": scope["kind"],
                   "count": matching, "noun": "source", "position": at + 1 if at is not None else None,
                   "of": len(topics) if at is not None else None})
    if source and source not in s["sources"]:
        raise KeyError(source)
    cluster = cluster or (s["sources"][source]["cluster"] if source else None)
    clusters = s["clusters"]
    c = next((c for c in clusters if c["id"] == cluster), None)
    if cluster and c is None:
        raise KeyError(cluster)
    if c:
        levels.append({"level": "cluster", "id": c["id"], "name": c["title"], "band": c["band"],
                       "count": len(c["placements"]), "noun": "source", "position": clusters.index(c) + 1, "of": len(clusters)})
    if source:
        x = s["sources"][source]
        siblings = [r["source"] for r in c["placements"]] if c else list(s["sources"])
        here = next((r for r in c["placements"] if r["source"] == source), None) if c else None
        ideas = (sorted(here["ideas"], key=x["all_ideas"].index) if here else x["all_ideas"])
        levels.append({"level": "source", "id": source, "name": x["title"], "count": len(ideas), "noun": "Idea",
                       "role": here["role"] if here else x["role"], "home": here["home"] if here else None,
                       "position": siblings.index(source) + 1 if source in siblings else None, "of": len(siblings)})
        if idea:
            if idea not in x["all_ideas"]:
                raise KeyError(idea)
            order = ideas if idea in ideas else x["all_ideas"]
            levels.append({"level": "idea", "id": idea, "name": s["ideas"][idea]["title"], "count": None, "noun": None,
                           "position": order.index(idea) + 1, "of": len(order)})
    return levels


def _summary(summaries: dict, item, lines: list[str]) -> tuple[str, str, str]:
    """(hook, gist, "model" | "lead"): the model's summary, else the item's lead sentences."""
    if item.item_id in summaries:
        return summaries[item.item_id]["hook"], summaries[item.item_id].get("gist", ""), "model"
    hook, gist = summarize.lead(lines, item.title)
    return hook, gist, "lead"
