"""Seed RawItem generator: turns saved originals plus raw_manifest.yaml into RawItem JSON.

    uv run refresher-seed-raw eval/seed_corpus          # write raw/*.json
    uv run refresher-seed-raw eval/seed_corpus --check  # fail if raw/ is stale
"""
import argparse
import email
import email.policy
import email.utils
import json
import mimetypes
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from refresher.schemas import Attachment, AuthorHint, RawItem

# Telegram Desktop "Copy" of a message: "Kirill R, [7 Oct 2026 at 12:13:38]:"
TG_HEADER = re.compile(r"^(?P<name>.+?), \[(?P<d>\d{1,2} \w{3} \d{4}) at (?P<t>\d{2}:\d{2}:\d{2})\]:\s*$", re.M)
# "October 7, 2026", "Oct 7, 2026", optionally prefixed with "Published"
LI_DATE = re.compile(r"^(?:Published )?(?P<d>[A-Z][a-z]{2,8} \d{1,2}, \d{4})\s*$", re.M)
TEXT_SUFFIXES = {".md", ".txt"}
# Manifest keys copied onto the RawItem; everything else (labels, notes) is for the eval only.
OVERRIDES = ("channel", "source_name", "author_hint", "forwarded_by", "captured_at", "published_at", "url", "kind")


@dataclass
class Result:
    file: str
    items: list[RawItem] = field(default_factory=list)
    status: str = "ok"


def _parse_dt(value, tz: ZoneInfo) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def detect_format(path: Path, text: str) -> str:
    if path.suffix == ".eml":
        return "email"
    if path.suffix not in TEXT_SUFFIXES:
        return "binary"
    if TG_HEADER.search(text):
        return "telegram_desktop"
    if path.stem.startswith("linkedin"):
        return "linkedin_paste"
    return "plain"


def parse_telegram_desktop(text: str, tz: ZoneInfo) -> list[dict]:
    """One dict per message. The header names who sent it in the chat, i.e. who forwarded it to us."""
    heads = list(TG_HEADER.finditer(text))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        when = datetime.strptime(f"{m['d']} {m['t']}", "%d %b %Y %H:%M:%S").replace(tzinfo=tz)
        out.append({"forwarded_by": {"name": m["name"]}, "captured_at": when,
                    "text": text[m.end():end].strip()})
    return out


def parse_linkedin_paste(text: str, tz: ZoneInfo) -> list[dict]:
    """Browser copy of a post or Pulse article: optional '# Title', author, headline, then the date line."""
    fields = {"text": text.strip()}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    if lines:
        fields["author_hint"] = {"name": lines[0]}
    if m := LI_DATE.search(text):
        d = m["d"]
        fmt = "%b %d, %Y" if len(d.split()[0]) == 3 else "%B %d, %Y"
        fields["published_at"] = datetime.strptime(d, fmt).replace(tzinfo=tz)
    return [fields]


def parse_email(raw: bytes, tz: ZoneInfo) -> list[dict]:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    name, addr = email.utils.parseaddr(str(msg.get("From", "")))
    body = msg.get_body(preferencelist=("plain", "html"))
    fields = {"channel": "email", "author_hint": {"name": name or None, "email": addr or None},
              "source_name": msg.get("List-Id"), "text": body.get_content().strip() if body else ""}
    if msg.get("Subject"):
        fields["text"] = f"{msg['Subject']}\n\n{fields['text']}"
    if msg.get("Date"):
        fields["published_at"] = _parse_dt(email.utils.parsedate_to_datetime(msg["Date"]), tz)
    return [fields]


def parse_binary(path: Path, root: Path) -> list[dict]:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    kind = {"image": "image", "audio": "voice", "video": "video"}.get(mime.split("/")[0], "file")
    return [{"kind": kind, "attachments": [{"mime": mime, "path": str(path.relative_to(root)), "name": path.name}]}]


def import_file(path: Path, root: Path, entry: dict, defaults: dict, tz: ZoneInfo) -> Result:
    res = Result(path.name)
    if entry.get("status") == "skip":
        res.status = "skip"
        return res
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace") if path.suffix in TEXT_SUFFIXES else ""
    if not raw.strip():
        res.status = "TODO: empty"
        return res
    fmt = entry.get("format") or detect_format(path, text)
    parsed = {
        "telegram_desktop": lambda: parse_telegram_desktop(text, tz),
        "linkedin_paste": lambda: parse_linkedin_paste(text, tz),
        "plain": lambda: [{"text": text.strip()}],
        "email": lambda: parse_email(raw, tz),
        "binary": lambda: parse_binary(path, root),
    }[fmt]()

    for n, fields in enumerate(parsed, 1):
        data = {"kind": "text", **defaults, **fields, "source_id": f"seed:{path.stem}#{n}"}
        data.update({k: entry[k] for k in OVERRIDES if k in entry})
        for k in ("captured_at", "published_at"):
            if data.get(k) is not None:
                data[k] = _parse_dt(data[k], tz)
        if "captured_at" not in data:
            res.status = "error: no captured_at (add it to the manifest)"
            res.items = []
            return res
        for k in ("author_hint", "forwarded_by"):
            if isinstance(data.get(k), dict):
                data[k] = AuthorHint(**data[k])
        data["attachments"] = [Attachment(**a) for a in data.get("attachments", [])]
        res.items.append(RawItem(**data))
    return res


def load_manifest(root: Path) -> dict:
    path = root / "raw_manifest.yaml"
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def import_corpus(root: Path) -> list[Result]:
    manifest = load_manifest(root)
    tz = ZoneInfo(manifest.get("timezone", "UTC"))
    defaults = manifest.get("defaults", {})
    entries = manifest.get("items") or {}
    results = []
    for path in sorted((root / "originals").iterdir()):
        if path.name.startswith("."):
            continue
        if path.name not in entries:
            print(f"warning: {path.name} is not in raw_manifest.yaml, using defaults", file=sys.stderr)
        results.append(import_file(path, root, entries.get(path.name) or {}, defaults, tz))
    return results


def render(results: list[Result]) -> dict[str, str]:
    """File name under raw/ -> JSON text. Stable so --check can compare byte for byte."""
    out = {}
    for res in results:
        stem = Path(res.file).stem
        for n, item in enumerate(res.items, 1):
            name = f"{stem}.json" if len(res.items) == 1 else f"{stem}-{n}.json"
            data = item.model_dump(mode="json", exclude_none=True)
            out[name] = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", type=Path, help="corpus folder with originals/ and raw_manifest.yaml")
    ap.add_argument("--check", action="store_true", help="fail if raw/ differs from a fresh import")
    args = ap.parse_args(argv)

    results = import_corpus(args.root)
    files = render(results)
    for res in results:
        item = res.items[0] if res.items else None
        author = item.author_hint.name if item and item.author_hint else "-"
        fwd = item.forwarded_by.name if item and item.forwarded_by else "-"
        print(f"{res.file:40} {len(res.items):>2}  {item.kind if item else '-':5}  "
              f"{author or '-':22} {fwd:12} {res.status}")

    raw_dir = args.root / "raw"
    if args.check:
        on_disk = {p.name: p.read_text() for p in raw_dir.glob("*.json")} if raw_dir.exists() else {}
        stale = sorted(n for n in files.keys() | on_disk.keys() if files.get(n) != on_disk.get(n))
        if stale:
            print(f"stale: {', '.join(stale)}; rerun without --check", file=sys.stderr)
            return 1
        return 0

    raw_dir.mkdir(exist_ok=True)
    for old in raw_dir.glob("*.json"):
        if old.name not in files:
            old.unlink()
    for name, body in files.items():
        (raw_dir / name).write_text(body)
    failed = [r for r in results if r.status.startswith("error")]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
