#!/bin/bash
# The hourly review as a Nebius Serverless Job: `refresher consolidate --if-due` over the endpoint's vault, on its own
# small machine, then gone. It exits at once unless a review is due and something new was placed; a run started by
# the app's Consolidate button at the same time waits for the vault lock and skips. Finished review jobs older than a
# day are deleted. Started hourly by .github/workflows/nebius-review.yml, or by hand.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/nebius/config.sh
model_envs
name="review-$(date -u +%Y%m%dt%H%M%S)"
"$NEBIUS" ai job create --parent-id "$PROJECT" --name "$name" --image "$IMAGE:demo" \
    --platform cpu-d3 --preset 2vcpu-8gb --disk-size 20Gi --timeout 1h --restart-policy never \
    --volume "$FILESYSTEM:/data:rw" --args "refresher consolidate --vault $VAULT --if-due" \
    "${envs[@]}" --env-secret "NEBIUS_API_KEY=$SECRET" --async --format jsonpath='{.resource_id}'
echo " $name started"
cutoff=$(date -u -v-1d +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u -d '1 day ago' +%Y-%m-%dT%H:%M:%S)
"$NEBIUS" ai job list --parent-id "$PROJECT" --format json | python3 -c '
import json, sys
cutoff = sys.argv[1]
for j in json.load(sys.stdin).get("items", []):
    m, s = j["metadata"], j.get("status", {})
    if m["name"].startswith("review-") and m.get("created_at", "") < cutoff and s.get("state") in ("COMPLETED", "FAILED", "CANCELLED"):
        print(m["id"])' "$cutoff" | while read -r old; do "$NEBIUS" ai job delete --id "$old" >/dev/null && echo "deleted $old"; done
