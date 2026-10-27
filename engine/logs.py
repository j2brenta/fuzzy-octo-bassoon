"""Logging for the bot, the jury frontend and the CLI: one line per pipeline stage and per model call, and every
problem kept in the vault for review.

The console gets LOG_LEVEL (INFO by default for the long-running processes; DEBUG also shows aiogram's per-update
lines, WARNING only problems). With a vault, every warning and error is also appended to <vault>/logs/problems.jsonl,
one JSON object per line, rotated at 5 MB (problems.jsonl.1 … .3), whatever the console level; `refresher problems`
reads it. Lines name items by id and say what failed and why, never the captured text or the claims.
"""
import json
import logging
import logging.handlers
import os
import re
from datetime import datetime, timezone
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
PROBLEMS = Path("logs") / "problems.jsonl"
ENABLED = True  # tests turn it off: the bot's default vault is the real one
ITEM = re.compile(r"\b(?:item |message )?([0-9a-f]{10,64})\b")  # the item id a line names, for filtering


class ProblemLog(logging.handlers.RotatingFileHandler):
    """Warnings and errors, as JSON lines, in <vault>/logs/problems.jsonl; the file appears with the first problem."""

    def __init__(self, vault: Path):
        if not ENABLED:
            raise OSError("the problem log is off")
        (Path(vault) / PROBLEMS).parent.mkdir(parents=True, exist_ok=True)
        super().__init__(Path(vault) / PROBLEMS, maxBytes=5_000_000, backupCount=3, encoding="utf-8", delay=True)
        self.setLevel(logging.WARNING)
        self.setFormatter(JsonLines())


def attach(vault: Path, process: str | None = None) -> None:
    """Keep this process's problems in the vault without touching the console (for the CLI, which prints its own
    output): one problem log on the root logger, replacing an earlier one."""
    root = logging.getLogger()
    for old in [h for h in root.handlers if isinstance(h, ProblemLog)]:
        root.removeHandler(old)
        old.close()
    try:
        root.addHandler(ProblemLog(vault))
    except OSError:
        return
    if process:
        os.environ["REFRESHER_PROCESS"] = process


class JsonLines(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        found = ITEM.search(message)
        row = {"at": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="seconds"),
               "level": record.levelname, "source": record.name, "message": message,
               "item": found.group(1)[:12] if found else None, "process": os.environ.get("REFRESHER_PROCESS")}
        if record.exc_info:
            row["error"] = f"{record.exc_info[0].__name__}"  # the type only: a traceback can quote the content
        return json.dumps(row, ensure_ascii=False)


def configure(env=None, vault: Path | None = None, default: str = "INFO", process: str | None = None) -> None:
    env = os.environ if env is None else env
    name = (env.get("LOG_LEVEL") or default).strip().upper()
    level = logging.getLevelName(name) if isinstance(logging.getLevelName(name), int) else logging.getLevelName(default)
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter(FORMAT))
    handlers: list[logging.Handler] = [console]
    if vault is not None:
        try:
            handlers.append(ProblemLog(vault))
        except OSError:
            pass  # a read-only vault still runs; problems then reach the console only
    if process:
        os.environ["REFRESHER_PROCESS"] = process
    logging.basicConfig(level=min(level, logging.WARNING), handlers=handlers, force=True)
    if level > logging.DEBUG:
        logging.getLogger("aiogram.event").setLevel(logging.WARNING)  # "Update id=… is handled" per message


def read_problems(vault: Path) -> list[dict]:
    """Every stored problem, oldest first, across the rotated files."""
    rows = []
    for path in sorted(Path(vault).glob(str(PROBLEMS) + "*")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return sorted(rows, key=lambda r: r.get("at") or "")
