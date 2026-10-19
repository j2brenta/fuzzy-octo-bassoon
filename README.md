# Refresher

Personal knowledge engine for the Nebius x NVIDIA hackathon. Development is in
progress. The current block captures UTF-8 text files; model extraction,
Telegram, graph placement and refreshers are not implemented yet.

## Local setup

Use Python 3.11 or newer. No third-party packages are needed for this block.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## Block 1 — capture text and provenance

Run from the repository root:

```bash
python -m channels.importer sample_raw/tg_memory_redis.md
```

The importer reads the file and calls `store.raw.capture_text`. A JSON record
under `.local-vault/raw/` holds the exact text, source identity, content hash
and UTC capture time. JSON is a structured text format with named fields.
Unknown authorship and publication time remain unset rather than guessed.

Run the command again: it should report `Already captured` and reuse the
original record without changing its timestamp. Matching text from different
sources is preserved separately, so later graph processing can distinguish
reposts from independent evidence. With message IDs, distinct messages remain
distinct; changed text under an existing message ID is rejected for now rather
than replacing the original. Edit/version handling is a later contract decision.

The capture envelope is provisional pending the shared RawItem schema.
The file path is the importer's source identity; moving a file makes it a
different source. This block supports only UTF-8 text/Markdown, not attachments
or the planned seed manifest. Its original `save_text` exercise remains available.

`.local-vault/` is ignored by Git. It is a local practice vault, not the personal
Obsidian vault. Do not commit private captured content.

## Check the block

```bash
python -m unittest discover -v
```

Checks cover exact content preservation, safe retries, independent sources,
message identity, conflict protection and blank input.

## Next blocks

1. Agree the shared RawItem/Extraction contracts with the team.
2. Add the seed manifest and expected labels to file ingestion.
3. Connect Telegram to the same capture function.
4. Parse and extract claims, then call Victor's graph interface.

## License

Apache-2.0; see [LICENSE](LICENSE).
