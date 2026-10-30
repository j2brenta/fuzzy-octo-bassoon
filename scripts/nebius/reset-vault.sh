#!/bin/bash
# Empty the demo vault: stop the endpoint (no writes while deleting, and its instance is free for the job), delete
# everything in the vault folder with a one-off job, start the endpoint again with nothing in memory. The URLs stay.
# The job runs as root, past the image's entrypoint, as files may belong to either user. Asks before deleting;
# pass --yes to skip the question.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/nebius/config.sh
if [ "${1:-}" != --yes ]; then
    read -r -p "Delete everything in $VAULT on $FILESYSTEM? Type 'empty' to go on: " answer
    [ "$answer" = empty ] || { echo "nothing deleted"; exit 1; }
fi
endpoint=$("$NEBIUS" ai endpoint get-by-name --parent-id "$PROJECT" --name "$ENDPOINT_NAME" --format jsonpath='{.metadata.id}')
echo "stopping $endpoint"
"$NEBIUS" ai endpoint stop --id "$endpoint" >/dev/null
script=$(mktemp)
trap 'rm -f "$script"' EXIT
printf 'find "%s" -mindepth 1 -delete && echo "reset: %s is empty"\n' "$VAULT" "$VAULT" > "$script"
job=$("$NEBIUS" ai job create --parent-id "$PROJECT" --name "reset-$(date -u +%Y%m%dt%H%M%S)" --image "$IMAGE:demo" \
    --platform cpu-d3 --preset 2vcpu-8gb --disk-size 20Gi --timeout 1h --restart-policy never \
    --volume "$FILESYSTEM:/data:rw" --env "REFRESHER_VAULT=$VAULT" --inject-file "$script:/reset.sh" \
    --container-command sh --args /reset.sh --async --format jsonpath='{.resource_id}' | tail -1)
echo "emptying with $job"
for _ in $(seq 1 90); do
    state=$("$NEBIUS" ai job get --id "$job" --format jsonpath='{.status.state}')
    case "$state" in COMPLETED|FAILED|CANCELLED|ERROR) break ;; esac
    sleep 10
done
"$NEBIUS" ai job logs "$job" 2>&1 | grep "^reset:" || echo "reset job ended $state; see: $NEBIUS ai job logs $job"
"$NEBIUS" ai job delete --id "$job" >/dev/null
echo "starting $endpoint"
"$NEBIUS" ai endpoint start --id "$endpoint" >/dev/null
[ "$state" = COMPLETED ]
