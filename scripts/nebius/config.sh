# Where the demo runs (sourced by the other scripts). eu-west1: the new tenant has no CPU quota in eu-north1, and
# allows two instances per region: the endpoint (app + MCP) and the review job.
NEBIUS="${NEBIUS:-$HOME/.nebius/bin/nebius}"
PROJECT="${PROJECT:-project-e01bn5tzpa00cdbng71pff}"
REGISTRY="${REGISTRY:-cr.eu-west1.nebius.cloud/e01xabbdzg97xbc1f1}"
IMAGE="$REGISTRY/refresher"
FILESYSTEM="${FILESYSTEM:-computefilesystem-e01zb936t3yg01rmys}"
SECRET="${SECRET:-refresher-demo}"  # NEBIUS_API_KEY, REFRESHER_LOGIN, MCP_TOKEN
ENDPOINT_NAME="${ENDPOINT_NAME:-refresher}"
VAULT=/data/demo
PUBLIC_HOSTS="https://*.tunnel.applications.eu-west1.nebius.cloud"
ENV_FILE="${ENV_FILE:-.env.demo}"  # model settings; its secrets go to the Nebius secret, never into flags

# The model settings from ENV_FILE as --env flags in the array `envs` (no secrets: those come from SECRET).
model_envs() {
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
    envs+=(--env "REFRESHER_VAULT=$VAULT" --env REFRESHER_RUN_TOKENS=2000000 --env REFRESHER_RUN_SECONDS=1800)
}
