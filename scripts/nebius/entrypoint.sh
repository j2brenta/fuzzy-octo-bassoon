#!/bin/sh
# Nebius Serverless mounts the shared filesystem owned by root: make the vault folder the app user's, then run the
# command as that user. On a read-only mount (the MCP endpoint) the folder must already exist; the app creates it.
set -eu
vault="${REFRESHER_VAULT:-/data/telegram}"
{ mkdir -p "$vault" && chown 10001:10001 "$vault"; } 2>/dev/null || true
exec setpriv --reuid=10001 --regid=10001 --init-groups "$@"
