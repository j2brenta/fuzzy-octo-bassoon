"""refresher-view: browse a vault's items in the terminal and inspect each stage file with jq.

    uv run refresher-view [--vault V] [--plain]    # default $REFRESHER_VAULT, else .local-vault/seed

Items: ↑/↓ (or j/k) to move, Enter or → to open, r to reload, q to quit.
Stage files: ↑/↓ to move, Enter runs the stage's jq filter, / runs your own filter, t shows the
original text, ← / Esc / b goes back. Stages with problems are marked ✗ with a short reason.

--plain (or a non-terminal stdin) uses numbered prompts instead: `3`, `3 .claims | length`, `t`, `b`, `q`.
"""
import argparse
import curses
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
# Stages every kept item should reach; the optional correction stages are only listed when present.
CORE = ["raw", "documents", "triage", "extractions", "reviews", "entities", "entity-reviews", "processed", "placements"]
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


def problem(stage: str, data: dict) -> str:
    """Short reason a stage file is not clean, or "" when it is."""
    if stage == "triage":
        return "" if (data.get("labels") or {}).get("keep", True) else "triage: not kept"
    if stage.endswith("reviews"):
        review = data.get("review") or {}
        if review.get("status") == "accepted":
            return ""
        rows = review.get("entities" if stage == "entity-reviews" else "claims") or []
        omitted = review.get("omitted_entities" if stage == "entity-reviews" else "omitted_concepts") or []
        flagged = sum(r.get("verdict") != "supported" for r in rows)
        return f"{review.get('status', 'no status')}: {flagged} flagged, {len(omitted)} omitted"
    if stage == "processed":
        parts = [] if data.get("quality_gate_passed") else [f"quality {data.get('quality_status')}"]
        for name, review in (data.get("reviews") or {}).items():
            if (review or {}).get("status") != "accepted":
                parts.append(f"{name} review {(review or {}).get('status')}")
        return ", ".join(parts)
    if stage == "placements":
        return "" if data.get("status") == "stored_only" or data.get("graph_applied") else str(data.get("status"))
    return ""


def stages(vault: Path, item_id: str) -> list[dict]:
    """Stage files for an item, with missing core stages listed so failed steps stay visible."""
    out = []
    for s in STAGES:
        path = vault / s / f"{item_id}.json"
        if path.exists():
            out.append({"stage": s, "path": path, "problem": problem(s, _read(path))})
        elif s in CORE:
            out.append({"stage": s, "path": None, "problem": "missing: this step did not finish"})
    return out


def run_jq(path: Path, expr: str, color: bool) -> str:
    if not shutil.which("jq"):
        return json.dumps(_read(path), ensure_ascii=False, indent=2) + "\n(jq not installed: showing the whole file)\n"
    done = subprocess.run(["jq", *(["-C"] if color else []), expr, str(path)], capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else f"jq error: {done.stderr.strip()}\n"


# ---------- plain mode: numbered prompts, used for pipes and tests ----------

def show(text: str) -> None:
    """Page long output through less, keeping jq's colours."""
    if sys.stdout.isatty() and text.count("\n") > shutil.get_terminal_size().lines - 2 and shutil.which("less"):
        subprocess.run(["less", "-R"], input=text, text=True)
    else:
        print(text, end="" if text.endswith("\n") else "\n")


def item_row(r: dict) -> str:
    return (f"{r['id'][:8]}  {r['status']:14} {r['claims']:>3}  {r['kind'][:16]:16} "
            f"{r['author'][:18]:18} {r['headline']}")


def stage_row(s: dict) -> str:
    return f"{'✗' if s['problem'] else '✓'} {s['stage']:17} {s['problem']}"


def pick(rows: list[dict], answer: str) -> dict | None:
    if answer.isdigit() and 1 <= int(answer) <= len(rows):
        return rows[int(answer) - 1]
    matches = [r for r in rows if r["id"].startswith(answer)]
    return matches[0] if len(matches) == 1 else None


def plain_item(vault: Path, row: dict, ask) -> bool:
    """Returns False when the user quits."""
    while True:
        files = stages(vault, row["id"])
        print(f"\n{row['headline']}\n{row['id']}  {row['status']}  {row['author']}  {row['event']}")
        for n, s in enumerate(files, 1):
            print(f"  {n:>2}  {stage_row(s)}")
        answer = ask("file [jq filter] | t text | b back | q quit > ").strip()
        if answer in ("q", "quit"):
            return False
        if answer in ("b", ""):
            return True
        if answer == "t":
            show(_read(vault / "raw" / f"{row['id']}.json").get("text", ""))
            continue
        number, _, expr = answer.partition(" ")
        if not number.isdigit() or not 1 <= int(number) <= len(files) or files[int(number) - 1]["path"] is None:
            print("pick a file number from the list")
            continue
        s = files[int(number) - 1]
        expr = expr.strip() or FILTERS.get(s["stage"], ".")
        print(f"$ jq '{expr}' {s['path'].relative_to(vault)}")
        show(run_jq(s["path"], expr, sys.stdout.isatty()))


def plain(vault: Path, ask) -> int:
    try:
        while True:
            rows = items(vault)
            print(f"\n{'#':>3}  {'id':8}  {'status':14} {'cl':>3}  {'kind':16} {'author':18} headline")
            for n, r in enumerate(rows, 1):
                print(f"{n:>3}  {item_row(r)}")
            answer = ask("item # or id prefix | r refresh | q quit > ").strip()
            if answer in ("q", "quit"):
                return 0
            if answer in ("r", ""):
                continue
            row = pick(rows, answer)
            if row is None:
                print("no single item matches")
            elif not plain_item(vault, row, ask):
                return 0
    except (EOFError, KeyboardInterrupt):
        print()
        return 0


# ---------- curses mode: arrow keys ----------

UP, DOWN = (curses.KEY_UP, ord("k")), (curses.KEY_DOWN, ord("j"))
OPEN, BACK = (curses.KEY_ENTER, 10, 13, curses.KEY_RIGHT, ord("l")), (curses.KEY_LEFT, 27, ord("b"), ord("h"))


def _put(win, y: int, x: int, text: str, attr=0) -> None:
    height, width = win.getmaxyx()
    if 0 <= y < height and x < width:
        win.addnstr(y, x, text, max(0, width - x - 1), attr)


def _page(stdscr, text: str) -> None:
    """Leave curses, show text in less (or plain stdout), come back."""
    curses.def_prog_mode()
    curses.endwin()
    if shutil.which("less"):
        subprocess.run(["less", "-R", "+g"], input=text, text=True)
    else:
        print(text)
        input("-- Enter to return --")
    curses.reset_prog_mode()
    stdscr.refresh()


def _prompt(stdscr, label: str, initial: str) -> str | None:
    height, _ = stdscr.getmaxyx()
    curses.curs_set(1)
    curses.echo()
    stdscr.move(height - 1, 0)
    stdscr.clrtoeol()
    _put(stdscr, height - 1, 0, label)
    stdscr.refresh()
    try:
        raw = stdscr.getstr(height - 1, len(label), 400).decode("utf-8", "replace").strip()
    except KeyboardInterrupt:
        raw = ""
    finally:
        curses.noecho()
        curses.curs_set(0)
    return raw or initial or None


def _menu(stdscr, title: list[str], rows: list[str], marks: list[int], footer: str, pos: int, detail=None) -> int:
    """Draw a list with a highlighted row; returns the key pressed. marks: colour pair per row."""
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    for y, line in enumerate(title):
        _put(stdscr, y, 0, line, curses.A_BOLD if y == 0 else curses.A_DIM)
    top = len(title) + 1
    detail_lines = detail(pos) if detail else []
    room = max(1, height - top - 2 - (len(detail_lines) + 1 if detail_lines else 0))
    first = min(max(0, pos - room + 1), max(0, len(rows) - room))
    for i, line in enumerate(rows[first:first + room]):
        n = first + i
        attr = curses.color_pair(marks[n]) if marks[n] else 0
        if n == pos:
            attr |= curses.A_REVERSE
        _put(stdscr, top + i, 0, f" {line}".ljust(width - 1), attr)
    y = top + room + 1
    for line in detail_lines:
        _put(stdscr, y, 2, line, curses.A_DIM)
        y += 1
    _put(stdscr, height - 1, 0, footer, curses.A_DIM)
    stdscr.refresh()
    return stdscr.getch()


def _wrap(text: str, width: int, limit: int) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    lines.append(line)
    return [l for l in lines if l][:limit]


def _summary(s: dict) -> str:
    if s["path"] is None:
        return "No file: the step failed or was not run. Check the pipeline log for its message."
    data = _read(s["path"])
    review = data.get("review") or {}
    if review.get("summary"):
        return review["summary"]
    if s["stage"] == "triage":
        return (data.get("labels") or {}).get("reason", "")
    if s["stage"] == "placements":
        return str(data.get("reason", ""))
    return ""


def tui(stdscr, vault: Path) -> int:
    curses.curs_set(0)
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    tone = {"accepted": 2, "needs_revision": 1, "pending": 3, "not packaged": 3, "skipped": 0}
    pos = 0
    while True:
        rows = items(vault)
        if not rows:
            _menu(stdscr, [f"refresher-view  {vault}", "no items in raw/"], [], [], "q quit", 0)
            return 0
        pos = min(pos, len(rows) - 1)
        header = f"{'id':8}  {'status':14} {'cl':>3}  {'kind':16} {'author':18} headline"
        key = _menu(stdscr, [f"refresher-view  {vault}  ({len(rows)} items)", header], [item_row(r) for r in rows],
                    [tone.get(r["status"], 0) for r in rows], "↑/↓ move  Enter open  r reload  q quit", pos)
        if key in UP:
            pos = max(0, pos - 1)
        elif key in DOWN:
            pos = min(len(rows) - 1, pos + 1)
        elif key in (ord("q"), 27):
            return 0
        elif key in OPEN and not tui_item(stdscr, vault, rows[pos]):
            return 0


def tui_item(stdscr, vault: Path, row: dict) -> bool:
    """Returns False when the user quits."""
    pos = 0
    while True:
        files = stages(vault, row["id"])
        width = stdscr.getmaxyx()[1]
        title = [row["headline"], f"{row['id']}  {row['status']}  {row['author']}  {row['event']}"]
        detail = lambda n: _wrap(_summary(files[n]), width - 4, 4)  # noqa: E731
        key = _menu(stdscr, title, [stage_row(s) for s in files], [1 if s["problem"] else 2 for s in files],
                    "↑/↓ move  Enter default filter  / own filter  t text  ← back  q quit", pos, detail)
        s = files[pos]
        if key in UP:
            pos = max(0, pos - 1)
        elif key in DOWN:
            pos = min(len(files) - 1, pos + 1)
        elif key in BACK:
            return True
        elif key == ord("q"):
            return False
        elif key == ord("t"):
            _page(stdscr, _read(vault / "raw" / f"{row['id']}.json").get("text", ""))
        elif key in OPEN and s["path"] is not None:
            expr = FILTERS.get(s["stage"], ".")
            _page(stdscr, f"$ jq '{expr}' {s['path'].relative_to(vault)}\n" + run_jq(s["path"], expr, True))
        elif key == ord("/") and s["path"] is not None:
            expr = _prompt(stdscr, "jq filter (Enter for the default): ", FILTERS.get(s["stage"], "."))
            if expr:
                _page(stdscr, f"$ jq '{expr}' {s['path'].relative_to(vault)}\n" + run_jq(s["path"], expr, True))


def main(argv=None, ask=None) -> int:
    ap = argparse.ArgumentParser(prog="refresher-view", description=__doc__.splitlines()[0])
    ap.add_argument("--vault", type=Path, default=Path(os.environ.get("REFRESHER_VAULT") or ".local-vault/seed"))
    ap.add_argument("--plain", action="store_true", help="numbered prompts instead of arrow keys")
    args = ap.parse_args(argv)
    if not (args.vault / "raw").is_dir():
        ap.error(f"{args.vault} has no raw/ folder")
    if ask is not None or args.plain or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return plain(args.vault, ask or input)
    os.environ.setdefault("ESCDELAY", "25")  # make Esc respond quickly
    try:
        return curses.wrapper(tui, args.vault)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
