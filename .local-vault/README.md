# .local-vault: seed sample runs only

These vaults hold pipeline output for the **nine seed samples** in `eval/seed_corpus/` and nothing else.
They are committed so the team can browse one shared run; they are not the live knowledge base. Real
captures (for example `.local-vault/telegram/`) stay ignored by `.gitignore` and must never be committed.

| Path | What |
|---|---|
| `seed/` | Triage and extraction on Nemotron 3 Super, review on Nemotron 3 Ultra (with short ids) |
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

Open `seed/` as an Obsidian vault and look at `wiki/` for the generated notes.

To reproduce (paid Nebius calls; needs `.env`), use a new folder so these stay as they are:

```bash
scripts/run_seed_pipeline.sh .local-vault/seed-try 2>&1 | tee .local-vault/seed-try-run.log
NEBIUS_MODEL=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B scripts/run_seed_pipeline.sh .local-vault/seed-try-nano
```

New vaults are ignored unless you add them to the `.gitignore` exceptions.
