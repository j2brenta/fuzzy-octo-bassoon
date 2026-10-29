"""Scores the Context Engine against golden subjects (eval/context/manifest.yaml).

For each subject: is the expected base the base of its major idea (base@1), are the expected builders, rewrites and
superseded sources in that idea with those roles, are the expected people at the top of the people list, and what
share of the written brief's sentences kept a valid citation (from the paragraphs already written; none are written
here). For each delta: a thread stamped at one moment and looked at later puts each arrival in the expected class.
Snapshots for deltas are made in a scratch folder, never in the vault. No model calls.
"""
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

from graph import embed
from refresher import brief, context, threads, vault


def _match(prefix: str, ids) -> str | None:
    return next((i for i in ids if i.startswith(prefix)), None)


def _store(path: Path, cache: dict):
    if path not in cache:
        embed.use(embed.from_environment(path))
        cache[path] = vault.replay(path, include_unreviewed=True)
    embed.use(embed.from_environment(path))
    return cache[path]


def run(manifest: Path, root: Path, at: datetime) -> dict:
    spec = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    stores, checks, skipped, validity = {}, [], [], []
    for s in spec.get("subjects", []):
        path = root / s["vault"]
        if not (path / "processed").is_dir():
            skipped.append(f"{s['vault']}: {s['query']}")
            continue
        store = _store(path, stores)
        c = context.build(store, query=s["query"], at=at)
        name = f"{s['query']}"
        if c is None:
            checks.append((name, "subject resolves", False, "nothing matched"))
            continue
        ideas = c["major"] + c["also"]
        for want in s.get("ideas", []):
            idea = next((x for x in ideas if _match(want["base"], [x["base"]])), None)
            checks.append((name, f"base@1 {want['base']}", idea is not None and idea in c["major"],
                           "base of: " + (idea["title"][:60] if idea else
                                          ", ".join(x["base"][:10] for x in c["major"]) + " (no idea has it)")))
            if idea is None:
                continue
            for role, key in (("builder", "builders"), ("rewrite", "rewrites"), ("superseded", "superseded")):
                for prefix in want.get(key, []):
                    got = idea["sources"].get(_match(prefix, idea["sources"]) or "")
                    checks.append((name, f"{role} {prefix}", got == role, f"is {got or 'absent'}"))
        top = [r["name"] for r in c["people"][:3]]
        for person in s.get("people", []):
            checks.append((name, f"top-3 people: {person}", person in top, ", ".join(top)))
        written = brief.write(path, c, None)  # what is written, without calling a model
        if written["validity"] is not None:
            validity.append(written["validity"])
    for d in spec.get("deltas", []):
        path = root / d["vault"]
        if not (path / "processed").is_dir():
            skipped.append(f"{d['vault']}: delta {d['query']}")
            continue
        store = _store(path, stores)
        with tempfile.TemporaryDirectory() as scratch:
            t = threads.follow(Path(scratch), query=d["query"])
            threads.stamp(Path(scratch), store, t["slug"], datetime.fromisoformat(d["stamp"]))
            v = threads.view(Path(scratch), store, t["slug"], datetime.fromisoformat(d["at"]))
        delta = v["delta"]
        name = f"delta {d['query']} since {d['stamp'][:16]}"
        got = {"critical": [r["item"] for r in delta["critical"]],
               "noise": [r["item"] for rows in delta["noise"].values() for r in rows],
               "notes": [r["item"] for r in delta["notes"]]}
        for cls in ("critical", "noise", "notes"):
            for prefix in d.get(cls, []):
                checks.append((name, f"{cls} {prefix}", _match(prefix, got[cls]) is not None,
                               "; ".join(f"{k}: {', '.join(i[:10] for i in v_)}" for k, v_ in got.items() if v_)))
        if "critical" in d and not d["critical"]:
            checks.append((name, "no critical", not got["critical"], ", ".join(i[:10] for i in got["critical"])))
    passed = sum(ok for *_, ok, _ in checks)
    return {"checks": checks, "passed": passed, "total": len(checks), "skipped": skipped,
            "validity": round(sum(validity) / len(validity), 3) if validity else None}


def markdown(report: dict) -> str:
    lines = ["# Context Engine eval", "", f"{report['passed']}/{report['total']} checks pass"
             + (f"; citation validity {report['validity']:.0%} of written sentences" if report["validity"] is not None else "")
             + (f"; skipped (vault missing): {', '.join(report['skipped'])}" if report["skipped"] else ""), "",
             "| subject | check | result | detail |", "| --- | --- | --- | --- |"]
    lines += [f"| {s} | {c} | {'pass' if ok else 'FAIL'} | {d} |" for s, c, ok, d in report["checks"]]
    return "\n".join(lines) + "\n"
