#!/bin/bash
# Ship the current checkout: push :demo, then restart the endpoint so it pulls it. The URLs stay the same.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/nebius/config.sh
scripts/nebius/push.sh
id=$("$NEBIUS" ai endpoint get-by-name --parent-id "$PROJECT" --name "$ENDPOINT_NAME" --format jsonpath='{.metadata.id}')
"$NEBIUS" ai endpoint restart --id "$id" >/dev/null
"$NEBIUS" ai endpoint logs "$id" 2>&1 | grep "refresher-entrypoint: build" | tail -1
