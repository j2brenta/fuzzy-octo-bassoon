# .local-vault: seed sample runs only

The tracked `seed/` and `seed-nano/` vaults hold output for the **nine seed samples** in `eval/seed_corpus/`.
They are committed so the team can browse historical runs; they are not the live knowledge base. Other
local captures and test vaults—including `.local-vault/telegram/` and private picture tests—stay ignored
by `.gitignore` and must never be committed.

| Path | What |
|---|---|
| `seed/` | Triage and extraction on Nemotron 3 Super, review on Nemotron 3 Ultra (with short ids) |
| `seed-triage-v4-2026-10-23/` | Local triage-only refresh: 18/18 label checks, 9/9 kept; artifacts ignored by Git. Shareable summary: `docs/triage-v4-seed-evaluation-2026-10-23.md` |
| `seed-nano/` | Triage and extraction on Nemotron 3 Nano, review on Ultra (before the short-id fix) |
| `seed*-run*.log` | Console output of `scripts/run_seed_pipeline.sh` runs |
| `seed*-eval.md` | `refresher eval` against `eval/seed_corpus/manifest.yaml` |

Findings and next steps: `docs/integration-trial-2026-10-20.md`.

## Things to try

```bash
uv run refresher-view                              # choose a vault, browse items and stages
uv run refresher km --vault .local-vault/seed --at 2027-01-15   # decay at a later date
uv run refresher eval --vault .local-vault/seed --manifest eval/seed_corpus/manifest.yaml
```

Open `seed/` as an Obsidian vault and look at `wiki/` for the generated notes. The committed `wiki/` was
built with `refresher rebuild --include-unreviewed`, so it also shows items that did not pass ingestion's
quality gate; those notes carry an "Unreviewed" banner and `review_status` in their frontmatter.

To reproduce (paid Nebius calls; needs `.env`), use a new folder so these stay as they are:

```bash
scripts/run_seed_pipeline.sh .local-vault/seed-try 2>&1 | tee .local-vault/seed-try-run.log
NEBIUS_MODEL=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B scripts/run_seed_pipeline.sh .local-vault/seed-try-nano
```

New vaults are ignored unless you add them to the `.gitignore` exceptions.

## Local picture and reviewer checks — 23 October

`seed-reviewer-usage-2026-10-23/` contains the two-sample reviewer measurement;
`picture-smoke-2026-10-23/` contains the synthetic picture check;
`picture-tests-2026-10-23/` contains **private, explicitly approved image tests**.
These are local test artifacts, not additional shared seed runs. Never commit the
private images, OCR or replies. Share only the summaries in repository `docs/`.
