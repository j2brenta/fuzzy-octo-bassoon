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
