"""Import a UTF-8 file or a seed corpus through the shared capture block."""

import argparse
from pathlib import Path

from store.raw import capture_text
from channels.pasted import telegram_copy_metadata


from channels.seed_raw import import_corpus, import_file, parse_linkedin_paste, parse_telegram_desktop


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="Text/Markdown file, manifest.yaml, or corpus directory")
    parser.add_argument("--vault", type=Path, default=Path(".local-vault"))
    parser.add_argument("--source-name", help="Display name for a single-file import")
    parser.add_argument("--validate-only", action="store_true", help="Check a seed manifest without writing records")
    parser.add_argument("--copy-timezone", help="IANA timezone for copied Telegram chat timestamps; does not set publication time")
    parser.add_argument("--check", action="store_true", help="Check committed RawItem fixtures")
    args = parser.parse_args(argv)
    if args.check:
        from channels.seed_raw import main as seed_raw_main
        return seed_raw_main([str(args.file), "--check"])

    try:
        source = args.file.resolve(strict=True)
        manifest = source / "manifest.yaml" if source.is_dir() else source
        if source.is_dir() or source.suffix.lower() in {".yaml", ".yml"}:
            if args.source_name:
                raise ValueError("Set source names in the manifest for seed imports.")
            try:
                from channels.seed import import_seed, load_manifest
                import yaml
            except ImportError:
                raise ValueError("Install seed-import dependencies: python -m pip install -r requirements.txt") from None
            try:
                items = load_manifest(manifest, copy_timezone=args.copy_timezone)
            except yaml.YAMLError as error:
                raise ValueError(f"Invalid YAML: {error}") from None
            if args.validate_only:
                print(f"Valid manifest: {len(items)} items; no records written.")
                return 0
            results = import_seed(items, args.vault)
            for seed_id, result in results:
                print(f"{seed_id}: {'Already captured' if result.duplicate else 'Captured'}")
            created = sum(not result.duplicate for _, result in results)
            print(f"Imported {len(results)} items: {created} new, {len(results) - created} already captured.")
            print(f"Records: {args.vault / 'raw'}")
            return 0

        if args.validate_only:
            raise ValueError("--validate-only requires a seed manifest or corpus directory.")
        with source.open(encoding="utf-8", newline="") as file:
            text = file.read()
        copied = telegram_copy_metadata(text, args.copy_timezone)
        result = capture_text(
            text,
            args.vault,
            channel="file",
            source_id=source.as_uri(),
            source_name=args.source_name or source.name,
            channel_metadata={"telegram_copy": copied} if copied else None,
            forwarded_by=copied["messages"][0]["sender_hint"] if copied and len(copied["messages"]) == 1 else None,
        )
    except (OSError, UnicodeError, ValueError) as error:
        parser.exit(1, f"Import failed: {error}\n")

    print("Already captured" if result.duplicate else "Captured")
    print(result.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
