"""refresher-view: browse a vault's items in the terminal and inspect each stage file with jq.

    uv run refresher-view [--vault V]     # default $REFRESHER_VAULT, else .local-vault/seed

Item list: pick a number or an id prefix. Item view: `3` runs that file's default filter,
`3 .claims | length` runs your own, `t` prints the original text, `b` goes back, `q` quits.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# Pipeline order, as each folder appears under the vault.
STAGES = ["raw", "documents", "triage", "extractions", "reviews", "repairs", "repair-reviews", "coverage",
          "coverage-reviews", "evidence", "evidence-reviews", "entities", "entity-reviews", "processed", "placements"]
CLAIMS = "[.claims[] | {statement, kind, durability_class, confidence}]"
REVIEW = ".review | {status, summary, flagged: [.claims[] | select(.verdict != \"supported\")], omitted: .omitted_concepts}"
FILTERS = {
    "raw": "{source_name, author_hint, forwarded_by, captured_at, published_at, url, text}",
    "documents": "{sections: [.sections[] | {section_id, kind}], links}",
    "triage": ".labels + {model: .model_name}",
    "extractions": CLAIMS, "repairs": CLAIMS, "coverage": CLAIMS, "evidence": CLAIMS,
    "reviews": REVIEW, "repair-reviews": REVIEW, "coverage-reviews": REVIEW, "evidence-reviews": REVIEW,
    "entities": "[.entities[] | {name, kind, aliases}]",
    "entity-reviews": ".review | {status, summary, flagged: [.entities[] | select(.verdict != \"supported\")], "
                      "omitted: .omitted_entities}",
    "processed": "{quality_status, quality_gate_passed, claim_candidate, triage, "
                 "claim_review: .reviews.claims.status, entity_review: .reviews.entities.status, "
                 "claims: [.claims[] | .statement], entities: [.entities[] | \"\\(.name) (\\(.kind))\"]}",
    "placements": "{status, reason, quality_status, graph_applied}",
}


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def headline(text: str, width: int = 60) -> str:
    for line in text.splitlines():
        line = re.sub(r"^[#>*\-\s]+", "", line).strip()
        if line and not re.match(r"^.+, \[\d{1,2} \w{3} \d{4} at [\d:]+\]:$", line):  # skip Telegram copy headers
            return line if len(line) <= width else line[: width - 1] + "…"
    return "(empty)"


def items(vault: Path) -> list[dict]:
    rows = []
    for raw_path in sorted((vault / "raw").glob("*.json")):
        raw = _read(raw_path)
        processed = _read(vault / "processed" / raw_path.name)
        triage = _read(vault / "triage" / raw_path.name).get("labels") or {}
        author = (raw.get("author_hint") or {}).get("name") or ""
        if not author and raw.get("forwarded_by"):
            fwd = raw["forwarded_by"]
            author = "fwd: " + (fwd.get("name", "") if isinstance(fwd, dict) else fwd)
        rows.append({
            "id": raw_path.stem, "headline": headline(raw.get("text", "")), "author": author,
            "event": raw.get("published_at") or raw.get("captured_at") or "",
            "status": processed.get("quality_status", "not packaged"),
            "claims": len(processed.get("claims", [])), "kind": triage.get("kind", ""),
        })
    return sorted(rows, key=lambda r: r["event"])


def stage_files(vault: Path, item_id: str) -> list[tuple[str, Path]]:
    return [(s, vault / s / f"{item_id}.json") for s in STAGES if (vault / s / f"{item_id}.json").exists()]


def show(text: str) -> None:
    """Page long output through less, keeping jq's colours."""
    lines = text.count("\n")
    if sys.stdout.isatty() and lines > shutil.get_terminal_size().lines - 2 and shutil.which("less"):
        subprocess.run(["less", "-R"], input=text, text=True)
    else:
        print(text, end="" if text.endswith("\n") else "\n")


def run_jq(path: Path, expr: str) -> str:
    if not shutil.which("jq"):
        return json.dumps(_read(path), ensure_ascii=False, indent=2) + "\n(jq not installed: showing the whole file)\n"
    color = ["-C"] if sys.stdout.isatty() else []
    done = subprocess.run(["jq", *color, expr, str(path)], capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else f"jq error: {done.stderr.strip()}\n"


def print_items(rows: list[dict]) -> None:
    print(f"\n{'#':>3}  {'id':8}  {'status':14} {'cl':>3}  {'kind':16} {'author':18} headline")
    for n, r in enumerate(rows, 1):
        print(f"{n:>3}  {r['id'][:8]}  {r['status']:14} {r['claims']:>3}  {r['kind'][:16]:16} "
              f"{r['author'][:18]:18} {r['headline']}")


def pick(rows: list[dict], answer: str) -> dict | None:
    if answer.isdigit() and 1 <= int(answer) <= len(rows):
        return rows[int(answer) - 1]
    matches = [r for r in rows if r["id"].startswith(answer)]
    return matches[0] if len(matches) == 1 else None


def item_loop(vault: Path, row: dict, ask) -> bool:
    """Returns False when the user quits."""
    while True:
        files = stage_files(vault, row["id"])
        print(f"\n{row['headline']}\n{row['id']}  {row['status']}  {row['author']}  {row['event']}")
        for n, (stage, _) in enumerate(files, 1):
            print(f"  {n:>2}  {stage}")
        answer = ask("file [jq filter] | t text | b back | q quit > ").strip()
        if answer in ("q", "quit"):
            return False
        if answer in ("b", ""):
            return True
        if answer == "t":
            show(_read(vault / "raw" / f"{row['id']}.json").get("text", ""))
            continue
        number, _, expr = answer.partition(" ")
        if not number.isdigit() or not 1 <= int(number) <= len(files):
            print("pick a file number from the list")
            continue
        stage, path = files[int(number) - 1]
        expr = expr.strip() or FILTERS.get(stage, ".")
        print(f"$ jq '{expr}' {path.relative_to(vault)}")
        show(run_jq(path, expr))


def main(argv=None, ask=input) -> int:
    ap = argparse.ArgumentParser(prog="refresher-view", description=__doc__.splitlines()[0])
    ap.add_argument("--vault", type=Path, default=Path(os.environ.get("REFRESHER_VAULT") or ".local-vault/seed"))
    args = ap.parse_args(argv)
    if not (args.vault / "raw").is_dir():
        ap.error(f"{args.vault} has no raw/ folder")
    try:
        while True:
            rows = items(args.vault)
            print_items(rows)
            answer = ask("item # or id prefix | r refresh | q quit > ").strip()
            if answer in ("q", "quit"):
                return 0
            if answer in ("r", ""):
                continue
            row = pick(rows, answer)
            if row is None:
                print("no single item matches")
            elif not item_loop(args.vault, row, ask):
                return 0
    except (EOFError, KeyboardInterrupt):
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main())
