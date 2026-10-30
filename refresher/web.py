"""The capture workspace behind /capture/ in `refresher serve` (refresher/app.py); `python -m refresher.web` is the
same app, kept for old commands."""
import argparse
import os
from pathlib import Path

from refresher.workspace import MAX_FILE, Workspace, read_json, recovery  # noqa: F401  (imported from here)

STATIC = Path(__file__).with_name("static")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="bind address; the container uses 0.0.0.0 behind a loopback port")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--vault", type=Path, default=os.environ.get("REFRESHER_VAULT"))
    args = parser.parse_args()
    if not args.vault:
        parser.error("pass --vault or set REFRESHER_VAULT")
    from refresher import cli
    return cli.main(["serve", "--vault", str(args.vault), "--host", args.host, "--port", str(args.port)])


if __name__ == "__main__":
    raise SystemExit(main())
