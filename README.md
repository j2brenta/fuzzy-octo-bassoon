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

The manifest describes nine originals: six supplied samples and a synthetic
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
- New `forwarded_by` values use the shared person-hint shape (name/handle/URL);
  legacy strings are normalized in the combined handoff without rewriting raw.
  Forwarders are not substituted for original authors; hidden author names are
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
counts. This preparation stage only identifies links; the article stage below
fetches supported public HTML pages. PDF and YouTube parsers remain pending.

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

## Read public web articles

With `WEB_FETCH=on` (the default), Telegram downloads up to three distinct links
from a saved message, then runs the existing triage, extraction and review
pipeline on each readable HTML article. A bare URL is treated as a pointer;
claims come from the fetched article text. A comment and its linked articles
remain separate inputs. Article links are never recursively crawled.

[Trafilatura](https://trafilatura.readthedocs.io/en/latest/usage-python.html)
extracts the main text, title, byline and site name. Publication dates come only
from explicit page metadata. A date without a timezone stays in metadata rather
than becoming an invented midnight timestamp. The article author is independent
of the person who shared the URL.

- `raw/`: original message plus a separate article-text child, with source URL
  and a parent reference. Article claims cite the child's exact text spans.
- `web-snapshots/`: immutable HTML bytes addressed by SHA-256, checked before
  processing a derived article. These are local captures and stay ignored by Git.
- `web-links/`: per-link captured/failed/unsupported/deferred status, used to
  reuse successful downloads and retry failures without losing originals.

Downloads accept public HTTP(S) pages, check every redirect, verify TLS against
the original hostname and cap HTML at 2 MB. PDF/media, restricted or JavaScript-only
pages return explicit status and preserve the original link. Sites returning
HTTP 403 may require a browser challenge; open the page normally and paste its
text into Telegram. The fetcher does not bypass those challenges. Extracted text over
60,000 characters is saved but needs future section processing before AI review.

Import a public article without running a Telegram bot:

```bash
python -m channels.web 'https://example.org/article' --vault .local-vault/web
```

This CLI saves/parses the child and prints its raw path; it does not call an LLM.
Use the existing extraction/review commands for that child. A repeated import
reuses the download. `--refresh` requests the current page, retaining older HTML
and child versions when content changes. Set `WEB_FETCH=off` to disable automatic
article downloads in Telegram, and restart the bot after configuration changes.

## AI triage and providers

Nebius Token Factory is the default provider. The trial configuration uses NVIDIA
Nemotron 3 Super for triage/extraction and Ultra for independent review, entity
extraction and repair. Model IDs match Kirill's integration trial and can be
changed for the models available to your Token Factory account.

Keep credentials in the ignored project-root `.env`:

```dotenv
TRIAGE_PROVIDER=nebius
JUDGE_PROVIDER=nebius
NEBIUS_API_KEY=your_token_factory_key
NEBIUS_BASE_URL=https://api.tokenfactory.nebius.com/v1
NEBIUS_MODEL=nvidia/nemotron-3-super-120b-a12b
NEBIUS_JUDGE_MODEL=nvidia/Nemotron-3-Ultra-550b-a55b
NEBIUS_MAX_TOKENS=32768
NEBIUS_JUDGE_MAX_TOKENS=32768
ENTITY_EXTRACTION=on
```

Both models use the same Token Factory key. `NEBIUS_JUDGE_MAX_TOKENS` overrides
the general budget for review/repair/entity calls. Model context/output limits
still apply; a truncated answer remains an explicit retryable error. Run
`python -m engine.llm models` to list models using your configured key.

Set a provider to `off` to disable that stage. Restart the bot after changes.
No key belongs in Git.

Triage returns keep/skip, kind, durability, digest-splitting flag and a reason.
Event logistics and corrections are kept as announcement/ephemeral, including
original announcements needed by later reposts or corrections. Curated lists are
post/reference; technical talk abstracts can remain reusable references despite
clock times. Long-form opinion/explanatory essays are article/reference; current
tool-release commentary is post/news. Pasted sender/time headers are metadata,
not substantive ideas. Expected seed labels are never supplied to the model.

Substantive chats are kept too. Each forwarded message is processed independently;
paste an exchange as one message when context matters. Automatic conversation
grouping and a manual Keep override are not implemented. A skip preserves raw.

Results are cached under `triage/` by source, rubric, provider and model. Inputs
over 60,000 characters wait for section processing rather than being truncated.
Invalid output gets one retry; service/auth errors stop. Failed JSON/validation
attempts are saved under `rejected/<stage>/` with both reasons. These diagnostic
records are ignored by the pipeline and can contain private source text.

```bash
python -m engine.triage .local-vault/telegram --item-id ITEM_ID
```

Omit the ID for all captures; `--force` repeats calls even if cached. Digest
splitting is flagged but not performed. Triage labels are model judgments.

## Claims and automated quality review

For a keep decision, the extractor produces at most 25 atomic claims. Each has a
statement, kind, durability, confidence and exact source references. Attribution,
uncertainty, dates, conditions and negations should be preserved. Durability maps
to half-life days: foundation 720, reference 180, news 30 and ephemeral 7.
Confidence concerns extraction faithfulness, not factual accuracy or author trust.
Each statement must stand alone. A corrected event value and its replacement
context form one update; an obsolete value must not be presented as current.

Python numbers nonblank source lines. New model outputs use
`evidence_passage_ids`, a list of 1–10 distinct supporting passage IDs. Python
validates every reference and stores exact character spans in source order.
Several passages may supply attribution or qualifications for one atomic idea;
they do not justify merging unrelated ideas. Legacy singular references remain
readable. Stored `evidence` is an object for one span or a list for several; use
`evidence_spans(claim)` for both. Single-span IDs remain stable; multi-span IDs
depend on all ordered spans. Source text remains necessary to resolve references.

### Configuration

The judge uses `JUDGE_PROVIDER` and the model settings above through the small
`JsonModel` boundary. Review requests allow up to 180 seconds; triage/extraction
requests allow up to 120 seconds. Nebius calls send stateless chat requests
without tools. Source text and claims go to Nebius Token Factory.

### Workflow and limits

1. Judge every claim for source support, cited passages, attribution, lost
   qualifications, duplicates, atomicity, clear label errors and major omissions.
2. For citation-only findings, repair evidence directly without rewriting claim wording. For semantic errors, make one strong-model correction and judge it again.
3. Optionally use `--expand-coverage` to make one additive
   coverage pass for omitted ideas: retain existing claims and IDs, append at most 25 new claims,
   then judge the full set. Expanded results have a 50-claim limit.
4. If the remaining claim errors concern citations only, repair those references
   and judge again, with at most two citation rounds for a candidate. A second
   round is allowed only if the new findings are still citation-only. Python
   prevents changes to wording, labels or confidence and preserves unflagged claims.

Python requires exactly one review per claim and validates omission passage IDs.
`accepted` requires every claim to be supported. Omissions are advisory coverage
notes, not blocking failures. Other correctness errors remain `needs_revision`; malformed or unavailable results
remain pending. Telegram sends a final quality-review reply automatically.
There is no loop that keeps retrying until a model approves.

Model-facing review/citation IDs are short (`C1`, `E1`); stored IDs remain the
full stable identifiers. Each JSON-producing step can retry once for invalid output. Service errors stop
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
drafts. Replacing a stage archives its dependent live records under `stale/`,
so old coverage, repairs and handoffs do not appear current. Raw sources stay
untouched. Archives are recoverable and never selected by the live pipeline.
Unchanged records preserve their downstream caches.
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

## People and organizations

New extraction requests up to 40 explicitly named people and organizations.
Products, works, headings and abstract concepts are excluded from new requests;
legacy `concept` entities remain readable and their findings stay advisory.
The model returns names, types and aliases; Python locates every exact name
occurrence and records its passage and character spans. The model no longer
copies passage IDs for new entity extractions. Names must match the original
spelling; invented expansions and partial-word matches fail validation. Legacy
replies with passage IDs remain readable, with only verifiable mechanical
reference repairs allowed and recorded. Semantic errors still require review.
Entity IDs are local to an item. Within an article, names that clearly refer to
the same entity are grouped under the most complete source-written name, with
short forms in `aliases`. Each mention retains its exact `surface_name` and source
span. The canonical full-name ID stays stable when aliases are added. Short names
inside the full name are not counted as extra mentions. Ambiguous names remain
separate; cross-source identity, authorship, affiliations and relationships are
not inferred. The independent reviewer checks missed merges and unsafe merges.

Set `ENTITY_EXTRACTION=on` in `.env` to enable this stage. It uses the configured
strong judge model for extraction and a separate fresh-conversation review.
A configured Nebius judge and its key are required. Set
`ENTITY_EXTRACTION=off` to disable it. Restart the bot after changes.

The reviewer checks every entity's type and source context and looks for major
omissions. Python validates complete review coverage and source-grounded omission
names. Wrong people/organizations and unsafe named-entity merges block acceptance.
Legacy concept findings and all entity omissions remain advisory and visible, without
blocking the item. Generic concepts, headers and framing text are excluded from
the omission rubric. Duplicate alias labels are normalized rather than rejecting
the entire extraction; invalid source evidence still fails. Failures remain pending.
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

## Read claims and entities without opening pipeline folders

Open `.local-vault/telegram/processed/index.md`. It lists saved items by readable
title and status. Each item has one current Markdown note next to its JSON
handoff: claims, source passage numbers, people, organizations, aliases, mention
counts and review findings. Unfinished entity extraction is labeled explicitly;
it is not shown as “no people found”. Notes are refreshed after Telegram processing,
including pending model stages. Text below the user-notes marker is preserved.

Generate or refresh these notes for existing captures without any model calls:

```bash
python -m engine.results .local-vault/telegram
```

`raw/` contains originals; `processed/` contains current results. The other stage
folders are internal caches and diagnostics used for bounded correction and
retries. They are retained so a failed stage can resume without losing evidence
or paying to repeat completed work. There is no need to read them to understand
an extraction; their folder names do not represent independent copies of an article.

The claim citation limit is ten **source lines**, because a single supporting
code block can occupy more than five lines. This does not relax atomicity or
source-support review. Validation distinguishes an over-limit citation list from
invalid ID types or references to nonexistent passages. IDs and character spans
are never silently dropped or replaced to make output pass.

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
gate passes for a keep decision with accepted correctness reviews. Omissions are
retained in `coverage_notes`; concept-only warnings remain in
`concept_review_notes`. Missing reviews still prevent acceptance. Older valid
reviews can be reassessed under this policy without another model call; their
original stored judgments remain unchanged.
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
ingestion and external fact-checking remain unimplemented. Fresh Nebius seed runs
should be repeated after configuring the Token Factory key; one trial per model
is not a model ranking.

## License

Apache-2.0; see [LICENSE](LICENSE).
