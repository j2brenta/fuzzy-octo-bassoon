"""refresher: rebuild the knowledge base from the vault, run the KM pass, evaluate against the seeds.

    uv run refresher rebuild [--vault V] [--at DATE]   replay processed/ + overrides, write wiki/
    add --include-unreviewed to any command to also place items that did not pass ingestion's quality gate
    uv run refresher km [--vault V] [--at DATE]        print the health report at a moment
    uv run refresher eval [--vault V] --manifest M     score against expected labels

The vault defaults to $REFRESHER_VAULT.
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from refresher import evaluate, km, vault


def _at(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="refresher", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("rebuild", "km", "eval"):
        p = sub.add_parser(name)
        p.add_argument("--vault", type=Path, default=os.environ.get("REFRESHER_VAULT"))
        p.add_argument("--at", help="ISO date or datetime to score at (default: now)")
        p.add_argument("--include-unreviewed", action="store_true",
                       help="also place needs_revision/pending items, marked unreviewed (triage skips stay out)")
        if name == "eval":
            p.add_argument("--manifest", type=Path, required=True)
            p.add_argument("--out", type=Path, help="write the markdown report here")
    args = ap.parse_args(argv)
    if not args.vault:
        ap.error("pass --vault or set REFRESHER_VAULT")
    if not (args.vault / "processed").is_dir():
        ap.error(f"{args.vault} has no processed/ folder; run ingestion first")
    at = _at(args.at)

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
        checks = evaluate.evaluate(store, evaluate.load_expectations(args.manifest), extractions)
        text = evaluate.render(checks)
        if args.out:
            args.out.write_text(text, encoding="utf-8")
        print(text, end="")
        return 0 if all(c.ok is not False for c in checks) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
