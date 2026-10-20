# Refresher

Personal knowledge engine for the Nebius x NVIDIA hackathon. Development is in
progress. The application captures UTF-8 files and Telegram text, imports a
labeled seed corpus, prepares source sections and links, extracts claims, and runs automated
quality review with bounded corrections. It also extracts and reviews named entity
mentions and saves a combined handoff record with a storage-only receipt.
Graph placement and refreshers are not implemented yet.

## Local setup

Use Python 3.11 or newer. Single-file capture uses the standard library; seed manifests use PyYAML.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Capture text and provenance

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

## Labeled seed imports

```bash
python -m channels.importer eval/seed_corpus --validate-only
python -m channels.importer eval/seed_corpus --vault .local-vault/seed
```

The manifest describes seven originals: four supplied samples and a synthetic
original/repost/correction sequence. Metadata enters the same capture function;
expected labels remain separate for future evaluation. Repeating the import
creates no additional records. See [the seed guide](eval/seed_corpus/README.md)
for fixture provenance, simulated channel IDs and current limitations.

## Telegram text capture

The bot uses aiogram long polling, so it can run on your laptop without a public
server. It accepts text in a private conversation with its configured owner.
Text is stored once in the top-level `text` field. Telegram-specific metadata
(sender, forwarding details, formatting and link previews) stays under
`channel_metadata`; it does not repeat the message text. Attachments, edits and channel subscriptions are future work.

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
  verdict. AI processing follows capture; graph relations are still pending.
- One polling process per token. Existing webhooks are detected, not deleted.
- Owner ID is required for capture; other users and group messages are not saved.
  No bot credentials are needed for automated tests.

See [aiogram polling docs](https://docs.aiogram.dev/en/latest/dispatcher/long_polling.html)
and [Telegram bot setup](https://core.telegram.org/bots/tutorial#obtain-your-bot-token).

## Prepare captured text and links

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

## AI triage through Nebius Token Factory

Model calls go through the small `JsonModel` interface (`engine/llm.py`). The default
provider is Nebius Token Factory with NVIDIA Nemotron models, called through its
OpenAI-compatible chat API in JSON mode. Backboard remains available for development.

Add these settings to your **real `.env`**, keeping your Telegram settings:

```dotenv
TRIAGE_PROVIDER=nebius
JUDGE_PROVIDER=nebius
NEBIUS_API_KEY=your_actual_key_here
NEBIUS_MODEL=fast Nemotron model ID (triage, extraction)
NEBIUS_JUDGE_MODEL=stronger Nemotron model ID (judge, quality, evidence, entities)
```

List the model IDs your key can call with `uv run python -m engine.llm models`.
`NEBIUS_BASE_URL` overrides the default `https://api.tokenfactory.nebius.com/v1`.
Thinking blocks and code fences around the JSON answer are stripped.

To use Backboard instead, set `TRIAGE_PROVIDER=backboard` (and `JUDGE_PROVIDER=backboard`)
with `BACKBOARD_API_KEY`, `BACKBOARD_LLM_PROVIDER` and `BACKBOARD_MODEL`; see `.env.example`.
`TRIAGE_PROVIDER=off` disables remote triage while keeping capture and preparation working.
Restart the bot after configuration changes.

Substantive conversations, brainstorming and Q&A should be kept too; triage judges
content rather than rejecting chat format. Each forwarded Telegram message is
currently processed independently. Paste the relevant exchange as one message
when context matters. Automatic conversation grouping and a manual Keep override
are not implemented yet. Triage caches depend on the rubric version.

After capture/preparation, enabled triage sends **only the note text** to
the model and asks for five fields: keep, kind, durability_class, split_digest
and a short reason. Each call is stateless (on Backboard: memory and web search off, a new conversation per
call, though Backboard can still retain conversation history). The source text is
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

## Claims and automated quality review

For a keep decision, the extractor produces at most 25 atomic claims. Each has a
statement, kind, durability, confidence and exact source references. Attribution,
uncertainty, dates, conditions and negations should be preserved. Durability maps
to half-life days: foundation 720, reference 180, news 30 and ephemeral 7.
Confidence concerns extraction faithfulness, not factual accuracy or author trust.

Python numbers nonblank source lines. New model outputs use
`evidence_passage_ids`, a list of 1–5 distinct supporting passage IDs. Python
validates every reference and stores exact character spans in source order.
Several passages may supply attribution or qualifications for one atomic idea;
they do not justify merging unrelated ideas. Legacy singular references remain
readable. Stored `evidence` is an object for one span or a list for several; use
`evidence_spans(claim)` for both. Single-span IDs remain stable; multi-span IDs
depend on all ordered spans. Source text remains necessary to resolve references.

### Configuration

The strong review model is separate from the extraction model. Add these settings
to the existing `.env`, using the same Nebius key:

```dotenv
JUDGE_PROVIDER=nebius
NEBIUS_JUDGE_MODEL=stronger Nemotron model ID
```

On Backboard, set `JUDGE_PROVIDER=backboard`, `BACKBOARD_JUDGE_LLM_PROVIDER` and `BACKBOARD_JUDGE_MODEL`.

`JUDGE_PROVIDER=off` disables review. Restart after code or configuration changes.
Judge requests allow up to 180 seconds. Source text and claims are sent to
the judge model in stateless requests.

### Workflow and limits

1. Judge every claim for source support, cited passages, attribution, lost
   qualifications, duplicates, atomicity, clear label errors and major omissions.
2. If claims have errors, make one strong-model correction and judge it again.
3. If all claims are supported but central ideas are missing, make one additive
   coverage pass: retain existing claims and IDs, append at most 25 new claims,
   then judge the full set. Expanded results have a 50-claim limit.
4. If the remaining claim errors concern citations only, repair those references
   once and judge again. Python prevents changes to wording, labels or confidence
   and preserves unflagged claims.

Python requires exactly one review per claim and validates omission passage IDs.
`accepted` requires every claim to be supported with no reported major omissions.
Other valid results remain `needs_revision`; malformed or unavailable results
remain pending. Telegram sends a final quality-review reply automatically.
There is no loop that keeps retrying until a model approves.

Each JSON-producing step can retry once for invalid output. Service errors stop
without a blind retry. Completed unchanged results are cached by source, parent
artifact/review hashes, prompt versions and model settings. A saved candidate can
resume at review after interruption. Reported usage covers the final response of
each step; retries incur additional usage. Judge decisions can vary; correction
and judging with the same model can share errors. This is a source-faithfulness
assessment, not independent factual verification.

### Local records

| Folder | Contents |
|---|---|
| `raw/` | Original text and provenance |
| `documents/` | Prepared sections and links |
| `triage/` | Keep/skip judgment and labels |
| `extractions/`, `reviews/` | Initial claims and their review |
| `repairs/`, `repair-reviews/` | Corrected claims and their review |
| `coverage/`, `coverage-reviews/` | Expanded claims and their review |
| `evidence/`, `evidence-reviews/` | Citation-only repairs and their review |
| `entities/`, `entity-reviews/` | Named entity mentions and their review |
| `processed/` | Combined source, claims, entities and review record |
| `placements/` | Storage-only handoff receipt; no graph placement |

These are working pipeline records under `.local-vault/telegram/`, not document
drafts. Later stages depend on earlier records for cache reuse and provenance.
Original captures remain unchanged. Formats are provisional until the team agrees
the shared RawItem/Extraction contracts.

### Process saved items

Run from the repository root, replacing `ITEM_ID`:

```bash
python -m engine.extract .local-vault/telegram --item-id ITEM_ID
python -m engine.quality .local-vault/telegram --item-id ITEM_ID
```

The first command extracts after triage; the second runs the full quality
workflow. Neither sends Telegram messages. Isolated commands are also available:

```bash
python -m engine.judge .local-vault/telegram --item-id ITEM_ID
python -m engine.evidence .local-vault/telegram --item-id ITEM_ID --candidate coverage
```

For citation repair, `--candidate` also accepts `extractions` or `repairs`. The
matching prior review must exist and refer to that exact candidate. Extraction
and judge commands support `--force`, which repeats calls even with a cache.

## People, organizations and concepts

Entity extraction identifies up to 40 explicit source mentions, typed as `person`,
`organization` or `concept`. Names must match the original spelling within each
cited passage. Python rejects invented expansions, partial-word matches, invalid
passage IDs and duplicate kind/name pairs, then records exact mention spans.
Entity IDs are local to an item. Within an article, names that clearly refer to
the same entity are grouped under the most complete source-written name, with
short forms in `aliases`. Each mention retains its exact `surface_name` and source
span. The canonical full-name ID stays stable when aliases are added. Short names
inside the full name are not counted as extra mentions. Ambiguous names remain
separate; cross-source identity, authorship, affiliations and relationships are
not inferred. The independent reviewer checks missed merges and unsafe merges.

Set `ENTITY_EXTRACTION=on` in `.env` to enable this stage. It uses the configured
strong judge model for extraction and a separate fresh-conversation review.
A configured `JUDGE_PROVIDER` (nebius or backboard) is required. Set
`ENTITY_EXTRACTION=off` to disable it. Restart the bot after changes.

The reviewer checks every entity's type and source context and looks for major
omissions. Python validates complete review coverage and source-grounded omission
names. Only a review with all entities supported and no major omissions is
`accepted`; other valid results are `needs_revision`. Failures remain pending.
Entity reviews do not change claim-review decisions or promote data into a graph.
Automatic entity correction is not implemented.

Telegram sends an additional entity-summary reply after claim quality processing.
This stage normally makes two model calls; each can retry once for invalid output. Extraction retries receive the failed
output and entry-specific feedback to repair source names/references.
Service errors stop without blind retries. Source/prompt/model-dependent caches
prevent repeat calls for unchanged completed results. A pending entity review can
resume from its saved extraction. Reported usage covers each step's final response.

Entity records and reviews are provisional additions to the team's future shared
Extraction contract. They are stored in `entities/` and `entity-reviews/`, separate
from immutable raw sources and claim artifacts. Process one saved item without
rerunning claim extraction or sending Telegram messages:

```bash
python -m engine.entities .local-vault/telegram --item-id ITEM_ID
```

## Combined extraction and storage handoff

After entity review, Telegram packages the selected claims, entities, sections,
links, source metadata and review findings into `processed/<item_id>.json`. The
original text and channel metadata remain in raw storage and are referenced by
path. Inputs carry content fingerprints for provenance and cache invalidation.
The record is explicitly provisional pending Kirill's shared Extraction schema;
chunks and embeddings are not yet implemented.

The packager makes no model or network calls. It validates exact evidence and
entity mentions, review completeness, source hashes and candidate lineage. It
selects the most advanced valid candidate (initial, correction, coverage, then
citation repair), rather than relying on file timestamps. A candidate's review
must match its exact content hash. Missing or stale reviews remain pending;
rejected judgments and omissions stay visible. An unchanged build reuses its
record and timestamp. Concurrent input changes abort publication.

`quality_status` is `accepted`, `needs_revision`, `pending` or `skipped`. The quality
gate passes only for a keep decision with accepted claim and entity reviews.
The storage-only adapter writes an idempotent receipt under `placements/`, with
`stored_only` for a passed gate or `pending_quality` otherwise. It always reports
`graph_applied=false` and assigns no node or edge IDs. It does not implement
Victor's graph placement logic or claim compatibility with an agreed graph API.

Package saved results and write the receipt locally:

```bash
python -m engine.package .local-vault/telegram --item-id ITEM_ID
```

Use `--claims-from extractions|repairs|coverage|evidence` to select a specific valid
candidate. The command needs no API key and does not rerun models or send Telegram
messages. With the full AI workflow enabled, Telegram adds a combined-handoff
reply automatically. Restart the bot after updating its code.

## Validation

```bash
python -m unittest discover -v
```

Tests cover capture integrity, source identity, Telegram behavior, parsing,
provider failures, claim validation, review completeness, bounded corrections,
coverage, multi-passage references, entity mentions, combined records and handoff
quality gates. They make no live
API calls.

## Remaining work

Agree shared schemas and adapt the provisional handoff; add embeddings and
multi-format parsing; integrate Victor’s graph interface and
Kirill’s retrieval/refresher work. Graph promotion, digest splitting, attachment
ingestion and external fact-checking remain unimplemented. Nebius runtime and NVIDIA model integration remain planned.

## License

Apache-2.0; see [LICENSE](LICENSE).
