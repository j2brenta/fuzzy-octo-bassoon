#!/bin/bash
# The app (port 8080) and the read-only MCP server (port 8081) in one container: one Nebius endpoint, so the tenant's
# second instance is free for the hourly review job. When either stops, the other is stopped too and the container
# exits, so the endpoint restarts both.
set -u
refresher-mcp --http --host 0.0.0.0 --port 8081 &
mcp=$!
refresher serve --host 0.0.0.0 --port 8080 &
app=$!
trap 'kill -TERM "$app" "$mcp" 2>/dev/null' TERM INT
wait -n "$app" "$mcp"
status=$?
echo "app-and-mcp: a process stopped (exit $status); stopping the other" >&2
kill -TERM "$app" "$mcp" 2>/dev/null
wait
exit "$status"
