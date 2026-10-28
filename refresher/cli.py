"""refresher: rebuild the knowledge base from the vault, run the KM pass, evaluate against the seeds.

    uv run refresher rebuild [--vault V] [--at DATE]   replay processed/ + overrides, write wiki/
    add --include-unreviewed to any command to also place items that did not pass ingestion's quality gate
    uv run refresher km [--vault V] [--at DATE]        print the health report at a moment
    uv run refresher eval [--vault V] --manifest M     score against expected labels
    uv run refresher relate [--vault V] [--lexical]    judge relations for items without a relations/ verdict file
    uv run refresher review [--vault V] [--at DATE]    item duplicates and firm Ideas, stored in graph-reviews/
    uv run refresher graph [--vault V] [--at DATE]     write wiki/graph/index.html, an offline view with typed edges
    uv run refresher enrich [--vault V] [--limit N] [--person NAME]   look people up with Tavily, stored in people/
    uv run refresher consolidate [--vault V] [--lexical]   the hourly job: relate, review, enrich, rebuild
    add --if-due to run it only when due and something new was placed, --every SECONDS to keep checking
    uv run refresher serve [--vault V] [--port P]     the app on http://127.0.0.1:8080: add sources, the Atlas, proposals
    uv run refresher proposals [--vault V] [--all]     what the review suggests, with the token to decide it
    uv run refresher decide [--vault V] ID accept|reject|defer --token T [--why TEXT]
    uv run refresher problems [--vault V] [--since DATE] [--limit N]   warnings and errors kept in logs/problems.jsonl
    uv run refresher translate [--vault V] [--force]  passages in REFRESHER_LANGUAGE for reading, stored in translations/

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
    for name in ("rebuild", "km", "eval", "relate", "review", "graph", "enrich", "proposals", "decide", "consolidate", "serve",
                 "translate", "problems"):
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
        if name == "serve":
            p.add_argument("--port", type=int, default=8080)
            p.add_argument("--host", default="127.0.0.1", help="bind address; the container uses 0.0.0.0")
            p.add_argument("--allow-host", action="append", default=[],
                           help="another Host the browser uses, e.g. a published port: localhost:9000 (repeatable)")
        if name == "consolidate":
            p.add_argument("--lexical", action="store_true", help="no model or web calls: wording and similarity only")
            p.add_argument("--if-due", action="store_true",
                           help="only when the last run is an hour old and something was placed since (else exit 0)")
            p.add_argument("--every", type=float, metavar="SECONDS",
                           help="keep checking at this interval, as --if-due (the review container)")
        if name == "problems":
            p.add_argument("--since", help="ISO date or datetime; only problems from then on")
            p.add_argument("--limit", type=int, default=20, help="latest problems shown in full (default 20)")
        if name == "translate":
            p.add_argument("--force", action="store_true", help="translate again even when a stored translation matches")
        if name == "proposals":
            p.add_argument("--all", action="store_true", help="also list decided, stale and unplaced proposals")
        if name == "decide":
            p.add_argument("proposal")
            p.add_argument("action", choices=("accept", "reject", "defer"))
            p.add_argument("--token", required=True, help="from `refresher proposals`; refused if anything changed")
            p.add_argument("--why", default="", help="your reason, kept with the decision")
    args = ap.parse_args(argv)
    if not args.vault:
        ap.error("pass --vault or set REFRESHER_VAULT")
    if args.command != "serve" and not (args.vault / "processed").is_dir():  # the app may start an empty vault
        ap.error(f"{args.vault} has no processed/ folder; run ingestion first")
    at = _at(args.at)
    from engine import logs
    logs.attach(args.vault, process="cli")  # warnings still print to stderr; they are also kept in the vault
    if args.command == "problems":
        return _problems(args.vault, args.since, args.limit)
    _language()
    _embedder(args.vault)
    if args.command == "translate":
        return _translate(args.vault, args.force, at)
    if args.command == "relate":
        return _relate(args.vault, args.lexical)
    if args.command == "enrich":
        return _enrich(args.vault, args.limit, args.person, at)
    if args.command == "serve":
        from refresher.app import serve
        serve(args.vault, args.port, args.host, args.allow_host)
        return 0
    if args.command == "consolidate":
        return _consolidate(args.vault, args.lexical, at, args.if_due, args.every)
    if args.command == "proposals":
        return _proposals(args.vault, args.all)
    if args.command == "decide":
        try:
            d = vault.decide(args.vault, args.proposal, args.action, args.token, args.why, at)
        except KeyError:
            sys.exit(f"decide: no proposal {args.proposal}")
        except vault.StaleToken as error:
            sys.exit(f"decide: refused: {error}")
        print(f"decide: {d.action} {d.proposal_id}")
        return 0
    if args.command == "review":
        # Like relate, the review sees unreviewed items too, so its file serves either kind of rebuild.
        with vault.lock(args.vault):
            report = vault.replay(args.vault, include_unreviewed=True).review(at)
            if not (report.item_duplicates or report.firmed or report.proposals):
                print("review: nothing new")
                return 0
            path = vault.save_review(args.vault, report)
        print(f"review: {len(report.item_duplicates)} item duplicates, {len(report.firmed)} Ideas firmed, "
              f"{len(report.proposals)} new proposals; wrote {path.name}")
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


def _problems(vault_path: Path, since: str | None, limit: int) -> int:
    """What went wrong, grouped by kind (ids and numbers stripped), then the latest problems in full."""
    import re
    from collections import Counter
    from engine import logs
    rows = logs.read_problems(vault_path)
    if since:
        rows = [r for r in rows if (r.get("at") or "") >= _at(since).isoformat(timespec="seconds")]
    if not rows:
        print(f"problems: none in {vault_path / logs.PROBLEMS}" + (f" since {since}" if since else ""))
        return 0
    kind = lambda r: (r.get("source"), re.sub(r"\b[0-9a-f]{10,64}\b|\d+(\.\d+)?", "#", r.get("message", ""))[:140])
    groups = Counter(kind(r) for r in rows)
    last = {kind(r): r.get("at") for r in rows}
    print(f"problems: {len(rows)} from {rows[0].get('at')} to {rows[-1].get('at')}, {len(groups)} kinds\n")
    for (source, message), n in groups.most_common():
        print(f"{n:4} × {source}: {message}  (last {last[(source, message)]})")
    print(f"\nlatest {min(limit, len(rows))}:")
    for r in rows[-limit:]:
        print(f"{r.get('at')} {r.get('level'):7} {r.get('process') or '-':8} {r.get('source')}: {r.get('message')}")
    return 0


def _language() -> None:
    """REFRESHER_LANGUAGE from .env, so notes and the map show translations in the language the bot writes."""
    from dotenv import dotenv_values
    configured = dotenv_values(Path(__file__).resolve().parents[1] / ".env").get("REFRESHER_LANGUAGE")
    if configured:
        os.environ.setdefault("REFRESHER_LANGUAGE", configured)


def _translate(vault_path: Path, force: bool, at: datetime) -> int:
    from dotenv import load_dotenv
    from engine import language
    from engine.llm import ModelError
    from refresher import translate
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    client = translate.client_from_environment()
    if client is None:
        sys.exit("translate: needs NEBIUS_API_KEY, TRIAGE_PROVIDER=nebius and REFRESHER_LANGUAGE other than source.")
    items = sorted({ex.item_id for ex, _ in vault.load_extractions(vault_path)})
    todo = items if force else translate.missing(vault_path, items, client)
    done = failed = 0
    for item_id in todo:
        try:
            record = translate.translate_item(vault_path, item_id, client, force=force)
        except (ModelError, OSError, ValueError, KeyError) as error:
            failed += 1
            print(f"translate: {item_id[:10]} failed ({type(error).__name__})", file=sys.stderr)
            continue
        done += 1
        print(f"translate: {item_id[:10]} from {record.get('source_language') or '?'}, "
              f"{len(record['passages'])} passages")
    store = vault.replay(vault_path, include_unreviewed=True)
    vault.export(store, vault_path, at)
    graphview.write(store, vault_path, at)
    print(f"translate: {done} items into {language.name()} with {client.model_name}, {failed} failed, "
          f"{len(items) - len(todo)} already done; notes and map rebuilt")
    return 1 if failed else 0


def _embedder(vault_path: Path) -> None:
    """Nebius embeddings when NEBIUS_EMBED_MODEL is set in .env (cached in the vault), else the offline stems."""
    from dotenv import dotenv_values
    from graph import embed
    env = {**dotenv_values(Path(__file__).resolve().parents[1] / ".env"), **os.environ}  # .env without exporting it
    embed.use(embed.from_environment(vault_path, env))


def _proposals(vault_path: Path, everything: bool) -> int:
    store = vault.replay(vault_path, include_unreviewed=True)
    claims = {c.claim.claim_id: c.claim.statement for c in store.claims(hidden=True)}
    shown = [s for s in sorted(store.proposals.values(), key=lambda s: (s.proposal.at, s.proposal.proposal_id))
             if everything or s.status == "proposed"]
    for s in shown:
        p = s.proposal
        print(f"{p.proposal_id} {p.kind} [{s.status}] {p.payload.get('title') or p.payload.get('type') or ''}".rstrip())
        for ref in p.payload.get("claims", []) + p.payload.get("people", []):
            print(f"    {claims.get(ref, ref)}")
        print(f"    why: {p.rationale}" + (f"; {s.reason}" if s.reason else ""))
        print(f"    token: {s.token}")
    print(f"proposals: {sum(s.status == 'proposed' for s in store.proposals.values())} waiting")
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
        from engine.llm import BudgetedModel
        model = BudgetedModel(model)  # REFRESHER_RUN_TOKENS / REFRESHER_RUN_SECONDS cap the run
        relator = relation_stage.ModelRelator(model)
    run = place.relate_pending(vault_path, relator, log=print, warn=place._warn)
    if run.error:
        print(f"relate: stopped after {run.judged} items", file=sys.stderr)
        return 1
    print(f"relate: judged {run.judged} items, {run.failed} failed"
          + (f"; {model.calls} model calls, about {model.spent:,} tokens" if not lexical else ""))
    return 1 if run.failed else 0


def _enrich(vault_path: Path, limit: int, names: list[str], at: datetime) -> int:
    """Look up the most involved people not yet enriched (or the ones named), at most 3 Tavily searches each."""
    from dotenv import load_dotenv
    from engine.llm import ModelError, client_from_environment
    from engine.people import search_from_environment
    from graph.memory import person_id
    from refresher.consolidate import enrich_people, pick_people
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
        chosen = pick_people(vault_path, store, limit)
    done, error = enrich_people(vault_path, store, chosen, search, model, log=print,
                                warn=place._warn)
    if error:
        print(f"enrich: stopped after {done} people", file=sys.stderr)
        return 1
    vault.log(vault_path, at, f"enrich: {done} people looked up with Tavily")
    print(f"enrich: {done} people")
    return 0


def _consolidate(vault_path: Path, lexical: bool, at: datetime, if_due: bool = False,
                 every: float | None = None) -> int:
    from dotenv import load_dotenv
    from engine.llm import ModelError
    from refresher.consolidate import consolidate, models_from_environment
    if every is not None and every <= 0:
        sys.exit("consolidate: --every needs a positive number of seconds")
    if not lexical:  # a lexical run needs no providers, so .env stays out of the environment
        load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
    try:
        model, search = models_from_environment(lexical)
    except ModelError as error:
        sys.exit(str(error))
    if every is None:
        run = consolidate(vault_path, model, search, at, log=print, warn=place._warn, if_due=if_due)
        print(run.summary() if run else "consolidate: skipped, not due or nothing placed since the last run")
        return 0
    import time
    print(f"consolidate: checking every {every:g} s whether the review is due", flush=True)
    while True:  # a failed run is logged and the next check tries again, as the bot's timer does
        try:
            run = consolidate(vault_path, model, search, log=print, warn=place._warn, if_due=True)
            if run:
                print(run.summary(), flush=True)
        except (ModelError, OSError, ValueError) as error:
            place._warn(f"consolidate failed ({type(error).__name__}: {str(error)[:300]}); next check in {every:g} s")
        time.sleep(every)

if __name__ == "__main__":
    sys.exit(main())
