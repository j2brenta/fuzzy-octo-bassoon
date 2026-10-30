#!/bin/bash
# Build the nebius image for linux/amd64 and push it as :<commit> and :demo (the tag the endpoint runs).
# Logs in with a short-lived Nebius token in a throwaway Docker config, so ~/.docker is left alone.
set -euo pipefail
cd "$(dirname "$0")/../.."
source scripts/nebius/config.sh
build=$(git rev-parse --short HEAD)$(git diff --quiet HEAD -- . ':!*.md' || echo -dirty)
docker buildx build --platform linux/amd64 --target nebius --build-arg BUILD="$build" -t refresher:nebius --load .
export DOCKER_HOST="${DOCKER_HOST:-unix://$HOME/.docker/run/docker.sock}" DOCKER_CONFIG
DOCKER_CONFIG=$(mktemp -d)
trap 'rm -rf "$DOCKER_CONFIG"' EXIT
"$NEBIUS" iam get-access-token | docker login "${REGISTRY%%/*}" --username iam --password-stdin >/dev/null
for tag in "$build" demo; do
    docker tag refresher:nebius "$IMAGE:$tag"
    docker push -q "$IMAGE:$tag"
done
echo "pushed $IMAGE:$build as :demo"
