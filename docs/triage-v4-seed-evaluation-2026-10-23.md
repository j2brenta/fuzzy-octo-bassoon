# Seed triage evaluation — 23 October 2026

**Result: 9/9 seeds matched both manifest labels and were kept.** `demo-original` was kept as `announcement / ephemeral`.

## Scope

Fresh Nebius triage calls using the unchanged `triage-v4` prompt and all nine originals from `eval/seed_corpus/manifest.yaml`. Expected labels were used only for scoring after the responses; they were not sent to the model. Neither the prompt nor the manifest was edited for this run.

This is a **triage-only evaluation**: 18 manifest checks (kind and durability) plus nine retention checks. Retention is checked separately because the manifest does not specify a `keep` field. It is not a new claim/entity-quality run, graph relation evaluation, or replacement for the integration trial’s 37-check score. Six supplied items still have draft labels in the manifest.

## Results

| Seed | Expected kind / durability | Actual kind / durability | Keep | Result |
|---|---|---|---|---|
| demo-correction | announcement / ephemeral | announcement / ephemeral | Yes | Pass |
| demo-repost | announcement / ephemeral | announcement / ephemeral | Yes | Pass |
| demo-original | announcement / ephemeral | announcement / ephemeral | Yes | Pass |
| people-list | post / reference | post / reference | Yes | Pass |
| introspection-list | post / reference | post / reference | Yes | Pass |
| redis-talks | post / reference | post / reference | Yes | Pass |
| economics-list | post / reference | post / reference | Yes | Pass |
| eli5-post | post / news | post / news | Yes | Pass |
| linkedin-math | article / reference | article / reference | Yes | Pass |

## Historical comparison

| Triage artifact set | Kind/durability checks | Items kept | demo-original |
|---|---:|---:|---|
| Shared Super vault, `triage-v2` | 8/18 | 8/9 | Skipped |
| Fresh Super run, `triage-v4` | 18/18 | 9/9 | Kept |

Both historical vaults (`seed/`, `seed-nano/`), source originals and the manifest were checked for unchanged SHA-256 hashes after the run. They were not overwritten. This is one run of each prompt, not a statistical reliability estimate or a model ranking.

## Runtime and usage

- Model: `nvidia/nemotron-3-super-120b-a12b`; provider: `nebius`.
- Started: `2026-10-23T16:02:51.553291+00:00`; finished: `2026-10-23T16:03:19.199653+00:00` (UTC).
- Input tokens: **11,335**; output tokens: **3,309**; total: **14,644**.
- Model calls: 9 fresh items; format attempts: 9.
- Exact-cache rerun: all nine results reused; a client that raises on any API call confirmed no additional requests.
- Usage is reported by the final response for each item; no currency cost is estimated.

| Seed | Input tokens | Output tokens | Call seconds |
|---|---:|---:|---:|
| demo-correction | 701 | 290 | 1.823 |
| demo-repost | 687 | 232 | 1.584 |
| demo-original | 687 | 237 | 2.236 |
| people-list | 1566 | 302 | 4.008 |
| introspection-list | 1535 | 520 | 4.125 |
| redis-talks | 879 | 336 | 2.628 |
| economics-list | 1857 | 363 | 3.480 |
| eli5-post | 1096 | 654 | 5.242 |
| linkedin-math | 2327 | 375 | 2.459 |

## Artifacts and reproducibility

- Local run vault: `.local-vault/seed-triage-v4-2026-10-23/`.
- `raw/` retains source captures; `documents/` retains parser output; `triage/` retains each reply, model identity, request ID and usage.
- `triage-evaluation.json` retains per-item source hashes, expectations, actual labels, request IDs and check results. These local run artifacts are ignored by Git; this report is the shareable summary.
- Checkout commit: `165ff60e5f4ed09b104ef99f5462b636cf181cf8`. Uncommitted voice changes were present; the triage prompt/module were not changed for this evaluation.
- Manifest SHA-256: `384c34757d3354180548129c5eca153fccf0d787368d0d5e1f7105a0e90125ae`.
- System prompt SHA-256: `05de38574f4fc8a5b3c73e73da73f9ed49b99b060d696e12a02ad2681fde5323`.
- `engine/triage.py` SHA-256: `93246d1a9188993212002ddfa7e9812d3502c3008da54832707a152157974a26`.

To reuse these cached results without paid calls:

```bash
python -m engine.triage .local-vault/seed-triage-v4-2026-10-23
```

For a fresh paid run, import the manifest into a new vault using `python -m channels.importer eval/seed_corpus/manifest.yaml --vault NEW_VAULT`, then run `python -m engine.triage NEW_VAULT` with the same Nebius model and prompt. Do not overwrite the historical comparison vaults.

## Consequence for the integration review

The triage-label and `demo-original` retention concern is resolved for this fresh nine-seed run. Earlier committed `triage-v2` artifacts remain historical evidence. Pasted Telegram header/date handling, reviewer evidence token overhead, claim/entity quality and graph relations are separate follow-ups; this run does not resolve them.
