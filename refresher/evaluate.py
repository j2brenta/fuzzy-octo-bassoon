"""Score a built knowledge base against the seed manifest's expected labels.

Reads both manifest shapes: ingestion's (`items:` list with `expected:`) and the importer's (`items:` map of
file name to `expect:`). Labels are an answer key: they are never fed to the pipeline.

The manifest's top-level `ideas:` lists claims that should form one Idea, each as "seed-id:phrase from the claim".
Groups are also meant to stay apart from each other, so they are scored pairwise over the labelled claims.
"""
from itertools import combinations
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from graph.memory import MemoryStore, person_id
from graph.relate import words
from refresher.schemas import Source


@dataclass
class Expect:
    seed_id: str
    keys: dict  # source fields that identify the item
    labels: dict
    relations: list[tuple[str, str]] = field(default_factory=list)  # (type, target seed id)


@dataclass
class Check:
    seed_id: str
    name: str
    expected: object
    got: object
    ok: bool | None  # None = not scored (e.g. item not processed)


def load_expectations(manifest: Path) -> list[Expect]:
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    items = data.get("items") or []
    out = []
    if isinstance(items, list):
        for e in items:
            labels = dict(e.get("expected") or {})
            rels = [(r["type"], r["target"]) for r in labels.pop("relations", [])]
            keys = {k: e[k] for k in ("channel", "source_id", "message_id") if e.get(k)}
            out.append(Expect(e["id"], keys, labels, rels))
    else:
        for name, e in items.items():
            labels = dict((e or {}).get("expect") or {})
            rels = [(r["type"], r["of"]) for r in labels.pop("relations", [])]
            if "authors" in labels:
                authors = labels.pop("authors")
                labels["author"] = authors[0] if authors else None
            if "claims_min" in labels:
                labels["claim_count_min"] = labels.pop("claims_min")
            out.append(Expect(name, {"source_id": f"seed:{Path(name).stem}#1"}, labels, rels))
    return out


def load_groupings(manifest: Path) -> list[list[tuple[str, str]]]:
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    return [[tuple(label.split(":", 1)) for label in group] for group in data.get("ideas") or []]


def _matches(source: Source, keys: dict) -> bool:
    return all(getattr(source, k, None) == v for k, v in keys.items())


def evaluate(store: MemoryStore, expectations: list[Expect], extractions: dict,
             groupings: list[list[tuple[str, str]]] = ()) -> list[Check]:
    """extractions: item id -> Extraction, including the ones held back from the graph."""
    seed_item = {}
    for exp in expectations:
        found = [ex for ex in extractions.values() if _matches(ex.source, exp.keys)]
        if found:
            seed_item[exp.seed_id] = found[0].item_id

    checks = []
    for exp in expectations:
        item_id = seed_item.get(exp.seed_id)
        if item_id is None:
            checks.append(Check(exp.seed_id, "processed", True, False, None))
            continue
        ex = extractions[item_id]
        lb = exp.labels
        if "author" in lb:
            got = ex.source.author_hint.name if ex.source.author_hint else None
            checks.append(Check(exp.seed_id, "author", lb["author"], got, (got or None) == (lb["author"] or None)))
        for name, got in (("kind", ex.triage.kind if ex.triage else None),
                          ("durability", ex.triage.durability_class if ex.triage else None)):
            if name in lb:
                checks.append(Check(exp.seed_id, name, lb[name], got, got == lb[name]))
        if "claim_count" in lb:
            checks.append(Check(exp.seed_id, "claims", lb["claim_count"], len(ex.claims), len(ex.claims) == lb["claim_count"]))
        if "claim_count_min" in lb:
            checks.append(Check(exp.seed_id, "claims", f">= {lb['claim_count_min']}", len(ex.claims),
                                len(ex.claims) >= lb["claim_count_min"]))
        if lb.get("people") is not None:
            want = {person_id(n) for n in lb["people"]}
            got = {person_id(e.name) for e in ex.entities if e.kind == "person"}
            recall = len(want & got) / len(want) if want else 1.0
            precision = len(want & got) / len(got) if got else 1.0
            checks.append(Check(exp.seed_id, "people recall", 1.0, round(recall, 2), recall >= 0.8))
            checks.append(Check(exp.seed_id, "people precision", 1.0, round(precision, 2), precision >= 0.8))
        if "superseded" in lb:
            got = any(c.superseded_by for c in store.claims(hidden=True) if c.item_id == item_id)
            checks.append(Check(exp.seed_id, "superseded", lb["superseded"], got, got == lb["superseded"]))
        for rtype, target in exp.relations:
            other = seed_item.get(target)
            got = other is not None and _related(store, item_id, other, rtype)
            checks.append(Check(exp.seed_id, f"{rtype} {target}", True, got, got if other else None))
    groups = _labelled_claims(store, groupings, seed_item, checks)
    checks += _grouping_checks(store, groups)
    checks.append(_relation_precision(store, expectations, seed_item, groups))
    return checks


CORPUS = "(corpus)"


def _labelled_claims(store, groupings, seed_item, checks) -> list[set[str]]:
    """Resolve each "seed:phrase" label to the one claim of that seed's item containing the phrase."""
    by_item: dict[str, list] = {}
    for c in store.claims(hidden=True):
        by_item.setdefault(c.item_id, []).append(c)
    out = []
    for group in groupings:
        ids = set()
        for seed_id, phrase in group:
            want = " ".join(words(phrase))
            claims = by_item.get(seed_item.get(seed_id))
            if claims is None:
                checks.append(Check(seed_id, f"idea label '{phrase}'", "1 claim", "not placed", None))
                continue
            found = [c.claim.claim_id for c in claims if want in " ".join(words(c.claim.statement))]
            if len(found) != 1:
                checks.append(Check(seed_id, f"idea label '{phrase}'", "1 claim", len(found), False))
            ids.update(found[:1])
        out.append(ids)
    return out


def _grouping_checks(store, groups: list[set[str]]) -> list[Check]:
    """Pairwise precision and recall: two labelled claims share an Idea exactly when they share a group."""
    if not groups:
        return []
    idea = {c.claim.claim_id: c.idea_id for c in store.claims(hidden=True)}
    group_of = {cid: n for n, g in enumerate(groups) for cid in g}
    pairs = list(combinations(sorted(group_of), 2))
    gold = {p for p in pairs if group_of[p[0]] == group_of[p[1]]}
    got = {p for p in pairs if idea.get(p[0]) is not None and idea.get(p[0]) == idea.get(p[1])}
    precision = len(gold & got) / len(got) if got else 1.0
    recall = len(gold & got) / len(gold) if gold else 1.0
    return [Check(CORPUS, "ideas pairwise precision", 1.0, round(precision, 2), precision >= 0.8),
            Check(CORPUS, "ideas pairwise recall", 1.0, round(recall, 2), recall >= 0.8)]


def _relation_precision(store, expectations, seed_item, groups) -> Check:
    """Of the distinct (seed, relation, seed) triples the graph found, the share the manifest expects. A relation
    between two claims labelled into the same Idea also counts as expected."""
    seed_of = {item_id: seed_id for seed_id, item_id in seed_item.items()}
    claim_item = {c.claim.claim_id: c.item_id for c in store.claims(hidden=True)}
    expected = {(e.seed_id, rtype, target) for e in expectations for rtype, target in e.relations}
    same_group = {frozenset(p) for g in groups for p in combinations(g, 2)}
    found, right = set(), set()
    for e in store.edges():
        if e.type not in {"DUPLICATES", "SUPPORTS", "SUPERSEDES", "CONTRADICTS", "ELABORATES"}:
            continue
        a, b = seed_of.get(claim_item.get(e.src, e.src)), seed_of.get(claim_item.get(e.dst, e.dst))
        if a is None or b is None:
            continue
        triple = (a, e.type, b)
        found.add(triple)
        if triple in expected or frozenset((e.src, e.dst)) in same_group:
            right.add(triple)
    if not found:
        return Check(CORPUS, "relation precision", 1.0, None, None)
    precision = len(right) / len(found)
    wrong = sorted(f"{a} {t} {b}" for a, t, b in found - right)
    return Check(CORPUS, "relation precision", 1.0, f"{precision:.2f}" + (f" (unexpected: {'; '.join(wrong)})" if wrong else ""),
                 precision >= 0.8)


def _related(store: MemoryStore, a: str, b: str, rtype: str) -> bool:
    claim_item = {c.claim.claim_id: c.item_id for c in store.claims(hidden=True)}
    for e in store.edges():
        if e.type != rtype:
            continue
        ends = {claim_item.get(e.src, e.src), claim_item.get(e.dst, e.dst)}
        if ends == {a, b}:
            return True
    return False


def render(checks: list[Check]) -> str:
    scored = [c for c in checks if c.ok is not None]
    passed = sum(c.ok for c in scored)
    lines = ["# Seed evaluation", "", f"{passed}/{len(scored)} checks pass, {len(checks) - len(scored)} not scored.", "",
             "| seed | check | expected | got | |", "|---|---|---|---|---|"]
    for c in checks:
        mark = "–" if c.ok is None else "✓" if c.ok else "✗"
        lines.append(f"| {c.seed_id} | {c.name} | {c.expected} | {c.got} | {mark} |")
    return "\n".join(lines) + "\n"
