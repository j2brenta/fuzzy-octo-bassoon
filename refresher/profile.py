"""A person or an organization opened from a brief, as the Atlas opens one: who they are, where they stand in the
brief's ranking and why, the sources that involve them, what is said about them, and their neighbours for the mini
map, so a click there opens the next one.

Standing reads the brief's own ranking (refresher/authority.py: roles and credits, never mere mentions) and, for a
person, Olga's contribution score on the brief's Ideas (refresher/people_scoring.py via store.experts). Someone the
ranking leaves out gets the reason in words: named N times and credited by no one, or only facts about them.

Neighbours, strongest tie first:
    affiliation   a stated or enriched tie between a person and an organization
    credit        one credits the other in a source (a link, a name in a thesis, a quote)
    shared        both are involved in the same sources (wrote, quoted, the subject of a claim, named)
"""
from collections import defaultdict
from datetime import datetime

from graph.store import AFFILIATION_WORDS
from refresher import authority

RELATION = ("wrote", "quoted", "about", "mentioned")  # strongest first, as in refresher/area.py details()
PEOPLE, ORGS, SOURCES, FACTS = 8, 6, 12, 8
TIE = {"affiliation": 3, "credit": 2}
CREDITS = {"links": "links to them", "credits": "credits them", "quotes": "quotes them"}  # the neighbour does it
CREDITED = {"links": "linked by them", "credits": "credited by them", "quotes": "quoted by them"}  # to the neighbour


def _involved(store) -> dict[str, dict[str, str]]:
    """item id -> entity id -> the strongest way the item involves them."""
    out: dict[str, dict[str, str]] = defaultdict(dict)

    def tie(item_id, eid, relation):
        if eid:
            before = out[item_id].get(eid)
            if before is None or RELATION.index(relation) < RELATION.index(before):
                out[item_id][eid] = relation

    for item in store.items():
        tie(item.item_id, item.author_id, "wrote")
    for c in store.claims():
        if c.speaker_id and c.attribution and c.attribution.kind == "quoted":
            tie(c.item_id, c.speaker_id, "quoted")
        if c.subject_id:
            tie(c.item_id, c.subject_id, "about")
    for e in store.edges():
        if e.type == "MENTIONS":
            tie(e.src, e.dst, "mentioned")
    return out


def ranking(context: dict) -> list[dict]:
    """The brief's ranking without those listed only for their reach (authority 0)."""
    return [r for r in context.get("people") or [] if r["raw"] > 0]


def _entity(store, eid: str):
    try:
        return "person", store.person(eid)
    except KeyError:
        pass
    try:
        return "organization", store.organization(eid)
    except KeyError:
        return None, None


def _standing(store, context: dict, eid: str, kind: str, sources: list[dict], at: datetime) -> dict:
    rows = ranking(context)
    n = next((k for k, r in enumerate(rows) if r["id"] == eid), None)
    in_brief = [s for s in sources if s["in_brief"]]
    out = {"rank": None if n is None else n + 1, "of": len(rows), "reach": len(in_brief)}
    if n is not None:
        r = rows[n]
        out.update(authority=r["authority"], why=r["why"], cited_by=r["cited_by"])
    elif kind == "organization":
        out["note"] = "The ranking is of writers and the people they credit; an organization is listed for its ties."
    elif not in_brief:
        out["note"] = "Not in this brief's sources; met through a neighbour."
    else:
        said, n = {s["relation"] for s in in_brief}, len(in_brief)
        where = f"{n} source{'s' * (n != 1)}"
        if said & {"wrote", "quoted"}:
            out["note"] = f"A voice in {where} of the brief, below the {len(rows)} it ranks."
        elif said <= {"mentioned"}:
            out["note"] = (f"Named in {where} of the brief and credited by no one: visible, not an authority. "
                           f"A link, a citation or a quote from a ranked writer would rank them.")
        else:
            out["note"] = (f"The subject of facts in {where}, not a voice in them: what is said about someone "
                           f"gives them no authority on the subject.")
    if kind == "person":
        ideas = sorted({c.idea_id for c in store.claims() if c.item_id in context["sources"] and c.idea_id})
        try:
            score = next((r for r in store.experts(at, None, idea_ids=ideas, subject_title=context["scope"]["title"])
                          if r["person_id"] == eid), None)
        except Exception:  # noqa: BLE001 - the people scoring is optional input, as in refresher/authority.py
            score = None
        if score:
            out["contribution"] = {k: score[k] for k in ("importance", "rank", "track_record", "subject_relevance",
                                                          "evidence_level", "source_count", "idea_count")}
            out["contribution"]["rationale"] = score["rationale"][:2]
    return out


def card(store, pool, context: dict, eid: str, at: datetime) -> dict | None:
    """The profile of one person or organization seen from a brief, or None when the vault has no such entity."""
    kind, node = _entity(store, eid)
    if node is None:
        return None
    items = {i.item_id: i for i in store.items()}
    involved = _involved(store)
    mine = {i: rel[eid] for i, rel in involved.items() if eid in rel and i in items}
    sources = [{"item": i, "title": items[i].title, "source": items[i].source.source_name,
                "when": items[i].source.event_time.isoformat(), "relation": r, "in_brief": i in context["sources"]}
               for i, r in mine.items()]
    sources.sort(key=lambda s: (not s["in_brief"], RELATION.index(s["relation"]), s["when"]))
    claims = {c.claim.claim_id: c for c in store.claims()}
    facts = [{"claim": f, "statement": claims[f].claim.statement, "item": claims[f].item_id,
              "in_brief": claims[f].item_id in context["sources"]}
             for f in sorted(node.facts, key=lambda f: (claims[f].item_id not in context["sources"], f)) if f in claims]
    said = sorted((c for c in claims.values() if c.speaker_id == eid), key=lambda c: (c.item_id, c.claim.claim_id))

    ranked = {r["id"]: k + 1 for k, r in enumerate(ranking(context))}
    ties: dict[str, dict] = {}

    def tie(other, how, label, weight):
        okind, onode = _entity(store, other)
        if onode is None or other == eid:
            return
        t = ties.setdefault(other, {"id": other, "name": onode.name, "kind": okind, "how": [], "weight": 0.0,
                                    "rank": ranked.get(other),
                                    "in_brief": any(other in involved.get(i, {}) for i in context["sources"])})
        if label not in t["how"]:
            t["how"].append(label)
        t["weight"] += weight

    for a in store.affiliations():
        word = AFFILIATION_WORDS.get(a.relation, a.relation)
        if a.person_id == eid:
            tie(a.org_id, "affiliation", word, TIE["affiliation"])
        elif a.org_id == eid:
            tie(a.person_id, "affiliation", word + " it", TIE["affiliation"])
    for i, to, _, how in authority.credits(store, pool, items):
        frm = authority.writer(store, items[i])[0]
        if frm == eid:
            tie(to, "credit", CREDITED[how], TIE["credit"])
        elif to == eid:
            tie(frm, "credit", CREDITS[how], TIE["credit"])
    shared: dict[str, int] = defaultdict(int)
    for i in mine:
        for other in involved[i]:
            shared[other] += 1
    for other, n in shared.items():
        tie(other, "shared", f"{n} source{'s' * (n != 1)} together", n)

    def top(k, limit):
        rows = [t for t in ties.values() if t["kind"] == k]
        return sorted(rows, key=lambda t: (-t["weight"], t["rank"] or len(ranked) + 1, not t["in_brief"], t["name"]))[:limit]

    out = {"id": eid, "name": node.name, "kind": kind, "standing": _standing(store, context, eid, kind, sources, at),
           "sources": sources[:SOURCES], "more_sources": max(0, len(sources) - SOURCES),
           "facts": facts[:FACTS], "more_facts": max(0, len(facts) - FACTS),
           "statements": [{"claim": c.claim.claim_id, "statement": c.claim.statement, "item": c.item_id}
                          for c in said[:FACTS]],
           "people": top("person", PEOPLE), "organizations": top("organization", ORGS)}
    if kind == "person":
        out.update(role=node.role, affiliation=node.affiliation, identity_url=node.identity_url)
    else:
        out["type"] = node.type.replace("_", " ")
    return out
