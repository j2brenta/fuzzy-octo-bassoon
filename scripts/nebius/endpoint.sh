#!/bin/bash
# Create the endpoint once: the app on 8080 and the read-only MCP server on 8081, over the vault on the shared
# filesystem. Its URLs stay as long as the endpoint exists; deploy.sh updates the code without recreating it.
# Settings can only change by deleting and recreating it, which gives new URLs.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/nebius/config.sh
envs=()
while IFS= read -r pair; do envs+=(--env "$pair"); done < <(uv run --no-sync python - "$ENV_FILE" <<'PY'
import sys
from dotenv import dotenv_values
e = dotenv_values(sys.argv[1])
for k in ("TRIAGE_PROVIDER", "JUDGE_PROVIDER", "NEBIUS_BASE_URL", "NEBIUS_MODEL", "NEBIUS_JUDGE_MODEL", "NEBIUS_MAX_TOKENS",
          "NEBIUS_JUDGE_MAX_TOKENS", "NEBIUS_EMBED_MODEL", "NEBIUS_TRANSLATE_MODEL", "NEBIUS_VISION_MODEL",
          "ENTITY_EXTRACTION", "GRAPH_RELATOR", "WEB_FETCH", "REFRESHER_LANGUAGE", "LOG_LEVEL"):
    if e.get(k):
        print(f"{k}={e[k]}")
PY
)
"$NEBIUS" ai endpoint create --parent-id "$PROJECT" --name "$ENDPOINT_NAME" --image "$IMAGE:demo" \
    --platform cpu-d3 --preset 2vcpu-8gb --disk-size 20Gi --auth none \
    --container-port 8080 --container-port 8081 \
    --volume "$FILESYSTEM:/data:rw" --ssh-key "$(cat "${SSH_KEY:-$HOME/.ssh/id_ed25519.pub}")" \
    "${envs[@]}" --env "REFRESHER_VAULT=$VAULT" --env "REFRESHER_APP_URL=$PUBLIC_HOSTS" \
    --env REFRESHER_RUN_TOKENS=2000000 --env REFRESHER_RUN_SECONDS=1800 \
    --env-secret "NEBIUS_API_KEY=$SECRET" --env-secret "REFRESHER_LOGIN=$SECRET" --env-secret "MCP_TOKEN=$SECRET" \
    --format jsonpath='{.status.public_endpoints}'
echo
