"""The area around a brief: the topics and clusters its sources live in, and the clusters just beyond it.

The brief (refresher/context.py) says what survives on a subject; the area says where that sits in everything you
have, so you can research further. It reuses the Atlas's own groups (graph/cluster.py): named clusters of close Ideas,
and the topics you accepted, which hold clusters. Nodes are sources, not Ideas: a source is what you open, and an
Idea is the reason two sources sit together.

- Home: a source lives in the cluster holding most of its Ideas, a cluster it shares with another source before one
  only it makes (as in refresher/navigator.py). A source without Ideas (everything it says is about a company or a
  person, so nothing became an Idea) is placed by its theses: in the cluster whose centroid is closest, at
  topic_attach or more; otherwise in an area of its own, named after its major idea.
- In the brief: the clusters holding the brief's sources. Beyond: clusters linked to those (store.links) but holding
  none of them, strongest link first, each with its sources and why it is linked.
- Colour: each major idea has one (its rank), and a source takes the colour of the best-ranked idea it serves, so
  the map, the cards and the reader agree. Sources of the area that are not in the brief stay uncoloured.
"""
from collections import Counter, defaultdict

from graph import cluster as grouping
from graph import embed

BEYOND = 5  # clusters shown beyond the brief
BEYOND_SOURCES = 4  # sources listed per cluster beyond
OTHERS = 6  # sources of a brief cluster that are not in the brief, shown grey


def feeds(store, items: set[str]) -> dict[str, Counter]:
    """item id -> cluster id -> how many of its Ideas are there."""
    cluster_of = {i: c.cluster_id for c in store.clusters() for i in c.ideas}
    out: dict[str, Counter] = defaultdict(Counter)
    for c in store.claims():
        if c.item_id in items and c.idea_id in cluster_of:
            out[c.item_id][cluster_of[c.idea_id]] += 1
    return out


def homes(store, pool, items: set[str], fed: dict[str, Counter], group_of: dict[str, str]) -> dict[str, str]:
    """item id -> its cluster; for an item without Ideas the closest cluster by its theses, else the area of its major
    idea ("own:<idea id>", from `group_of`), else one of its own ("own:<item id>")."""
    clusters = {c.cluster_id: c for c in store.clusters()}
    cut = embed.thresholds().topic_attach
    out = {}
    for i in sorted(items):
        held = fed.get(i)
        if held:
            out[i] = max(held, key=lambda cid: (len(clusters[cid].items) > 1, held[cid], cid))
            continue
        vectors = [pool.vector[t["id"]] for t in pool.of_item.get(i, []) if t["id"] in pool.vector]
        if vectors and clusters:
            mine = grouping.centroid(vectors)
            best = max(clusters.values(), key=lambda c: (embed.cosine(mine, c.centroid) if c.centroid else -1, c.cluster_id))
            if best.centroid and embed.cosine(mine, best.centroid) >= cut:
                out[i] = best.cluster_id
                continue
        out[i] = "own:" + group_of.get(i, i)
    return out


def area(store, context: dict, pool) -> dict:
    """The topics, clusters, sources and links around a brief (see the module docstring)."""
    items = {i.item_id: i for i in store.items() if not i.hidden}
    clusters = {c.cluster_id: c for c in store.clusters()}
    colour, role, group_of = {}, {}, {}
    for n, x in enumerate(context["major"]):
        for i, r in x["sources"].items():
            colour.setdefault(i, n)
            role.setdefault(i, r)
            group_of.setdefault(i, x["id"])
    for n, x in enumerate(context["major"]):
        for a in x.get("adjacent", []):
            role.setdefault(a["item"], "lent")
            colour.setdefault(a["item"], n)
    fed = feeds(store, set(items))
    home = homes(store, pool, set(items), fed, group_of)
    brief_sources = {i for i in colour if i in items}
    inside = {home[i] for i in brief_sources} | {cid for i in brief_sources for cid in fed.get(i, {})}
    members: dict[str, list[str]] = defaultdict(list)
    also: dict[str, list[str]] = defaultdict(list)  # cluster -> sources that feed it but live elsewhere
    for i, cid in home.items():
        members[cid].append(i)
    for i, held in fed.items():
        for cid in held:
            if cid != home.get(i):
                also[cid].append(i)
    relevance = context.get("on_subject", {})

    def ranked(ids):
        return sorted(ids, key=lambda i: (i not in brief_sources, -relevance.get(i, 0.0), i))

    beyond_w: dict[str, tuple[float, str]] = {}
    links = []
    for link in store.links("cluster"):
        a_in, b_in = link.a in inside, link.b in inside
        if a_in and b_in:
            links.append({"a": link.a, "b": link.b, "weight": round(link.weight, 3)})
        elif a_in or b_in:
            near, far = (link.a, link.b) if a_in else (link.b, link.a)
            if far in clusters and (far not in beyond_w or link.weight > beyond_w[far][0]):
                beyond_w[far] = (link.weight, near)
    beyond = sorted(beyond_w.items(), key=lambda kv: (-kv[1][0], kv[0]))[:BEYOND]
    for far, (w, near) in beyond:
        links.append({"a": near, "b": far, "weight": round(w, 3), "beyond": True})
    topic_of = {}
    for t in store.topics():
        for cid in t.clusters:
            topic_of.setdefault(cid, t.topic_id)
    shown = sorted(inside) + [far for far, _ in beyond]
    titles = {x["id"]: x["title"] for x in context["major"]}

    def title_of(cid):
        if cid.startswith("own:"):
            key = cid[4:]
            x = next((x for x in context["major"] if x["id"] == key), None)
            return (x["title"] if x else items[key].title if key in items else key)[:80]
        return clusters[cid].title

    out_clusters, sources = [], {}
    for cid in shown:
        is_in = cid in inside
        own = ranked(members.get(cid, []))
        listed = [i for i in own if i in brief_sources] + [i for i in own if i not in brief_sources][:OTHERS if is_in else BEYOND_SOURCES]
        visiting = ranked(i for i in also.get(cid, []) if i in brief_sources or not is_in)[:BEYOND_SOURCES]
        out_clusters.append({"id": cid, "title": title_of(cid), "topic": topic_of.get(cid), "in_brief": is_in,
                             "count": len(own), "sources": listed, "also": visiting,
                             "ideas": sorted({colour[i] for i in listed + visiting if i in colour})})
        for i in listed + visiting:
            if i in sources:
                continue
            th = pool.of_item.get(i) or [{"statement": items[i].title}]
            sources[i] = {"cluster": home[i], "feeds": sorted(fed.get(i, {})), "idea": colour.get(i), "role": role.get(i),
                          "title": items[i].title,
                          "source": items[i].source.source_name, "thesis": th[0]["statement"],
                          "relevance": round(relevance.get(i, 0.0), 3)}
    topics = [{"id": t.topic_id, "title": t.title, "clusters": sorted(set(t.clusters) & set(shown))}
              for t in store.topics() if set(t.clusters) & set(shown)]
    for c in out_clusters:
        if c["in_brief"] is False:
            c["why"] = next((f"linked to {title_of(link['a'])}" for link in links if link.get("beyond") and link["b"] == c["id"]), "")
    return {"topics": topics, "clusters": out_clusters, "sources": sources, "links": links,
            "idea_titles": [titles[x["id"]] for x in context["major"]]}


ROLE_STEP = {"base": 1.0, "builder": 0.8, "lent": 0.6, "superseded": 0.4}
NOVELTY = {None: 1.0, "unread": 1.0, "skimmed": 0.4, "read": 0.15}
STEP_GROUPS, STEP_ROWS = 5, 4
WHY = {("BUILDS_ON", True): "builds on this", ("BUILDS_ON", False): "this builds on it",
       ("SAME", True): "says the same", ("SAME", False): "says the same",
       ("CONFLICTS", True): "disputes this", ("CONFLICTS", False): "disputes this"}


def next_steps(store, context: dict, around: dict, pool, item_id: str, marks: dict[str, str]) -> list[dict]:
    """Where to go from this source, grouped by what you find there (the major idea, else the area) and ranked:
    relevance to the subject x the role a source plays x what is new to you (read sources sink), plus a direct
    relation to this source. Rewrites are counted, never listed."""
    from refresher import thesis_relate
    items = {i.item_id: i for i in store.items() if not i.hidden}
    mine = {t["id"] for t in pool.of_item.get(item_id, [])}
    relevance = context.get("on_subject", {})
    when = {i: items[i].source.event_time for i in items}
    direct: dict[str, str] = {}
    for e in thesis_relate.edges(getattr(store, "thesis_relations", {}) or {}, pool.of_item):
        a, b = thesis_relate.item_of(e["thesis"]), thesis_relate.item_of(e["target"])
        if e["thesis"] in mine and b != item_id:
            other, outgoing = b, True
        elif e["target"] in mine and a != item_id:
            other, outgoing = a, False
        else:
            continue
        if e["label"] == "UPDATES":
            why = "newer version" if (when.get(other), other) > (when.get(item_id), item_id) else "earlier version"
        elif e["label"] == "BUILDS_ON":
            why = "this builds on it" if outgoing else "builds on this"
        else:
            why = WHY.get((e["label"], outgoing), e["label"].lower())
        direct.setdefault(other, why)
    rows: dict[str, dict] = {}
    retold = {i for x in context["major"] for i, r in x["sources"].items() if r == "rewrite"} \
        | {i for i, it in items.items() if it.duplicate_of}  # counted, never offered, wherever else they show up

    def offer(i, group, colour, role, why):
        if i == item_id or i not in items or role == "rewrite" or i in retold:
            return
        score = max(relevance.get(i, 0.0), 0.3) * ROLE_STEP.get(role, 0.5) * NOVELTY.get(marks.get(i), 1.0)
        if i in direct:
            score += 0.3 * NOVELTY.get(marks.get(i), 1.0)
            why = direct[i]
        if i not in rows or score > rows[i]["score"]:
            th = pool.of_item.get(i) or [{"statement": items[i].title}]
            rows[i] = {"item": i, "group": group, "colour": colour, "why": why, "score": round(score, 4),
                       "title": items[i].title, "source": items[i].source.source_name, "thesis": th[0]["statement"],
                       "read": marks.get(i)}

    rewrites: Counter = Counter()
    for n, x in enumerate(context["major"]):
        for i, role in x["sources"].items():
            if role == "rewrite":
                rewrites[x["title"]] += 1
                continue
            offer(i, x["title"], n, role, {"base": "the source to read on this", "builder": "builds on it",
                                           "superseded": "an earlier view"}.get(role, role))
        for a in x.get("adjacent", []):
            offer(a["item"], x["title"], n, "lent", "lends facts: " + a["facts"][0]["statement"][:90])
    titles = {c["id"]: c["title"] for c in around["clusters"]}
    for c in around["clusters"]:
        for i in c["sources"] + c.get("also", []):
            if i not in rows:
                offer(i, titles[c["id"]], None, "area" if c["in_brief"] else "beyond",
                      ("same area: " if c["in_brief"] else "further: ") + c["title"][:60])
    for i, why in direct.items():
        if i not in rows:
            offer(i, "Linked to this source", None, "area", why)
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows.values():
        groups[r["group"]].append(r)
    out = []
    for name, rs in groups.items():
        rs.sort(key=lambda r: (-r["score"], r["item"]))
        out.append({"group": name, "colour": rs[0]["colour"], "rows": rs[:STEP_ROWS], "more": max(0, len(rs) - STEP_ROWS),
                    "rewrites": rewrites.get(name, 0), "best": rs[0]["score"]})
    out.sort(key=lambda g: (-g["best"], g["group"]))
    return out[:STEP_GROUPS]


def reader(store, context: dict, around: dict, pool, item_id: str, marks: dict[str, str]) -> dict | None:
    """One source to read inside the brief: its text, which passages carry which major idea (its thesis claims) or
    lend one a fact, where it sits in the area, and where to go next."""
    from refresher import dossier
    doc = dossier.document(store, item_id)
    if doc is None:
        return None
    marked: dict[str, dict] = {}
    for n, x in enumerate(context["major"]):
        for m in x["members"]:
            if m["item"] == item_id:
                for cid in m["claims"]:
                    marked.setdefault(cid, {"idea": n, "kind": "superseded" if m["role"] == "superseded" else "thesis"})
        for a in x.get("adjacent", []):
            if a["item"] == item_id:
                for f in a["facts"]:
                    marked.setdefault(f["claim"], {"idea": n, "kind": "lent"})
    here = around["sources"].get(item_id) or {"cluster": homes(store, pool, {item_id}, feeds(store, {item_id}), {})[item_id]}
    theses = [{"statement": t["statement"], "stance": t["stance"]} for t in pool.of_item.get(item_id, [])]
    roles = [{"idea": n, "title": x["title"], "role": x["sources"][item_id]} for n, x in enumerate(context["major"])
             if item_id in x["sources"]]
    return {**doc, "marked": marked, "theses": theses, "roles": roles, "here": here,
            "next": next_steps(store, context, around, pool, item_id, marks), "read": marks.get(item_id)}
