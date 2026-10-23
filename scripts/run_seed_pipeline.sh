#!/usr/bin/env bash
# Run ingestion's per-item stages over every captured item in a vault, then build the knowledge base.
#   scripts/run_seed_pipeline.sh [.local-vault/seed]
# Needs .env with NEBIUS_API_KEY, NEBIUS_MODEL and NEBIUS_JUDGE_MODEL (paid calls; results are cached, so reruns are free).
set -euo pipefail
VAULT=${1:-.local-vault/seed}

uv run python -m channels.importer eval/seed_corpus --vault "$VAULT"
uv run python -m engine.parse "$VAULT" >/dev/null
for raw in "$VAULT"/raw/*.json; do
  id=$(basename "$raw" .json)
  echo "== $id"
  for stage in triage extract quality judge; do
    uv run python -m engine.$stage "$VAULT" --item-id "$id" || echo "   $stage failed for $id"
  done
  # engine.quality runs coverage and citation repair itself when the review calls for them.
  uv run python -m engine.entities "$VAULT" --item-id "$id" || echo "   entities failed for $id"
  uv run python -m engine.package "$VAULT" --item-id "$id" || echo "   package failed for $id"
done
uv run refresher rebuild --vault "$VAULT"
