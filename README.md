# Refresher

Personal knowledge engine for the Nebius x NVIDIA hackathon. Development is in
progress. The current blocks capture UTF-8 files, import a labeled seed corpus and
receive Telegram text and prepare source sections/links. Atomic claim extraction is also available. Entity extraction, graph placement
and refreshers are not implemented yet.

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

## Block 5 — AI triage through Backboard (development)

Nebius access is pending. Backboard currently supplies triage through the small
`JsonModel` interface; the rest of the pipeline does not depend on its API.
This temporary route alone does not fulfill the hackathon's required Nebius
runtime usage. NVIDIA model and Nebius integration remain submission work.

Add these settings to your **real `.env`**, keeping your Telegram settings:

```dotenv
TRIAGE_PROVIDER=backboard
BACKBOARD_API_KEY=your_actual_key_here
BACKBOARD_LLM_PROVIDER=openai
BACKBOARD_MODEL=gpt-4o-mini
```

This uses the model through Backboard; a separate OpenAI key is not needed.
Provider/model availability depends on your Backboard account. Change the model
settings if necessary. `TRIAGE_PROVIDER=off` disables remote triage while keeping
capture and preparation working. Restart the bot after configuration changes.

Substantive conversations, brainstorming and Q&A should be kept too; triage judges
content rather than rejecting chat format. Each forwarded Telegram message is
currently processed independently. Paste the relevant exchange as one message
when context matters. Automatic conversation grouping and a manual Keep override
are not implemented yet. The triage-v2 rubric is cached separately from v1.

After capture/preparation, enabled triage sends **only the note text** to
Backboard and asks for five fields: keep, kind, durability_class, split_digest
and a short reason. Memory and web search are off, with a new conversation per
call; Backboard can still retain conversation history. The source text is
untrusted data and does not control system instructions or invoke tools.

A validated result is saved under `triage/<item_id>.json`, including the source
hash, prompt version, requested/actual model and reported token usage. Existing
results are reused for the same source/prompt/model. Raw captures never change,
even for a skip decision. Token usage describes the final response; retry
usage may be additional. Invalid output gets at most one retry; service/auth
errors stop without deleting the original. Inputs over 60,000 characters wait
for later section processing rather than being silently truncated.

To triage a saved item manually (replace the ID):

```bash
python -m engine.triage .local-vault/telegram --item-id YOUR_ITEM_ID
```

Omit `--item-id` to process all captures. `--force` repeats model calls even when
results exist and can incur additional usage. A classification is a model
judgment, not factual verification. Digest splitting is only flagged, not yet
performed; claim extraction is the next stage below and graph integration is pending.

API reference: [Backboard send message](https://docs.backboard.io/concepts/messages).

## Block 6 — claims with exact source references

For a keep decision, Telegram makes one additional model call to extract at most
25 central atomic claims. Skip means no extraction call, not deletion. Each claim
has its own kind and durability, so a mixed article can contain a foundational
principle and a short-lived forecast. The model is instructed to preserve modal
words such as “may”, negations, conditions, dates and speaker attribution.

Python numbers nonblank source lines and sends them as passages. The model
returns a statement and a supporting passage ID. Python validates that ID and
stores the original passage character boundaries, without asking the model to
copy quotes. Repeated lines have distinct IDs. A passage reference does not
prove the statement follows from it. `half_life_days` is mapped
deterministically from each claim's durability class (720/180/30/7 days).
Confidence describes the model's confidence in extraction, not factual accuracy
or author trust. Passage selection alone does not prove the paraphrase is entailed;
semantic faithfulness still needs human evaluation. Claims are not fact-checked.

Results are in `extractions/<item_id>.json`, with stable claim IDs, source/section
references, prompt/model versions and reported usage. Successful results are
cached. At most one retry is allowed for malformed JSON, invalid fields or a
invalid passage ID; failures preserve capture, preparation and triage.
Memory/search remain off. Usage records cover the final response; retries can
incur additional usage. No new API key or package is needed.

Process an existing item (triage is reused if cached):

```bash
python -m engine.extract .local-vault/telegram --item-id YOUR_ITEM_ID
```

`--force` repeats extraction even if cached. This is a **claim-only intermediate
record**, pending Kirill's shared Extraction schema. People/organization
extraction, embeddings, actual digest splitting and Victor's graph.apply are not
implemented in this block. The source text must remain available to resolve
claim references.

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
4. Add entities/embeddings, then integrate Victor's graph interface.

## License

Apache-2.0; see [LICENSE](LICENSE).


## Block 7 — automated extraction review

After extraction, a separate Backboard model compares every claim with the original
source. The configured judge is Anthropic `claude-opus-5-5`, selected from Backboard's
model catalog. The extraction model stays separately configurable. Set
`JUDGE_PROVIDER=backboard`, `BACKBOARD_JUDGE_LLM_PROVIDER=anthropic` and
`BACKBOARD_JUDGE_MODEL=claude-opus-5-5` in `.env`; the same `BACKBOARD_API_KEY`
is used. Set `JUDGE_PROVIDER=off` to disable the extra call.

The judge checks source support, cited passages, attribution, lost qualifications,
duplicate ideas, atomicity, clear label errors, and major omitted concepts. Python
requires exactly one review for every claim, validates issue codes and omission
passage IDs, and computes `accepted` only when every claim is supported and no
major omissions are reported. All other valid reviews are `needs_revision`.
Malformed or unavailable reviews remain pending; they never imply acceptance.
This is an AI assessment of source faithfulness, not independent factual verification.

Telegram automatically sends a fifth quality-review reply. Original captures and
extractions are preserved; review does not rewrite or delete claims. Detailed
reviews are saved separately under `reviews/<item_id>.json`, including requested
and actual models, prompt version, extraction hash and reported usage. Changed
extractions or judge settings invalidate the review cache. There is one retry for
invalid judge JSON, no blind retry for service errors. Judge requests allow up to
180 seconds. Reported usage covers the final response only; retries cost extra.
Memory and web search stay off. Source and claims are sent to Backboard for review.
No automatic correction or graph promotion is implemented in this block.

Review existing extractions without resending them through the extractor:

```bash
python -m engine.judge .local-vault/telegram --item-id ITEM_ID
```

Omit `--item-id` to review all existing extractions. Cached reviews are reused.
