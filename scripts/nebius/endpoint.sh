#!/bin/bash
# Create the endpoint once: the app on 8080 and the read-only MCP server on 8081, over the vault on the shared
# filesystem. Its URLs stay as long as the endpoint exists; deploy.sh updates the code without recreating it.
# Settings can only change by deleting and recreating it, which gives new URLs.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/nebius/config.sh
model_envs
"$NEBIUS" ai endpoint create --parent-id "$PROJECT" --name "$ENDPOINT_NAME" --image "$IMAGE:demo" \
    --platform cpu-d3 --preset 2vcpu-8gb --disk-size 20Gi --auth none \
    --container-port 8080 --container-port 8081 \
    --volume "$FILESYSTEM:/data:rw" --ssh-key "$(cat "${SSH_KEY:-$HOME/.ssh/id_ed25519.pub}")" \
    "${envs[@]}" --env "REFRESHER_APP_URL=$PUBLIC_HOSTS" \
    --env-secret "NEBIUS_API_KEY=$SECRET" --env-secret "REFRESHER_LOGIN=$SECRET" --env-secret "MCP_TOKEN=$SECRET" \
    --format jsonpath='{.status.public_endpoints}'
echo
