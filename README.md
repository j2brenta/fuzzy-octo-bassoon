# Refresher

Personal knowledge engine for the Nebius x NVIDIA hackathon. Development is in
progress. The current blocks capture UTF-8 files, import a labeled seed corpus and
receive Telegram text and prepare source sections/links. Model extraction, graph placement and refreshers are
not implemented yet.

## Local setup

Use Python 3.11 or newer. Single-file capture uses the standard library; seed manifests use PyYAML.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
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
different source. Both import paths support only UTF-8 text/Markdown, not attachments. Its original `save_text` exercise remains available.

`.local-vault/` is ignored by Git. It is a local practice vault, not the personal
Obsidian vault. Do not commit private captured content.

## Block 2 — labeled seed imports

```bash
python -m channels.importer eval/seed_corpus --validate-only
python -m channels.importer eval/seed_corpus --vault .local-vault/seed
```

The manifest describes seven originals: four supplied samples and a synthetic
original/repost/correction sequence. Metadata enters the same capture function;
expected labels remain separate for future evaluation. Repeating the import
creates no additional records. See [the seed guide](eval/seed_corpus/README.md)
for fixture provenance, simulated channel IDs and current limitations.

## Block 3 — Telegram text capture

The bot uses aiogram long polling, so it can run on your laptop without a public
server. It accepts text in a private conversation with its configured owner.
Text is stored once in the top-level `text` field. Telegram-specific metadata
(sender, forwarding details, formatting and link previews) stays under
`channel_metadata`; it does not repeat the message text. Attachments, edits, channel subscriptions and extraction are future work.

### Connect your bot

1. Open the official [BotFather](https://t.me/BotFather) in Telegram and use
   `/newbot`. Choose a name and a unique username ending in `bot`.
2. Put the token in the project-root `.env` file (already ignored by Git):

   ```dotenv
   TELEGRAM_BOT_TOKEN=your_real_token_here
   TELEGRAM_OWNER_ID=
   ```

   `.env.example` contains blank placeholders only. The bot loads `.env` using
   python-dotenv; existing shell variables take precedence. Then start setup mode:

   ```bash
   python -m channels.telegram
   ```

   If no token is configured, the hidden terminal prompt remains available.
3. Open your new bot in Telegram and send `/start`. It returns **your numeric
   Telegram user ID**. Setup mode does not save messages.
4. Stop the process with Ctrl+C, then restart with that ID:

   ```bash
   python -m channels.telegram --owner-id YOUR_NUMERIC_ID
   ```

   Replace `YOUR_NUMERIC_ID` with digits, not the bot username or bot ID.
   Alternatively set `TELEGRAM_OWNER_ID` in `.env` and run without `--owner-id`.
   The command-line owner ID takes precedence over `.env`. Restart after changes.
5. Send `My first Telegram note`, then forward a text post from a channel. The
   bot replies `Captured` after saving to `.local-vault/telegram/raw/`.
6. Stop with Ctrl+C when finished. The laptop process must be running to capture.

### Behavior and boundaries

- A redelivery of the same Telegram message reuses the saved record. Sending the
  same words as a new message is a new capture, not a transport retry.
- Forwarding the same unchanged channel post again reuses its original channel
  and post identity. Forwards without an exposed original message ID use their
  delivery identity, avoiding guesses about authors or independent sources.
- Forwarders are not substituted for original authors; hidden author names are
  only hints. A channel title is a source, not proof of a person's identity.
- Files, photos (even with captions) and voice are rejected explicitly; their
  attachments are not silently discarded. Edited messages are not processed.
- This is a raw-capture acknowledgment, not a filing, deduplication or graph
  verdict. Graph relations and AI extraction are still pending.
- One polling process per token. Existing webhooks are detected, not deleted.
- Owner ID is required for capture; other users and group messages are not saved.
  No bot credentials are needed for automated tests.

See [aiogram polling docs](https://docs.aiogram.dev/en/latest/dispatcher/long_polling.html)
and [Telegram bot setup](https://core.telegram.org/bots/tutorial#obtain-your-bot-token).

## Block 4 — prepare captured text and links

Telegram now acknowledges capture first, then reports prepared section/link
counts. Link identification does not fetch pages, PDFs or YouTube transcripts.

For existing captures, run:

```bash
python -m engine.parse .local-vault/telegram
```

A rebuildable document is written to `documents/<item_id>.json`. Each text body
gets one section ID and a reference to its character range in the raw record.
The full text remains only in `raw/`; the document does not duplicate it.
Future model claims can cite this section to point back to the original.

Links include literal HTTP(S) URLs and Telegram text links (for example, the
word “report” with a hidden URL). Positions are Python character offsets;
Telegram's UTF-16 entity offsets are converted, including text containing emoji.
Formatting, sender metadata and original text remain in the raw record.

Repeating preparation reuses identical documents. Raw records are never changed.
The source hash is checked before parsing. If preparation fails after capture,
the bot says the original was saved and preparation can be retried with the CLI.
These document fields are provisional until the team agrees the shared schema.

## Check the blocks

```bash
python -m unittest discover -v
```

Checks cover exact content preservation, safe retries, independent sources,
message identity, conflict protection and blank input.

## Next blocks

1. Agree the shared RawItem/Extraction contracts with the team.
2. Expand seed labels/topics and multi-format parsing coverage.
3. Extend Telegram capture to files/voice once parser contracts are ready.
4. Parse and extract claims, then call Victor's graph interface.

## License

Apache-2.0; see [LICENSE](LICENSE).
