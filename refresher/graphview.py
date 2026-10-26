"""wiki/graph/: an offline page that draws the graph the refresher reads, with typed edges.

Nodes are the items you captured, the Ideas their claims joined, the people they are by or about, and their sources.
Claim relations are drawn between items (the correction SUPERSEDES the post it corrects); each edge carries the two
statements and the rationale. MENTIONS edges and FROM spans are left out, and so are hidden triage stubs: they are
what turns a graph into a hairball.
"""
import json
import shutil
from datetime import datetime
from pathlib import Path

from graph.memory import MemoryStore
from refresher import km
from refresher.vault import note_names

ASSETS = Path(__file__).parent / "assets"
CLAIM_RELATIONS = ("SUPERSEDES", "CONTRADICTS", "SUPPORTS", "ELABORATES", "DUPLICATES")
EDGE_TYPES = {"STATES", "AUTHORED_BY", "PUBLISHED_IN", "ABOUT", *CLAIM_RELATIONS}


def graph_data(store: MemoryStore, at: datetime) -> dict:
    """Nodes and edges as plain JSON, scored at `at`."""
    scores = km.score_claims(store, at)
    names = note_names(store)
    claims = {c.claim.claim_id: c for c in store.claims()}
    items = {i.item_id: i for i in store.items()}
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}

    def claim_row(cid: str) -> dict:
        s, node = scores[cid], claims[cid]
        return {"statement": node.claim.statement, "band": s.band, "freshness": round(s.freshness, 2),
                "noise": s.noise, "superseded": node.superseded_by is not None, "item": items[node.item_id].title}

    def edge(src: str, kind: str, dst: str, **data):
        if src in nodes and dst in nodes and src != dst:
            edges.setdefault((src, kind, dst), {"source": src, "target": dst, "type": kind, **data})

    for item in items.values():
        nodes[item.item_id] = {"id": item.item_id, "type": "item", "label": item.title,
                               "unreviewed": item.review_status != "accepted", "repost": item.duplicate_of is not None,
                               "note": f"items/{names.get(item.item_id, item.item_id)}",
                               "claims": [claim_row(cid) for cid, c in claims.items() if c.item_id == item.item_id]}
    for idea in store.ideas():
        visible = [cid for cid in sorted(idea.members) if cid in claims]
        if not visible:
            continue
        canonical = scores.get(idea.canonical_claim_id)
        chain = [n.claim.statement for n in store.chain(idea.canonical_claim_id)]  # hidden originals included
        nodes[idea.idea_id] = {"id": idea.idea_id, "type": "idea", "label": idea.title, "status": idea.status,
                               "band": canonical.band if canonical else "stale", "members": len(visible),
                               "note": f"ideas/{names.get(idea.idea_id, idea.idea_id)}",
                               "claims": [claim_row(cid) for cid in visible], "chain": chain if len(chain) > 1 else []}
    for person in store.people():
        nodes[person.person_id] = {"id": person.person_id, "type": "person", "label": person.name,
                                   "trust": round(store.trust(person.person_id), 2),
                                   "note": f"people/{names.get(person.person_id, person.person_id)}"}
    for oid, name in store.organizations().items():
        nodes[oid] = {"id": oid, "type": "organization", "label": name}
    for source in store.sources():
        nodes[source.key] = {"id": source.key, "type": "source", "label": source.name,
                             "note": f"sources/{names.get(source.key, source.key)}"}

    for cid, node in claims.items():
        if node.idea_id:
            edge(node.item_id, "STATES", node.idea_id)
    for e in store.edges():
        if e.type in ("AUTHORED_BY", "PUBLISHED_IN") or (e.type == "DUPLICATES" and e.src in items):
            edge(e.src, e.type, e.dst, rationale=e.rationale)
        elif e.type == "ABOUT" and e.src in claims:
            edge(claims[e.src].item_id, "ABOUT", e.dst)
        elif e.type in CLAIM_RELATIONS and e.src in claims and e.dst in claims:
            # Claim relations between items; a repeat inside one item, or a repost's claims, add nothing here.
            edge(claims[e.src].item_id, e.type, claims[e.dst].item_id, rationale=e.rationale,
                 said=claims[e.src].claim.statement, about=claims[e.dst].claim.statement)

    # People, organizations and sources with no drawn edge are just a list; leave them to the notes.
    linked = {end for e in edges.values() for end in (e["source"], e["target"])}
    kept = {k: n for k, n in nodes.items() if n["type"] in ("item", "idea") or k in linked}
    return {"at": at.isoformat(), "nodes": list(kept.values()),
            "edges": [e for e in edges.values() if e["source"] in kept and e["target"] in kept]}


def write(store: MemoryStore, vault: Path, at: datetime) -> Path:
    """Write wiki/graph/: graph.json, and index.html with the data inline so it opens from disk."""
    out = vault / "wiki" / "graph"
    out.mkdir(parents=True, exist_ok=True)
    data = graph_data(store, at)
    text = json.dumps(data, ensure_ascii=False, indent=1)
    (out / "graph.json").write_text(text + "\n", encoding="utf-8")
    page = (ASSETS / "graph.html").read_text(encoding="utf-8")
    inline = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    (out / "index.html").write_text(page.replace("/*GRAPH_DATA*/null", inline), encoding="utf-8")
    for name in ("cytoscape.min.js", "CYTOSCAPE-LICENSE.txt"):
        shutil.copyfile(ASSETS / name, out / name)
    return out / "index.html"
