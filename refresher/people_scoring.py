"""Deterministic, rebuildable importance within this collection, not a claim of objective expertise."""
import re
from urllib.parse import urlparse

from refresher import scoring
from graph.corroboration import assertion_groups, checked_origins

VERSION = "people-v3"
STOP = {"the", "a", "an", "of", "on", "in", "for", "and", "to", "is", "are", "with", "their", "this", "that"}


def _words(text):
    # Explicit aliases only; unmatched domains remain unknown rather than negative evidence.
    text = text.casefold()
    for short, expanded in {"ai": "artificial intelligence", "ml": "machine learning",
                            "nlp": "natural language processing"}.items():
        text = re.sub(r"\b" + short + r"\b", expanded, text)
    return set(re.findall(r"[^\W_]+", text)) - STOP


def subjects(store):
    """Clusters are explicitly provisional; accepted topics keep their own identities."""
    return ([{"id": t.topic_id, "title": t.title, "kind": "topic", "ideas": t.ideas | t.attached}
             for t in store.topics()] +
            [{"id": c.cluster_id, "title": c.title, "kind": "cluster", "ideas": c.ideas} for c in store.clusters()])


def _profile(person, subject):
    if subject is None or person.enrichment_status != "enriched":
        return None, []
    domains = [d for d in person.domains if isinstance(d, dict) and isinstance(d.get("value"), str) and _words(d["value"])
               and urlparse(str(d.get("url", ""))).scheme in {"http", "https"}]
    if not domains:
        return None, []
    terms = _words(subject["title"])
    value = max(len(_words(d["value"]) & terms) / len(_words(d["value"])) for d in domains)
    return (max(0.5, value), domains) if value else (None, [])


def score_people(store, at, subject_id=None, *, idea_ids=None, subject_title=None):
    subject = next((s for s in subjects(store) if s["id"] == subject_id), None) if subject_id else None
    if idea_ids is not None:
        subject = {"id": subject_id, "title": subject_title or "Selected subject", "ideas": set(idea_ids)}
    if subject_id and subject is None:
        raise KeyError("Unknown people-scoring subject.")
    all_claims = {n.claim.claim_id: n for n in store.claims()}
    items = {i.item_id: i for i in store.items()}
    accepted_items = {i.item_id for i in items.values() if i.review_status == "accepted" and i.source.event_time <= at}
    accepted = {cid: n for cid, n in all_claims.items()
                if n.item_id in accepted_items and items[n.item_id].duplicate_of not in accepted_items and n.idea_id}
    edges = [e for e in store.edges() if e.src in accepted and e.dst in accepted and (e.at is None or e.at <= at)]
    # Build assertion groups before removing duplicate connector claims. A star and
    # a chain describe the same agreement; neither endpoint earns extra credit.
    groups = assertion_groups(accepted, edges)
    duplicates = {e.src for e in edges if e.type == "DUPLICATES"}
    accepted = {cid: n for cid, n in accepted.items() if cid not in duplicates}
    edges = [e for e in edges if e.src in accepted and e.dst in accepted]
    superseded = {e.dst for e in edges if e.type == "SUPERSEDES"}
    clocks = {cid: items[n.item_id].source.event_time for cid, n in accepted.items()}
    supporters = {}
    group_speakers, group_times = {}, {}
    for cid, node in accepted.items():
        group = groups[cid]
        if node.speaker_id:
            group_speakers.setdefault(group, set()).add(node.speaker_id)
        group_times[group] = max(group_times.get(group, clocks[cid]), clocks[cid])
    for cid, node in accepted.items():
        supporters[cid] = group_speakers.get(groups[cid], set()) - {node.speaker_id}
        if node.speaker_id and supporters[cid]:
            clocks[cid] = group_times[groups[cid]]
    # The display graph can include unreviewed material; none of its derived clocks or counters are scoring inputs.
    eligible = {}
    for cid, node in accepted.items():
        count = len(supporters.get(cid, ()))
        base = node.claim.half_life_days if node.durability == node.claim.durability_class else scoring.HALF_LIFE_DAYS[node.durability]
        if cid not in superseded and not scoring.is_noise(node.durability, scoring.half_life(base, count),
                                                         clocks[cid], at, confirmations=count):
            eligible[cid] = node
    scoped = {cid: n for cid, n in eligible.items() if subject is None or n.idea_id in subject["ideas"]}
    total_ideas = {n.idea_id for n in scoped.values()}
    confirmations, disputes, updates = {}, {}, {}
    members = {}
    for cid, node in scoped.items():
        if node.speaker_id:
            members.setdefault(groups[cid], set()).add(node.speaker_id)
    for group, speakers in members.items():
        for speaker in speakers:
            confirmations.setdefault(speaker, set()).update((group, peer) for peer in speakers if peer != speaker)
    verified = checked_origins(scoped, groups)
    for e in edges:
        a, b = accepted.get(e.src), accepted.get(e.dst)
        if not a or not b or not a.speaker_id or not b.speaker_id or a.speaker_id == b.speaker_id:
            continue
        if subject is not None and not ({a.idea_id, b.idea_id} & subject["ideas"]):
            continue
        key = (a.idea_id, b.idea_id, min(a.speaker_id, b.speaker_id), max(a.speaker_id, b.speaker_id))
        if e.type in {"CONTRADICTS", "SUPERSEDES"}:
            since = items[b.item_id].source.event_time
            if scoring.elapsed_days(since, e.at or items[a.item_id].source.event_time) > b.claim.half_life_days:
                continue
            target = disputes if e.type == "CONTRADICTS" else updates
            for p in (a.speaker_id, b.speaker_id) if e.type == "CONTRADICTS" else (b.speaker_id,):
                target.setdefault(p, set()).add(key)
    out = []
    for person in store.people():
        mine = sorted((n for n in scoped.values() if n.speaker_id == person.person_id), key=lambda n: n.claim.claim_id)
        ideas, sources = {n.idea_id for n in mine}, {n.item_id for n in mine}
        fit = len(ideas) / len(total_ideas) if total_ideas else 0.0
        corroborations = len(confirmations.get(person.person_id, ()))
        good = len(verified.get(person.person_id, ()))
        reviewed = sum(n.attribution_reviewed for n in mine)
        disputed = len(disputes.get(person.person_id, ()))
        changed = len(updates.get(person.person_id, ()))
        learned = person.trust_pinned if person.trust_pinned is not None else scoring.trust_learned(
            confirmations=good, up=person.up, down=person.down)
        professional, evidence = _profile(person, subject)
        importance = scoring.people_importance(fit, learned, professional) if mine else None
        confidence = "established" if len(sources) >= 5 and good >= 3 else "developing" if len(sources) >= 3 else "limited"
        reasons = [f"{len(ideas)} of {len(total_ideas)} accepted Ideas in this subject; {len(sources)} distinct source items.",
                   f"{corroborations} corroborating speakers; {good} distinct primary origins checked by you; {disputed} unresolved disagreements; {changed} updates.",
                   "Trust set by you." if person.trust_pinned is not None else f"User votes: {person.up} up, {person.down} down.",
                   "Professional relevance unknown; neutral 0.5 used." if professional is None else
                   "Professional relevance uses cited domain/title terms and explicit AI/ML/NLP aliases.",
                   f"Speaker review: {reviewed} of {len(mine)} statements checked by you.",
                   "Profile identity checked by you." if person.identity_url else "Identity unresolved: name-only records may combine different people.",
                   "Only checked, distinct primary origins affect track record. This ranking is an uncalibrated collection heuristic."]
        if not mine:
            reasons.insert(0, "Unscored: no accepted, current, nonduplicate statements attributed to this person in this subject.")
        out.append({"person_id": person.person_id, "subject_id": subject_id, "subject_title": subject["title"] if subject else "All subjects", "version": VERSION,
                    "importance": round(importance, 4) if importance is not None else None,
                    "subject_relevance": round(fit, 4), "track_record": round(learned, 4),
                    "professional_relevance": round(professional, 4) if professional is not None else None,
                    "evidence_level": confidence, "claim_ids": [n.claim.claim_id for n in mine],
                    "idea_count": len(ideas), "source_count": len(sources), "confirmations": good, "corroborations": corroborations,
                    "independence_verified": bool(good), "checked_origin_count": good,
                    "reviewed_claim_count": reviewed, "identity_url": person.identity_url,
                    "attribution_status": "reviewed" if mine and reviewed == len(mine) else "mixed" if reviewed else "model_inferred",
                    "disagreements": disputed, "updates": changed, "profile_sources": evidence,
                    "rationale": reasons, "at": at.isoformat()})
    out.sort(key=lambda r: (r["importance"] is None, -(r["importance"] or 0), r["person_id"]))
    for row in out:
        row["rank"] = (1 + sum(other["importance"] is not None and other["importance"] > row["importance"]
                               for other in out)) if row["importance"] is not None else None
    return out
