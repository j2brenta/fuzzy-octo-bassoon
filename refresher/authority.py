"""Authority: whom to listen to on a subject, scored from what each writer contributed and who credits them.

Reach (how often someone is named) and authority are kept apart. A dozen mentions in retellings make someone visible,
not an authority; one credit from the writer of a base source can. Authority is computed per major idea
(refresher/context.py groups(), which do not depend on the subject) and summed with weights, so the two views agree:

- a subject (a query or a topic): each major idea weighs its relevance, ideas outside the subject weigh nothing;
- a cluster from ingestion (graph/cluster.py): each major idea weighs the share of its claims whose Idea is in it.

For one writer (a person, or the channel when an item names no writer):

    own        sum over major ideas of weight x the best role they wrote there:
               base 1.0, builder 0.6, superseded 0.3 (they were right first), rewrite 0
    authority  own x (0.5 + track record), plus ENDORSE x the authority of each writer who credits them, over two
               rounds: a link from their item, a cite by name in a thesis, a quote attributed to them (half)

The track record is Olga's (refresher/people_scoring.py via store.experts): 0.5 until checked origins and votes move
it, so it scales authority between 1.0x and 1.5x and never makes anyone untrusted for lack of evidence. Someone who
wrote nothing in the vault but is credited by authorities gets authority from the credit alone: one citation by a
base author outweighs twelve mentions in rewrites.
"""
import re
from collections import defaultdict

from refresher import navigator

ROLE_WEIGHT = {"base": 1.0, "builder": 0.6, "superseded": 0.3, "rewrite": 0.0}
ENDORSE = 0.5
QUOTE = 0.5  # a quote counts half a credit
ROUNDS = 2
TOP = 12
REACH_ONLY = 2  # mentions in the subject from which someone credited by no one is still listed


def writer(store, item) -> tuple[str, str, str]:
    """(id, name, kind) of who stands behind an item: its author, else its channel."""
    if item.author_id:
        try:
            return item.author_id, store.person(item.author_id).name, "person"
        except KeyError:
            pass
    name = item.source.source_name or item.source.channel
    return item.source_key or f"source:{name}", name, "channel"


def _track(store, at) -> dict[str, dict]:
    ideas = sorted({c.idea_id for c in store.claims() if c.idea_id})
    try:
        rows = store.experts(at, idea_ids=ideas, subject_title="everything")
    except Exception:  # noqa: BLE001 - the people scoring is optional input, never a reason to fail a brief
        rows = []
    return {r["person_id"]: r for r in rows}


def names_match(cited: str, name: str) -> bool:
    """A name a thesis credits ("Farquhar et al.", "Fei-Fei Li, Stanford") names this person: every word of the
    person's name is in it, or a long surname matches across scripts and case endings (refresher/navigator.py)."""
    words = set(re.findall(r"[a-z]+", navigator._latin(cited)))
    own = re.findall(r"[a-z]+", navigator._latin(name))
    return bool(own) and (set(own) <= words or bool(navigator._surnames(cited) & navigator._surnames(name)))


def credits(store, pool, items: dict) -> list[tuple[str, str, float, str]]:
    """(from item, to writer id, weight, how): links and captures between items, names cited in theses, quotes."""
    out = []
    for i, targets in pool.points_at.items():
        for j in targets:
            if i in items and j in items:
                out.append((i, writer(store, items[j])[0], 1.0, "links"))
    people = list(store.people())
    for i, rows in pool.of_item.items():
        if i not in items:
            continue
        for t in rows:
            for c in t.get("cites", []):
                for p in people:
                    if c.get("name") and p.person_id != items[i].author_id and names_match(c["name"], p.name):
                        out.append((i, p.person_id, 1.0, "credits"))
    for c in store.claims():
        if c.item_id in items and c.speaker_id and c.attribution and c.attribution.kind == "quoted" \
                and c.speaker_id != items[c.item_id].author_id:
            out.append((c.item_id, c.speaker_id, QUOTE, "quotes"))
    return out


def score(store, pool, ideas: list[dict], weight: dict[str, float], at, top: int = TOP) -> list[dict]:
    """Writers and credited people ranked by authority over major ideas weighted by `weight` (idea id -> weight)."""
    items = {i.item_id: i for i in store.items()}
    track = _track(store, at)
    names, kinds = {}, {}
    own: dict[str, float] = defaultdict(float)
    roles: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    item_weight: dict[str, float] = defaultdict(float)
    for x in ideas:
        w = weight.get(x["id"], 0.0)
        if w <= 0:
            continue
        best: dict[str, float] = {}
        for i, role in x["sources"].items():
            if i not in items:
                continue
            wid, name, kind = writer(store, items[i])
            names[wid], kinds[wid] = name, kind
            best[wid] = max(best.get(wid, 0.0), ROLE_WEIGHT[role])
            roles[wid][role] += 1
            item_weight[i] = max(item_weight[i], w)
        for wid, b in best.items():
            own[wid] += w * b
    factor = {wid: 0.5 + float((track.get(wid) or {}).get("track_record") or 0.5) for wid in set(own) | set(names)}
    base = {wid: own[wid] * factor.get(wid, 1.0) for wid in own}
    # A rewrite's credits repeat its original's, and a writer crediting someone twice credits them once.
    rewrites = {i for x in ideas if weight.get(x["id"], 0.0) > 0 for i, r in x["sources"].items() if r == "rewrite"}
    strongest: dict[tuple[str, str], tuple] = {}
    for i, to, w, how in credits(store, pool, items):
        if item_weight.get(i, 0.0) <= 0 or i in rewrites:
            continue
        pair = (writer(store, items[i])[0], to)
        if pair not in strongest or w * item_weight[i] > strongest[pair][2] * item_weight[strongest[pair][0]]:
            strongest[pair] = (i, to, w, how)
    given = list(strongest.values())
    for _, to, _, _ in given:
        if to not in names:
            try:
                names[to], kinds[to] = store.person(to).name, "person"
            except KeyError:
                names[to], kinds[to] = to, "channel"
    authority = dict(base)
    for _ in range(ROUNDS):
        nxt = dict(base)
        for i, to, w, _ in given:
            frm = writer(store, items[i])[0]
            if frm != to:
                nxt[to] = nxt.get(to, 0.0) + ENDORSE * w * item_weight[i] * max(authority.get(frm, 0.0), 0.1)
        authority = nxt
    reach: dict[str, int] = defaultdict(int)
    scoped = {i for i, w in item_weight.items() if w > 0}
    for p in store.people():
        reach[p.person_id] = len((p.mentioned_in | p.authored) & scoped)
    for i in scoped:
        wid = writer(store, items[i])[0]
        if kinds.get(wid) == "channel":
            reach[wid] += 1
    cited_by: dict[str, set[str]] = defaultdict(set)
    how_cited: dict[str, set[str]] = defaultdict(set)
    for i, to, _, how in given:
        frm = writer(store, items[i])
        if frm[0] != to:
            cited_by[to].add(frm[1])
            how_cited[to].add(how)
    peak = max(authority.values(), default=0.0) or 1.0
    rows = []
    for p in store.people():  # named often but credited by no one: shown for its reach, at no authority
        if reach.get(p.person_id, 0) >= REACH_ONLY and p.person_id not in authority:
            authority[p.person_id], names[p.person_id], kinds[p.person_id] = 0.0, p.name, "person"
    for wid, a in authority.items():
        if a <= 0 and reach.get(wid, 0) < REACH_ONLY:
            continue
        r = track.get(wid) or {}
        parts = [f"{n} {role if n == 1 else role + 's'}" for role, n in sorted(roles[wid].items(),
                                                                             key=lambda kv: ROLE_WEIGHT[kv[0]], reverse=True)
                 if n and role != "rewrite"]
        if roles[wid].get("rewrite"):
            parts.append(f"{roles[wid]['rewrite']} retelling{'s' if roles[wid]['rewrite'] > 1 else ''}")
        if cited_by[wid]:
            parts.append("credited by " + ", ".join(sorted(cited_by[wid])[:3]))
        rows.append({"id": wid, "name": names.get(wid, wid), "kind": kinds.get(wid, "person"),
                     "authority": round(a / peak, 3), "raw": round(a, 4), "reach": reach.get(wid, 0),
                     "roles": dict(roles[wid]), "cited_by": sorted(cited_by[wid]), "how": sorted(how_cited[wid]),
                     "track_record": r.get("track_record"), "evidence_level": r.get("evidence_level"),
                     "why": "; ".join(parts) or "credited in this subject"})
    rows.sort(key=lambda r: (-r["raw"], -r["reach"], r["name"]))
    return _merged(rows)[:top]


def _merged(rows: list[dict]) -> list[dict]:
    """One row per person when the people resolution kept two spellings apart (a diminutive in the genitive in
    Cyrillic and the full name in Latin letters): a weaker person row whose long surname matches a stronger one's
    joins it."""
    out = []
    for r in rows:
        twin = next((o for o in out if o["kind"] == r["kind"] == "person"
                     and navigator._surnames(o["name"]) & navigator._surnames(r["name"])), None)
        if twin is None:
            out.append(dict(r))
            continue
        twin["raw"] = round(twin["raw"] + r["raw"], 4)
        twin["authority"] = round(min(1.0, twin["authority"] + r["authority"]), 3)
        twin["reach"] += r["reach"]
        twin["cited_by"] = sorted(set(twin["cited_by"]) | set(r["cited_by"]))
        twin["also_named"] = sorted(set(twin.get("also_named", [])) | {r["name"]})
    return out


def edges(store, pool, rows: list[dict]) -> list[dict]:
    """Who credits whom among the ranked, and their organisations, for the people graph."""
    items = {i.item_id: i for i in store.items()}
    shown = {r["id"] for r in rows}
    out = {}
    for i, to, w, how in credits(store, pool, items):
        frm = writer(store, items[i])[0]
        if frm in shown and to in shown and frm != to:
            out[(frm, to)] = {"from": frm, "to": to, "how": how}
    for a in store.affiliations():
        if a.person_id in shown:
            out[(a.person_id, a.org_id)] = {"from": a.person_id, "to": a.org_id, "how": a.relation,
                                            "org": store.organization(a.org_id).name}
    return list(out.values())


def by_cluster(store, ideas: list[dict]) -> dict[str, dict[str, float]]:
    """Cluster id -> major idea id -> the share of the idea's claims whose Idea is in the cluster: the weights for
    authority in the dimension of a cluster from ingestion."""
    claims = {c.claim.claim_id: c for c in store.claims()}
    cluster_of = {i: c.cluster_id for c in store.clusters() for i in c.ideas}
    out: dict[str, dict[str, float]] = defaultdict(dict)
    for x in ideas:
        held = [cluster_of.get(claims[c].idea_id) for m in x["members"] for c in m["claims"] if c in claims]
        held = [c for c in held if c]
        for cid in set(held):
            out[cid][x["id"]] = held.count(cid) / len(held)
    return dict(out)


def for_subject(store, pool, ideas: list[dict], at) -> dict:
    """The people block of a brief: ranked rows and the edges between them."""
    weight = {x["id"]: x["relevance"] for x in ideas if x.get("in_scope")}
    rows = score(store, pool, ideas, weight, at)
    return {"people": rows, "edges": edges(store, pool, rows)}

