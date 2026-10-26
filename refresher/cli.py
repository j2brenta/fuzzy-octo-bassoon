"""refresher: rebuild the knowledge base from the vault, run the KM pass, evaluate against the seeds.

    uv run refresher rebuild [--vault V] [--at DATE]   replay processed/ + overrides, write wiki/
    add --include-unreviewed to any command to also place items that did not pass ingestion's quality gate
    uv run refresher km [--vault V] [--at DATE]        print the health report at a moment
    uv run refresher eval [--vault V] --manifest M     score against expected labels
    uv run refresher relate [--vault V] [--lexical]    judge relations for items without a relations/ verdict file
    uv run refresher review [--vault V] [--at DATE]    item duplicates and firm Ideas, stored in graph-reviews/

The vault defaults to $REFRESHER_VAULT.
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from graph import relate as relation_stage
from graph.memory import MemoryStore
from refresher import evaluate, km, vault


def _at(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="refresher", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("rebuild", "km", "eval", "relate", "review"):
        p = sub.add_parser(name)
        p.add_argument("--vault", type=Path, default=os.environ.get("REFRESHER_VAULT"))
        p.add_argument("--at", help="ISO date or datetime to score at (default: now)")
        p.add_argument("--include-unreviewed", action="store_true",
                       help="also place needs_revision/pending items, marked unreviewed (triage skips stay out)")
        if name == "eval":
            p.add_argument("--manifest", type=Path, required=True)
            p.add_argument("--out", type=Path, help="write the markdown report here")
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
        report = km.run(store, at)
        message = (f"rebuild: {report.items} items ({len(report.unreviewed)} unreviewed), {report.claims} claims, "
                   f"{report.people} people, {len(store.held_back)} held back, {len(overrides)} overrides")
        vault.log(args.vault, at, message)
        print(f"{message}; wrote {len(written)} notes to {args.vault / 'wiki'}")
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
    """Replay in event-time order and judge each item that has no current verdict file against what precedes it.
    Every item is judged, unreviewed ones too, so the files serve rebuilds with or without --include-unreviewed."""
    from pydantic import ValidationError
    from engine.llm import ModelError, ModelOutputError
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
    store = MemoryStore(include_unreviewed=True)
    judged = failed = 0
    for ex, text in vault.load_extractions(vault_path):
        relation = vault.load_relation(vault_path, ex)
        if relation is None and ex.claims:
            try:
                relation = relation_stage.judge(ex, text, store.candidate_pool(ex.item_id), relator)
            except (ModelOutputError, ValidationError) as error:  # a bad answer: the item waits for the next run
                print(f"{ex.item_id[:12]}: not judged: {error}", file=sys.stderr)
                failed += 1
            except ModelError as error:  # key, credit, rate limit or network: every other item would fail too
                print(f"{ex.item_id[:12]}: not judged: {error}", file=sys.stderr)
                print(f"relate: stopped after {judged} items", file=sys.stderr)
                return 1
            else:
                vault.save_relation(vault_path, relation)
                judged += 1
                related = sum(v.label != "NONE" for v in relation.verdicts)
                print(f"{ex.item_id[:12]}: {len(relation.verdicts)} claims, {related} related ({relator.name})")
        store.apply(ex, text, relation)
    print(f"relate: judged {judged} items, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
