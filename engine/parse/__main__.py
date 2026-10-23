"""Prepare all raw JSON captures in a local vault."""

import argparse
from pathlib import Path

from engine.parse import parse_capture


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vault", type=Path, help="Vault containing a raw/ folder")
    args = parser.parse_args()
    paths = sorted((args.vault / "raw").glob("*.json"))
    if not paths:
        parser.exit(1, "No raw JSON captures found in this vault.\n")
    failed = 0
    for path in paths:
        try:
            result = parse_capture(path)
        except (OSError, ValueError) as error:
            failed += 1
            print(f"Could not prepare {path.name}: {type(error).__name__}")
            continue
        status = "Already prepared" if result.reused else "Prepared"
        print(f"{status} {path.stem}: {result.section_count} section(s), {result.link_count} link(s).")
    print(f"Processed {len(paths)} captures: {len(paths) - failed} prepared, {failed} failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
