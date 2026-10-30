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
MODELS="${MODELS:-scripts/nebius/models.env}"  # model settings, no secrets

# The model settings from MODELS as --env flags in the array `envs`, with the vault and the per-run caps.
model_envs() {
    envs=()
    while IFS= read -r line; do
        [[ "$line" =~ ^[A-Z_]+=.+ ]] && envs+=(--env "$line")
    done < "$MODELS"
    envs+=(--env "REFRESHER_VAULT=$VAULT" --env REFRESHER_RUN_TOKENS=2000000 --env REFRESHER_RUN_SECONDS=1800)
}
