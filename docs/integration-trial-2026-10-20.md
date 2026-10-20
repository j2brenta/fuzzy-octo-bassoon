# Integration trial: ingestion + knowledge management on the seed corpus (20 Oct 2026)

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
