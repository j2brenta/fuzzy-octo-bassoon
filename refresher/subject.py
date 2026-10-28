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
from collections import defaultdict
from datetime import datetime

from graph import cluster as grouping
from graph import embed
from graph.memory import MemoryStore
from graph.store import AFFILIATION_WORDS
from refresher import dossier, scoring, summarize

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
    relevance = {i: max(0.0, min(1.0, embed.cosine(vectors[i], center))) if center else 0.0 for i in idea_ids}
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

    # Idea pairs shared between clusters: near-duplicates among the in-scope Ideas, and claim relations.
    pairs: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    scoped = sorted(weight)
    for n, a in enumerate(scoped):
        for b in scoped[n + 1:]:
            if cluster_of[a] != cluster_of[b] and embed.cosine(vectors[a], vectors[b]) >= near_dup:
                pairs[tuple(sorted((cluster_of[a], cluster_of[b])))].add(tuple(sorted((a, b))))
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

    def trust(item_id: str) -> float:
        author = items[item_id].author_id
        return store.trust(author) if author else scoring.TRUST_PRIOR

    standalone = {i: sum(weight.get(x, 0.0) for x in ideas_of[i]) for i in items}
    sources_in: dict[str, set[str]] = defaultdict(set)
    for item_id, held in ideas_of.items():
        for i in held:
            sources_in[cluster_of[i]].add(item_id)
    home = {}
    for item_id in items:
        held: dict[str, float] = defaultdict(float)
        for i in ideas_of[item_id]:
            alone = 0.5 if sources_in[cluster_of[i]] == {item_id} else 1.0
            held[cluster_of[i]] += alone + 0.001 * weight.get(i, 0.0)  # how many Ideas; ties by relevance here
        home[item_id] = max(held, key=lambda c: (held[c], cluster_rel[c], c)) if held else None
    reposts = {i for i, item in items.items() if item.duplicate_of in items}
    for i in reposts:  # a repost lives with its original
        home[i] = home[items[i].duplicate_of] or home[i]
    when = {i: items[i].source.event_time for i in items}
    homed = {cid for cid in home.values() if cid}
    mass = {cid: sum(weight.get(i, 0.0) for i in members[cid]) for cid in members}
    main = max((cid for cid in homed if mass[cid] > 0), key=lambda cid: (mass[cid], cluster_rel[cid], cid), default=None)
    with_main = {e["b"] if e["a"] == main else e["a"]: e for e in edges if main in (e["a"], e["b"])}
    band = {cid: "main" if cid == main else "adjacent" if cluster_rel[cid] > 0 and (cid in with_main
            or cluster_rel[cid] >= ADJACENT) else "far" for cid in members}
    role, anchor = {}, {}
    for cid in members:
        own = [i for i in items if home[i] == cid]
        if not own:
            continue
        first = max(own, key=lambda i: (i not in reposts, standalone[i], trust(i), when[i], i))
        anchor[cid] = first
        for i in own:
            if i == first:
                role[i] = "anchor"
            elif i in reposts:
                role[i] = "noise"  # a repost adds nothing its original did not
            else:
                role[i] = "perspective" if (ideas_of[i] - ideas_of[first]) & set(weight) else "noise"
    for i in items:
        role.setdefault(i, "noise")
    if not weight:  # nothing in scope: every source is far noise
        role = {i: "noise" for i in items}

    summaries = getattr(store, "summaries", {})
    sources = {}
    for item_id, item in items.items():
        lines = dossier._lines(store, item)
        if item_id in summaries:
            hook, gist, kind = summaries[item_id]["hook"], summaries[item_id].get("gist", ""), "model"
        else:
            (hook, gist), kind = summarize.lead(lines, item.title), "lead"
        sources[item_id] = {
            "id": item_id, "title": item.title, "source": item.source.source_name, "author": dossier._name(store, item.author_id),
            "time": item.source.event_time.isoformat(), "url": item.source.url,
            "minutes": max(1, round(sum(len(ln.split()) for ln in lines) / dossier.WORDS_PER_MINUTE)),
            "hook": hook, "gist": gist, "summary": kind, "cluster": home[item_id], "role": role[item_id],
            "repost_of": item.duplicate_of if item.duplicate_of in items else None,
            "ideas": sorted(ideas_of[item_id] & set(weight), key=lambda i: -relevance[i]),
            "other_ideas": len(ideas_of[item_id] - set(weight)), "trust": round(trust(item_id), 3)}

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
        own = sorted((i for i in items if home[i] == cid), key=lambda i: (-standalone[i], i))
        if not own:
            continue
        cluster_rows.append({
            "id": cid, "title": titled[cid], "band": band[cid], "relevance": round(cluster_rel[cid], 3),
            "ideas": len(members[cid]), "in_scope": len(members[cid] & set(weight)), "anchor": anchor.get(cid),
            "perspectives": [i for i in own if role[i] == "perspective"], "noise": [i for i in own if role[i] == "noise"],
            "shares_with_main": with_main[cid]["count"] if cid in with_main else 0})
    shown = {c["id"] for c in cluster_rows}
    return {"scope": {"kind": scope.kind, "id": scope.key, "title": scope.title}, "at": at.isoformat(),
            "floor": floor, "clusters": cluster_rows,
            "edges": [e for e in edges if e["a"] in shown and e["b"] in shown],
            "sources": sources, "no_ideas": sorted(i for i in items if not ideas_of[i]),
            "ideas": {i: {"title": store.idea(i).title, "relevance": round(relevance[i], 3), "cluster": cluster_of[i],
                          "in_scope": i in weight} for i in idea_ids},
            "weights": {i: round(w, 4) for i, w in weight.items()}}


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
        best = max(gains, key=lambda i: (gains[i], s["sources"][i]["trust"], s["sources"][i]["time"], i))
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
    return {**doc, "gist": src["gist"], "hook": src["hook"], "summary": src["summary"], "cluster": src["cluster"],
            "role": src["role"], "ideas": ideas, "people": people, "orgs": orgs, "connected": connected}
