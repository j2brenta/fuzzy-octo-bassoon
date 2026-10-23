# Integration trial: ingestion + knowledge management on the seed corpus (20 Oct 2026)

**Latest follow-up, 23 Oct:** triage labels/retention and copied-header metadata are verified; reviewer usage is measured. See the dated follow-ups below and the [token report](reviewer-token-usage-2026-10-23.md). The merge-time findings remain as historical context.

Branch `kirill/integration-trial` merges `olga/ingestion` into `kirill/knowledge-management` and runs the
nine seeds end to end on Nebius Token Factory. It is a trial, not a proposal to merge as is: everything that
touches `engine/` is a suggestion for Olga to accept, rework or drop.

The runs are committed under `.local-vault/` (seed samples only; see its README) so anyone can browse them:

```bash
uv run refresher-view            # pick a vault, ↑/↓ to an item, Enter on a stage
```

## What ran

| Vault | Extraction / triage | Review, coverage, entities | Accepted | Needs revision | Pending | Seed eval |
|---|---|---|---|---|---|---|
| `.local-vault/seed` | Nemotron 3 Super 120B | Nemotron 3 Ultra 550B | 2 | 5 (+1 skipped) | 1 | 17/33 |
| `.local-vault/seed-nano` | Nemotron 3 Nano 30B | Nemotron 3 Ultra 550B | 3 | 3 | 3 | 23/33 |

The Nano vault ran before the short-id fix below, so most of its "pending" items are id-copy failures, and
its higher eval score comes mostly from triage labels on the demo items. Do not read the two as a model
ranking; they are one run each.

## Findings

1. **Extraction quality depends mostly on the extraction model.** On the LinkedIn essay Nano produced 8
   claims for 24 passages and the reviewer listed five real, central omissions (we checked them by hand and
   agreed). Super produced 16 claims and the first review accepted them with no omissions.
2. **The quality gate never passes long or dense items.** An item passes only when every claim is supported
   **and** `omitted_concepts` is empty, and the same for entities. The reviewer is a fresh model call each
   time, so after coverage adds claims the next review finds a different set of "missing" ideas
   (people list: 14 → 17 claims, all supported, then 6 new omissions). Coverage runs once, so these items
   stay `needs_revision` forever.
3. **Many omissions are noise.** Examples: the Telegram "Author and timestamp: Kirill R…" header, an
   article's rhetorical opening question, a linking sentence ("can be split into two categories") whose two
   categories already have claims, and concept entities such as *UTC*, *human*, *pipeline*, *cheat code*.
   Others are real (the essay's construction framework, "inspiration = time × cognitive efficiency").
4. **Concept entities are too broad.** The essay's 18 entities include *AI*, *mathematics*, *outline*,
   *novel*; the entity review then always finds more concepts "omitted" (8 on the essay's latest run, with
   none of the 18 flagged as wrong). With concepts in the gate, almost no article passes.
5. **Reviewers could not copy 64-character ids.** Entity review failed validation twice on the essay: one
   attempt repeated 9 entities and skipped 7, the other dropped one hex character from an id. The people
   list's claim review failed the same way. Fixed on this branch with short ids (below).
6. **Validation failures were opaque.** Six two-attempt loops replaced the reason with "failed validation
   twice" and kept no copy of the rejected answer. Fixed on this branch (below).
7. **Other validation failures seen:** "Entity aliases must not repeat the canonical name or another alias"
   (introspection list, ELI5), "Entity name was not found as an exact mention" (essay, Nano run), and
   "Nebius answer was cut off at max_tokens" (introspection entities: Ultra's reasoning plus ~40 entities
   exceeds 16,384 tokens).
8. **Old artifacts stay next to new ones.** After a model change, coverage/repair/evidence files from the
   old extraction stay in the vault. The packager correctly ignores them (`ignored_candidates`,
   `stale_parent`), but they read like current results. The viewer now marks them stale.
9. **Triage labels drift from the manifest.** Kind: post vs article/thought/announcement; durability: the
   demo correction and repost labelled `reference` (expected `ephemeral`), the essay `foundation`. Triage
   also skipped `demo-original` as "no lasting value", which removes the target the correction should
   supersede.
10. **Telegram copy headers are kept as text, and their time is lost.** Pasted Telegram Desktop messages
    start with "Kirill R, [7 Oct 2026 at 12:13:38]:". Capture keeps that line in `text` and, with no
    `published_at` in the manifest, the items get today's capture time, so the forwards sort as 20 Oct
    instead of 7 Oct. `channels/seed_raw.py` already parses this header into `forwarded_by` and a time.
11. **Relation checks cannot pass yet.** DUPLICATES and SUPERSEDES are only computed between items in the
    graph, and the graph only takes items whose quality gate passed. With most items held back, the eval's
    relation checks fail regardless of the relation logic. With `refresher rebuild --include-unreviewed`
    the introspection list is detected as a repost of the people list (check passes); the demo repost
    has no original to match because triage skipped `demo-original`, and SUPERSEDES needs Victor's check.

## Changes on this branch that touch `engine/` (for Olga to review)

- `engine/llm.py`: `NebiusModel` for Token Factory (OpenAI-compatible chat, JSON mode). `TRIAGE_PROVIDER` /
  `JUDGE_PROVIDER=nebius` with `NEBIUS_MODEL` and `NEBIUS_JUDGE_MODEL`; Backboard still works. Empty or
  cut-off answers raise the retryable `ModelOutputError`; `max_tokens` is 16,384.
  `python -m engine.llm models` lists available models.
- `engine/short_ids.py`: claim review, citation repair and entity review send `C1…`/`E1…` instead of
  64-character ids and map replies back before validation. Stored records, checks, prompts and cache keys are
  unchanged.
- `engine/artifacts.save_rejected`: every two-attempt loop reports both reasons and saves each rejected
  answer to `<vault>/rejected/<stage>/<item_id>.<attempt>.json` (never read by the pipeline).
- Error messages no longer name Backboard; README and `.env.example` describe Nebius.
- `tests/test_seed.py` counts nine seeds (two added: `tg_eli5`, LinkedIn) and no longer compares with
  `sample_raw/`, which PR #6 moved to `eval/seed_corpus/originals/`.

## Next steps for Olga to think about

1. **Gate on claim correctness; treat omissions as coverage notes.** Keep `unsupported`, `wrong_evidence`,
   attribution and duplicate verdicts as hard failures. Record `omitted_concepts` on the item (and in the
   Obsidian note) instead of failing it, or fail only above a small threshold of central ideas.
2. **Tighten what counts as an omission** in `judge.py`'s prompt: no titles, framing questions, linking
   sentences, headers, author/time metadata.
3. **Narrow entities or take concepts out of the gate.** Either extract people, organizations and named
   works/products only, or keep concepts but never fail an item for a missing concept.
4. **Repair instead of reject for mechanical entity errors**: drop an alias equal to the canonical name or to
   another alias rather than failing the whole extraction.
5. **Make `max_tokens` configurable** (`NEBIUS_MAX_TOKENS`) or split long entity lists, for Ultra on long
   lists.
6. **Clean or version stage folders when the extraction changes**, so stale coverage/repair/evidence files do
   not linger.
7. **Triage on the seeds**: decide whether announcements of past/future events are `ephemeral` or
   `reference`, and whether triage may skip an item another seed corrects. Update the manifest labels or the
   triage rubric so they agree.
8. **Default models**: Super for triage and extraction, Ultra for review looks like the right split; Nano is
   too thin on articles.
9. **Contract**: `forwarded_by` is still a string in raw records; the shared `RawItem` has an `AuthorHint`.
   `refresher/schemas.py` accepts both for now.

## Next steps on Kirill's side

- Freeze reviewed `processed/` output as graph-level fixtures once the gate settles, and replace the
  hand-written `tests/fixtures/vault`.
- Done: `refresher rebuild|km|eval --include-unreviewed` places `needs_revision`/`pending` items with an
  "Unreviewed" banner and `review_status` in their notes, so relations and decay can be shown before the
  gate changes. Triage skips stay out. The committed `wiki/` folders were built with it.
- Record the decisions above in the v2 plan doc.

## Olga's response: PR #8 (merged into this branch on 23 Oct)

PR #8 (`olga/ingestion`, rebased) takes over the engine changes above and answers most of the findings:

| Finding / next step | PR #8 |
|---|---|
| 1–3 Omissions fail the gate; noisy omissions | `engine/review_policy.py` (`correctness-v1`): claims pass on verdicts alone; omissions go to `coverage_notes`. The judge prompt (`judge-v4`) excludes titles, headers, author/time metadata, framing questions and linking sentences |
| 4 Concept entities block | Concepts are advisory (`concept_review_notes`); wrong people/organizations still block |
| Alias repeats reject the extraction | Duplicate aliases are dropped |
| 5 `max_tokens` | `NEBIUS_MAX_TOKENS` / `NEBIUS_JUDGE_MAX_TOKENS` (default 32,768); Super/Ultra are the defaults |
| 6 Stale stage files | A changed extraction moves its descendants to `<vault>/stale/<item_id>/` |
| 9 `forwarded_by` contract | The handoff (`handoff-v2`) emits the `AuthorHint` shape |
| Old reviews | Reassessed under the new policy without new model calls |
| Not yet | 7 (triage labels against the manifest), 10 (Telegram copy-header time) |

Also new: web article capture (`channels/web.py`, `engine/parse/web.py`, trafilatura); the Backboard provider is removed.

Repackaging the committed Super vault under `handoff-v2` with no new model calls gives 4 accepted, 4 pending
and 1 skipped (was 2 / 5 / 1 / 1), and 20/37 eval checks (21/37 with `--include-unreviewed`; the manifest now
has 37 checks). The four pending items are the reviews that failed before short ids; the prompt versions
changed (`judge-v4`, `repair-v3`), so a full rerun calls the models again for every item.

### To raise with Olga on PR #8

1. **Empty PR description.** It should state the policy change (`correctness-v1`): "accepted" now means every
   claim is supported and no person/organization entity is wrong; omissions and concepts are advisory.
2. **Findings still open:** 7, triage labels disagree with the manifest (kind, durability, `demo-original`
   skipped); 10, the Telegram copy header stays in `text` and its date is lost, so forwards sort by capture time.
3. **Reviewer token cost.** Each claim now carries its cited passages in full (`evidence_passages`) on top of
   the full passage list, so long items send most passages twice. Probably worth it for accuracy; worth
   measuring on the essay and the people list.
4. Nothing blocks the merge; it is merged into `kirill/integration-trial` with all 291 tests passing.

## Triage follow-up — fresh nine-seed run on 23 October

A fresh Nebius Super run with the existing `triage-v4` prompt matched **18/18 manifest kind/durability checks**, with **9/9 items kept**, including `demo-original` (`announcement / ephemeral`). The historical Super/Nano vaults and originals were preserved. A cache-only replay reused all nine results without API calls. Usage: 11,335 input + 3,309 output = 14,644 tokens.

The triage part of the open finding above is therefore verified on the refreshed corpus. The earlier `triage-v2` sample artifacts remain unchanged; repackaging them alone cannot show the new behavior. Telegram copied-header dates and reviewer token overhead remain open. This is a triage-only result, not a new 37-check graph/full-pipeline score.

Per-item results, provenance and reproduction details: [23 October triage evaluation](triage-v4-seed-evaluation-2026-10-23.md).

## Header metadata and reviewer usage follow-up — 23 October

- **Copied Telegram headers:** Olga’s importer, seed path and Telegram pastes now reuse Kirill’s parser while preserving the exact original text. Copied chat sender/time metadata is separate from original publication and actual capture time. Known publication time wins; otherwise timezone-qualified copied time precedes capture time in timeline ordering. An explicit import/config/corpus timezone is required for conversion; unknown timezone remains unknown. Direct paste senders and image uploaders are not promoted to original authors. Metadata-only header refresh preserves valid paid text-stage caches and rebuilds the handoff.
- **Reviewer usage measured:** people-list: **5,155 input / 5,856 output / 11,011 total tokens**, 25 claims, accepted; LinkedIn essay: **5,168 input / 4,005 output / 9,173 total**, 16 claims, needs revision for one citation-support issue. Both are fresh `judge-v4` Ultra calls with one format attempt. These are review measurements, not a refreshed graph score.
- **Evidence overhead:** attaching cited passages increases user JSON size by **81.0%** on the people list and **52.9%** on the essay. These are character comparisons, not token-cost multipliers. Keep the evidence for now; no accuracy/cost ablation was performed.

Details and saved local evidence: [reviewer usage report](reviewer-token-usage-2026-10-23.md). The preceding historical review comments remain as a record of what was observed before these follow-ups.

## Picture input follow-up — 23 October

Telegram photos/image documents now use Token Factory’s NVIDIA VLM and the existing reviewed text pipeline; voice handling was removed at Olga’s request. The synthetic full-pipeline check was accepted. Two explicitly approved Desktop test images completed image reading/extraction: the room photo was accepted (8 claims); the math screenshot remained needs revision (23 claims), with incorrect/unsupported claims retained as flagged. Originals were unchanged. Automated checks: 311 tests and 104 subtests pass. [Picture validation and limits](picture-support-validation-2026-10-23.md).

## PDF input follow-up — 23 October

PDF Telegram uploads, public links (including extensionless download URLs) and local imports now use page-preserving native extraction with cached VLM fallback for scans. Original files/receipts remain immutable; handoffs and readable claims retain PDF page references. Limits: 20 MB / 20 pages / 50,000 characters; over-limit/unreadable files are retained with explicit errors, never truncated. Tests: 327 passed plus 104 subtests. Native and scanned synthetic PDF handoffs passed live Nebius review; a public W3C PDF URL and its cache passed the real downloader check. [PDF validation and limitations](pdf-support-validation-2026-10-23.md). Existing graph-placement and temporal-relation limitations are unchanged.
