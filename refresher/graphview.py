"""wiki/graph/: the map, an offline page over the graph the refresher reads (Victor's News Atlas viewer, ported).

Topics are regions and clusters regions inside them; items sit in the topic of their Ideas. A Topics mode draws
clusters and topics with the links between them (relations that cross, Ideas they share, close centroids). Two modes: Sources draws items with the relations their
claims have (the correction SUPERSEDES the post it corrects), Claims draws the claims themselves. Clicking an item or
an edge opens the exact passages behind it; two claims can be compared side by side. MENTIONS edges, FROM spans and
hidden triage stubs are left out. Proposals from the review are listed with their state; `refresher serve` (the app,
at /atlas/) adds the buttons that decide them. The page opens from disk with the data inline and makes no network request.
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

from engine import language
from engine.extract import source_passages
from graph.memory import MemoryStore
from graph.store import AFFILIATION_WORDS
from refresher import km, translate
from refresher.people_scoring import subjects as scoring_subjects
from refresher.vault import note_names

ASSETS = Path(__file__).parent / "assets"
MAP_ASSETS = ("index.html", "app.js", "style.css", "people.js")
VENDOR = ("cytoscape.min.js", "CYTOSCAPE-LICENSE.txt")
SHELL_DIR, SHELL = Path(__file__).parent / "static", ("shell.css", "nav.js")  # the bar every page shares
CLAIM_RELATIONS = ("SUPERSEDES", "CONTRADICTS", "SUPPORTS", "ELABORATES", "DUPLICATES")
LABELS = {"SUPERSEDES": "supersedes", "CONTRADICTS": "contradicts", "SUPPORTS": "confirms",
          "ELABORATES": "adds detail to", "DUPLICATES": "repeats", "MERGE": "same as? (proposed)",
          "not_comparable": "not comparable", "insufficient_evidence": "not enough to compare"}
CONTEXT = 80  # characters of text shown before and after a quoted passage


def _anchors(node, text: str, translation: dict | None = None) -> list[dict]:
    out = []
    for n, span in enumerate(node.claim.spans, 1):
        start, end = max(0, span.start_char), min(len(text), span.end_char)
        if not text or start >= end:
            continue
        out.append({"span": n, "quote": text[start:end], "start_char": start, "end_char": end,
                    "prefix": ("…" if start > CONTEXT else "") + text[max(0, start - CONTEXT):start],
                    "suffix": text[end:end + CONTEXT] + ("…" if end + CONTEXT < len(text) else ""),
                    "translation": translate.passage_translation(translation, text, start, end)})
    return out


def _lines(text: str, translation: dict | None) -> list[dict]:
    """The item's translated lines with their place in the original, so the page can switch the whole text."""
    done = (translation or {}).get("passages") or {}
    return [{"start": p["start"], "end": p["end"], "text": done[str(p["id"])]}
            for p in source_passages(text) if str(p["id"]) in done]


def map_data(store: MemoryStore, at: datetime) -> dict:
    """Everything the map shows, as plain JSON, scored at `at`."""
    scores = km.score_claims(store, at)
    names = note_names(store)
    items = {i.item_id: i for i in store.items()}
    translations = getattr(store, "translations", {})
    claims = {c.claim.claim_id: c for c in store.claims() if c.item_id in items}
    ideas = {i.idea_id: i for i in store.ideas()}
    states = store.proposals
    subjects = scoring_subjects(store)
    people_scores = {"": store.experts(at)}
    people_scores.update({s["id"]: store.experts(at, s["id"]) for s in subjects})

    topic_of_idea: dict[str, list[str]] = {}
    topics = []
    for t in sorted(store.topics(), key=lambda t: t.topic_id):
        for idea_id in sorted(t.ideas | t.attached):
            topic_of_idea.setdefault(idea_id, []).append(t.topic_id)
        topics.append({"id": t.topic_id, "title": t.title, "ideas": sorted(t.ideas), "attached": sorted(t.attached),
                       "clusters": sorted(t.clusters)})
    cluster_of_idea = {i: c.cluster_id for c in store.clusters() for i in c.ideas}

    claim_rows = []
    for cid, node in sorted(claims.items(), key=lambda kv: (kv[1].item_id, kv[0])):
        s = scores.get(cid)
        claim_rows.append({"id": cid, "item": node.item_id, "statement": node.claim.statement,
                           "kind": node.claim.kind, "durability": node.durability,
                           "band": s.band if s else "stale", "freshness": round(s.freshness, 2) if s else 0,
                           "noise": bool(s and s.noise), "superseded": node.superseded_by is not None,
                           "idea": node.idea_id, "subject": node.subject_id, "speaker": node.speaker_id,
                           "attribution": node.attribution.model_dump(mode="json") if node.attribution else None,
                           "attribution_reviewed": node.attribution_reviewed, "evidence_origin": node.evidence_origin,
                           "quality": items[node.item_id].review_status,
                           "anchors": _anchors(node, items[node.item_id].text, translations.get(node.item_id))})

    mentions: dict[str, set[str]] = {}  # item -> the people and organizations it names
    for e in store.edges():
        if e.type == "MENTIONS" and e.src in items:
            mentions.setdefault(e.src, set()).add(e.dst)
    named = {p.person_id: p.name for p in store.people()} | {o.org_id: o.name for o in store.organizations()}
    item_rows = []
    for item in sorted(items.values(), key=lambda i: (i.source.event_time, i.item_id)):
        src = item.source
        mine = [c for c in claim_rows if c["item"] == item.item_id]
        idea_ids = sorted({c["idea"] for c in mine if c["idea"]})
        item_rows.append({
            "id": item.item_id, "title": item.title, "source": src.source_name or src.channel,
            "author": store.person(item.author_id).name if item.author_id else None,
            "url": src.url, "time": (src.published_at or src.captured_at).isoformat(),
            "time_basis": "publication" if src.published_at else "capture", "quality": item.review_status,
            "repost_of": item.duplicate_of, "note": f"items/{names.get(item.item_id, item.item_id)}",
            "claims": [c["id"] for c in mine], "stories": idea_ids,
            "topics": sorted({t for i in idea_ids for t in topic_of_idea.get(i, [])}),
            "text": item.text, "translation": _lines(item.text, translations.get(item.item_id)),
            "mentions": [{"id": m, "name": named.get(m, m), "kind": "organization" if m.startswith("org:") else "person"}
                         for m in sorted(mentions.get(item.item_id, ()))]})

    story_rows = []
    for idea in sorted(ideas.values(), key=lambda i: (-len(i.members), i.idea_id)):
        members = [cid for cid in sorted(idea.members) if cid in claims]
        if not members:
            continue
        canonical = scores.get(idea.canonical_claim_id)
        chain = [n.claim.statement for n in store.chain(idea.canonical_claim_id)]
        story_rows.append({"id": idea.idea_id, "title": idea.title, "status": idea.status,
                           "cluster": cluster_of_idea.get(idea.idea_id),
                           "topics": topic_of_idea.get(idea.idea_id, []),
                           "band": canonical.band if canonical else "stale", "claims": members,
                           "items": sorted({claims[c].item_id for c in members}),
                           "chain": chain if len(chain) > 1 else [],
                           "note": f"ideas/{names.get(idea.idea_id, idea.idea_id)}"})

    basis = {}  # claim pair -> proposal state, for relations the review asked the user to confirm
    for st in states.values():
        if st.proposal.kind == "relation":
            basis[frozenset(st.proposal.payload["claims"])] = st
    edges, item_edges = [], {}
    for e in store.edges():
        if e.type == "DUPLICATES" and e.src in items and e.dst in items:
            item_edges.setdefault((e.src, e.dst), {"id": f"{e.src}>{e.dst}", "source": e.src, "target": e.dst,
                                                   "kind": "DUPLICATES", "label": "repost of", "basis": "auto",
                                                   "rationale": e.rationale})
        if e.type not in CLAIM_RELATIONS or e.src not in claims or e.dst not in claims:
            continue
        st = basis.get(frozenset((e.src, e.dst)))
        judged = next((v for v in (store.verdict(e.src), store.verdict(e.dst))
                       if v is not None and v.target_id in (e.src, e.dst) and v.context), None)
        row = {"id": f"{e.src}>{e.type}>{e.dst}", "source": e.src, "target": e.dst, "kind": e.type,
               "label": LABELS[e.type], "rationale": e.rationale, "context": judged.context if judged else None,
               "basis": "proposal" if st and st.status == "proposed" else "accepted" if st and st.status == "accepted"
               else "auto", "proposal": st.proposal.proposal_id if st else None}
        edges.append(row)
        a, b = claims[e.src].item_id, claims[e.dst].item_id
        if a != b and not items[a].duplicate_of:
            item_edges.setdefault((a, b), {**row, "id": f"{a}>{e.type}>{b}", "source": a, "target": b,
                                           "said": claims[e.src].claim.statement,
                                           "about": claims[e.dst].claim.statement})
    for v in store.abstentions():  # the relator would not guess: drawn dotted, with what it saw
        if v.claim_id in claims and v.target_id in claims:
            edges.append({"id": f"{v.claim_id}>ABSTAIN>{v.target_id}", "source": v.claim_id, "target": v.target_id,
                          "kind": "ABSTAIN", "label": LABELS[v.abstention], "rationale": v.rationale,
                          "context": v.context, "basis": "auto", "proposal": None})
    for st in states.values():  # a pending merge is drawn dashed between the two statements
        if st.proposal.kind == "merge_ideas" and st.status == "proposed":
            a, b = st.proposal.payload["claims"][:2]
            if a in claims and b in claims:
                edges.append({"id": f"merge>{st.proposal.proposal_id}", "source": a, "target": b, "kind": "MERGE",
                              "label": LABELS["MERGE"], "rationale": st.proposal.rationale, "basis": "proposal",
                              "proposal": st.proposal.proposal_id})

    cluster_rows = []
    for c in sorted(store.clusters(), key=lambda c: (-len(c.ideas), c.cluster_id)):
        members = [cid for i in sorted(c.ideas) for cid in sorted(ideas[i].members) if cid in claims]
        cluster_rows.append({"id": c.cluster_id, "title": c.title, "ideas": sorted(c.ideas),
                             "sources": sorted(c.sources), "items": sorted({claims[m].item_id for m in members}),
                             "topics": sorted(t.topic_id for t in store.topics() if c.cluster_id in t.clusters),
                             "assigned": sorted(c.assigned), "theme": not c.fingerprint})
    statement = lambda cid: claims[cid].claim.statement if cid in claims else None
    link_rows = [{"id": f"{link.level}>{link.a}>{link.b}", "level": link.level, "source": link.a, "target": link.b,
                  "weight": link.weight, "similarity": link.similarity, "shared": list(link.shared),
                  "relations": [{"source": a, "kind": kind, "target": b, "label": LABELS.get(kind, kind),
                                 "said": statement(a), "about": statement(b)} for a, kind, b in link.relations],
                  "basis": "auto"} for link in store.links()]

    # People and organizations: what is said about them (their profile facts), the clusters their posts feed, and
    # the ties between them (stated in a post, or found by enrichment).
    ties: dict[str, list[dict]] = {}
    for a in store.affiliations():
        if a.item_id is None or a.item_id in items:
            tie = {"relation": a.relation, "label": AFFILIATION_WORDS[a.relation], "item": a.item_id, "url": a.url}
            ties.setdefault(a.person_id, []).append({"id": a.org_id, **tie})
            ties.setdefault(a.org_id, []).append({"id": a.person_id, **tie})

    def profile(pid, name, kind, facts, authored):
        facts = sorted(f for f in facts if f in claims)
        posts = sorted(i for i in authored if i in items)
        told = sorted({claims[f].item_id for f in facts} - set(posts))  # posts that say things about them
        feeds: dict[str, int] = {}
        for c in claims.values():
            if c.item_id in posts + told and c.idea_id in cluster_of_idea:
                feeds[cluster_of_idea[c.idea_id]] = feeds.get(cluster_of_idea[c.idea_id], 0) + 1
        return {"id": pid, "name": name, "kind": kind, "facts": facts, "items": posts,
                "about_in": told,
                "statements": sorted(c.claim.claim_id for c in claims.values() if c.speaker_id == pid),
                "identity_url": store.person(pid).identity_url if kind == "person" else None,
                "role": store.person(pid).role if kind == "person" else None,
                "affiliation": store.person(pid).affiliation if kind == "person" else None,
                "enrichment_status": store.person(pid).enrichment_status if kind == "person" else None,
                "clusters": dict(sorted(feeds.items())), "ties": ties.get(pid, [])}

    people_rows = [row for p in sorted(store.people(), key=lambda p: p.person_id)
                   for row in [profile(p.person_id, p.name, "person", p.facts, p.authored)]
                   if row["facts"] or row["items"] or row["ties"] or row["statements"] or p.mentioned_in]
    org_rows = [{**profile(o.org_id, o.name, "organization", o.facts, set()), "type": o.type,
                 "named_in": sorted(i for i in o.mentioned_in if i in items)}
                for o in sorted(store.organizations(), key=lambda o: o.org_id)]
    org_rows = [row for row in org_rows if row["facts"] or row["named_in"] or row["ties"]]

    all_claims = {c.claim.claim_id: c.claim.statement for c in store.claims(hidden=True)}
    people = {p.person_id: p.name for p in store.people()}
    proposal_rows = []
    for st in sorted(states.values(), key=lambda s: (s.status != "proposed", s.proposal.at, s.proposal.proposal_id)):
        p = st.proposal
        proposal_rows.append({
            "id": p.proposal_id, "kind": p.kind, "status": st.status, "token": st.token, "reason": st.reason,
            "rationale": p.rationale, "at": p.at.isoformat(), "title": p.payload.get("title"),
            "type": p.payload.get("type"),
            "claims": [{"id": c, "statement": all_claims.get(c, "(not in this graph)"),
                        "item": claims[c].item_id if c in claims else None} for c in p.payload.get("claims", [])],
            "people": [people.get(pid, pid) for pid in p.payload.get("people", [])],
            "decisions": [{"action": d.action, "at": d.at.isoformat(), "rationale": d.rationale}
                          for d in st.decisions]})

    return {"snapshot_at": at.isoformat(), "scope": "local", "served": False, "language": language.name(),
            "counts": {"items": len(item_rows), "ideas": len(story_rows), "topics": len(topics),
                       "clusters": len(cluster_rows), "links": len(link_rows),
                       "edges": len(edges), "waiting": sum(p["status"] == "proposed" for p in proposal_rows)},
            "limits": {"primary_nodes": 160},
            "topics": topics, "clusters": cluster_rows, "links": link_rows, "stories": story_rows,
            "people_scores": people_scores,
            "people_subjects": [{k: v for k, v in s.items() if k != "ideas"} for s in subjects],
            "people": people_rows, "organizations": org_rows, "items": item_rows, "claims": claim_rows,
            "edges": edges, "item_edges": list(item_edges.values()), "proposals": proposal_rows}


def page(data: dict) -> str:
    """index.html with the data inline, so it opens from disk."""
    inline = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")  # no </script> or <!-- can close it
    return (ASSETS / "map" / "index.html").read_text(encoding="utf-8").replace("<!--MAP_DATA-->", inline)


def write(store: MemoryStore, vault: Path, at: datetime) -> Path:
    """Write wiki/graph/: graph.json, and index.html with its scripts beside it."""
    out = vault / "wiki" / "graph"
    out.mkdir(parents=True, exist_ok=True)
    data = map_data(store, at)
    (out / "graph.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out / "index.html").write_text(page(data), encoding="utf-8")
    for name in MAP_ASSETS[1:]:
        shutil.copyfile(ASSETS / "map" / name, out / name)
    for name in VENDOR:
        shutil.copyfile(ASSETS / name, out / name)
    for name in SHELL:
        shutil.copyfile(SHELL_DIR / name, out / name)
    return out / "index.html"
