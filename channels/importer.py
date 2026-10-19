"""Import a UTF-8 text file through the shared capture block."""

import argparse
from pathlib import Path

from store.raw import capture_text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="UTF-8 text or Markdown file")
    parser.add_argument("--vault", type=Path, default=Path(".local-vault"))
    parser.add_argument("--source-name", help="Display name; defaults to filename")
    args = parser.parse_args()

    try:
        source = args.file.resolve(strict=True)
        with source.open(encoding="utf-8", newline="") as file:
            text = file.read()
        result = capture_text(
            text,
            args.vault,
            channel="file",
            source_id=source.as_uri(),
            source_name=args.source_name or source.name,
        )
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"Import failed: {error}\n")

    print("Already captured" if result.duplicate else "Captured")
    print(result.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
