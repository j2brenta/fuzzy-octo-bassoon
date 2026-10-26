"""refresher: rebuild the knowledge base from the vault, run the KM pass, evaluate against the seeds.

    uv run refresher rebuild [--vault V] [--at DATE]   replay processed/ + overrides, write wiki/
    add --include-unreviewed to any command to also place items that did not pass ingestion's quality gate
    uv run refresher km [--vault V] [--at DATE]        print the health report at a moment
    uv run refresher eval [--vault V] --manifest M     score against expected labels
    uv run refresher relate [--vault V] [--lexical]    judge relations for items without a relations/ verdict file
    uv run refresher review [--vault V] [--at DATE]    item duplicates and firm Ideas, stored in graph-reviews/
    uv run refresher graph [--vault V] [--at DATE]     write wiki/graph/index.html, an offline view with typed edges
    uv run refresher enrich [--vault V] [--limit N] [--person NAME]   look people up with Tavily, stored in people/

The vault defaults to $REFRESHER_VAULT.
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from graph import relate as relation_stage
from refresher import evaluate, graphview, km, place, vault


def _at(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="refresher", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("rebuild", "km", "eval", "relate", "review", "graph", "enrich"):
        p = sub.add_parser(name)
        p.add_argument("--vault", type=Path, default=os.environ.get("REFRESHER_VAULT"))
        p.add_argument("--at", help="ISO date or datetime to score at (default: now)")
        p.add_argument("--include-unreviewed", action="store_true",
                       help="also place needs_revision/pending items, marked unreviewed (triage skips stay out)")
        if name == "eval":
            p.add_argument("--manifest", type=Path, required=True)
            p.add_argument("--out", type=Path, help="write the markdown report here")
        if name == "enrich":
            p.add_argument("--limit", type=int, default=5, help="people to look up in this run (default 5)")
            p.add_argument("--person", action="append", default=[], help="look up this person (repeatable)")
        if name == "relate":
            p.add_argument("--lexical", action="store_true", help="store lexical verdicts instead of calling the model")
    args = ap.parse_args(argv)
    if not args.vault:
        ap.error("pass --vault or set REFRESHER_VAULT")
    if not (args.vault / "processed").is_dir():
        ap.error(f"{args.vault} has no processed/ folder; run ingestion first")
    at = _at(args.at)
    if args.command == "relate":
        return _relate(args.vault, args.lexical)
    if args.command == "enrich":
        return _enrich(args.vault, args.limit, args.person, at)
    if args.command == "review":
        # Like relate, the review sees unreviewed items too, so its file serves either kind of rebuild.
        report = vault.replay(args.vault, include_unreviewed=True).review(at)
        if not (report.item_duplicates or report.firmed):
            print("review: nothing new")
            return 0
        path = vault.save_review(args.vault, report)
        print(f"review: {len(report.item_duplicates)} item duplicates, {len(report.firmed)} Ideas firmed; wrote {path.name}")
        return 0

    overrides = vault.merged_overrides(args.vault)
    store = vault.replay(args.vault, overrides, include_unreviewed=args.include_unreviewed)

    if args.command == "rebuild":
        if overrides != vault.load_overrides(args.vault):
            vault.save_overrides(args.vault, overrides)
        written = vault.export(store, args.vault, at)
        graphview.write(store, args.vault, at)
        report = km.run(store, at)
        message = (f"rebuild: {report.items} items ({len(report.unreviewed)} unreviewed), {report.claims} claims, "
                   f"{report.people} people, {len(store.held_back)} held back, {len(overrides)} overrides")
        vault.log(args.vault, at, message)
        print(f"{message}; wrote {len(written)} notes to {args.vault / 'wiki'}")
    elif args.command == "graph":
        print(f"graph: wrote {graphview.write(store, args.vault, at)}")
    elif args.command == "km":
        print(km.render(km.run(store, at), store), end="")
    else:
        extractions = {ex.item_id: ex for ex, _ in vault.load_extractions(args.vault)}
        checks = evaluate.evaluate(store, evaluate.load_expectations(args.manifest), extractions,
                                   evaluate.load_groupings(args.manifest))
        text = evaluate.render(checks)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        print(text, end="")
        return 0 if all(c.ok is not False for c in checks) else 1
    return 0


def _relate(vault_path: Path, lexical: bool) -> int:
    """Judge every item without a current verdict file (place.relate_pending) with the model, or lexically."""
    from engine.llm import ModelError
    if lexical:
        relator = relation_stage.LexicalRelator()
    else:
        from dotenv import load_dotenv
        from engine.llm import client_from_environment
        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
        try:
            model = client_from_environment()
        except ModelError as error:
            sys.exit(str(error))
        if model is None:
            sys.exit("Set TRIAGE_PROVIDER=nebius in .env to judge relations with the model, or pass --lexical.")
        relator = relation_stage.ModelRelator(model)
    run = place.relate_pending(vault_path, relator, log=print, warn=lambda m: print(m, file=sys.stderr))
    if run.error:
        print(f"relate: stopped after {run.judged} items", file=sys.stderr)
        return 1
    print(f"relate: judged {run.judged} items, {run.failed} failed")
    return 1 if run.failed else 0


def _enrich(vault_path: Path, limit: int, names: list[str], at: datetime) -> int:
    """Look up the most involved people not yet enriched (or the ones named), at most 3 Tavily searches each."""
    from dotenv import load_dotenv
    from engine.llm import ModelError, ModelOutputError, client_from_environment
    from engine.people import SearchError, enrich, search_from_environment
    from graph.memory import person_id
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    search = search_from_environment()
    if search is None:
        sys.exit("Set TAVILY_API_KEY in .env to look people up.")
    try:
        model = client_from_environment()
    except ModelError as error:
        sys.exit(str(error))
    if model is None:
        sys.exit("Set TRIAGE_PROVIDER=nebius in .env: the model reads the search results.")
    store = vault.replay(vault_path, include_unreviewed=True)
    people = {p.person_id: p for p in store.people()}
    if names:
        chosen = [people[pid] for pid in map(person_id, names) if pid in people]
        missing = [n for n in names if person_id(n) not in people]
        if missing:
            print(f"enrich: not in the graph: {', '.join(missing)}", file=sys.stderr)
    else:
        def involvement(p):
            return 2 * len(p.facts) + 2 * len(p.authored) + len(p.mentioned_in)
        chosen = sorted((p for p in people.values() if not vault.enriched(vault_path, p.person_id)),
                        key=lambda p: (-involvement(p), p.person_id))[:limit]
    claims = {c.claim.claim_id: c for c in store.claims()}
    orgs = store.organizations()
    done = 0
    for p in chosen:
        facts = [claims[cid].claim.statement for cid in sorted(p.facts) if cid in claims]
        titles = [store.item(i).title for i in sorted(p.authored | p.mentioned_in)]
        affiliation = next((name for name in orgs.values() for f in facts if name.casefold() in f.casefold()), None)
        context = facts + [f"Wrote: {store.item(i).title}" for i in sorted(p.authored)] + \
                  [f"Mentioned in: {store.item(i).title}" for i in sorted(p.mentioned_in)]
        try:
            record = enrich(p.person_id, p.name, context, affiliation, titles[0] if titles else None, search, model,
                            log=lambda message: print(f"{p.name}: {message}"))
        except ModelOutputError as error:  # a bad answer: this person waits for the next run
            print(f"{p.name}: not enriched: {error}", file=sys.stderr)
            continue
        except (SearchError, ModelError) as error:  # key, credit or network: every other person would fail too
            print(f"{p.name}: not enriched: {error}", file=sys.stderr)
            print(f"enrich: stopped after {done} people", file=sys.stderr)
            return 1
        vault.save_enrichment(vault_path, record)
        done += 1
        found = ", ".join(f["value"] for f in (record["role"], record["affiliation"]) if f) or "no match"
        print(f"{p.name}: {found}; {len(record['domains'])} domains")
    vault.log(vault_path, at, f"enrich: {done} people looked up with Tavily")
    print(f"enrich: {done} people")
    return 0


if __name__ == "__main__":
    sys.exit(main())
