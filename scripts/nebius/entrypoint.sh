#!/bin/sh
# Nebius Serverless mounts the shared filesystem owned by root: make the vault folder the app user's, then run the
# command as that user. Where the filesystem refuses the change of owner, the command runs as root (logged), as the
# container is the endpoint's alone. REFRESHER_READ_ONLY=1 (the MCP endpoint) needs only to read the vault.
set -u
vault="${REFRESHER_VAULT:-/data/telegram}"
as_app="setpriv --reuid=10001 --regid=10001 --init-groups"
mkdir -p "$vault" 2>/dev/null
chown 10001:10001 "$vault" 2>/dev/null || chmod 0777 "$vault" 2>/dev/null
echo "refresher-entrypoint: build ${REFRESHER_BUILD:-dev}; $vault is $(stat -c '%U:%G %a' "$vault" 2>&1)" >&2
if [ "${REFRESHER_READ_ONLY:-0}" = 1 ]; then check="-r"; else check="-w"; fi
if $as_app test $check "$vault" -a -x "$vault"; then
    exec $as_app "$@"
fi
echo "refresher-entrypoint: the app user cannot use $vault; running as root" >&2
exec "$@"
