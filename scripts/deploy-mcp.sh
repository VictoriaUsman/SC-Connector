#!/usr/bin/env bash
# Build and deploy the MCP server container to Cloud Run.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"
check_gcp_account

PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ ! -f "$PROJECT_ROOT/.env.active" ]]; then
  error "No active environment. Run 'make env-staging' or 'make env-prod' first."
  exit 1
fi

source "$PROJECT_ROOT/.env.active"
ENV="${ENVIRONMENT:?ENVIRONMENT not set}"
GCP_PROJECT="${GCP_PROJECT:?GCP_PROJECT not set}"
REGION="${REGION:-us-central1}"

RESOURCE_NAME="kalilos-${ENV}-mcp"
REPO_NAME="${RESOURCE_NAME}-repo"
IMAGE="${REGION}-docker.pkg.dev/${GCP_PROJECT}/${REPO_NAME}/${RESOURCE_NAME}:latest"

log "Building MCP server image: $IMAGE"

cd "$PROJECT_ROOT/mcp-server"

gcloud builds submit \
  --tag "$IMAGE" \
  --project "$GCP_PROJECT" \
  --quiet

log "Image pushed. Deploying via Pulumi..."

cd "$PROJECT_ROOT"
./scripts/deploy-infra.sh

log "MCP server deployed. URL:"
cd "$PROJECT_ROOT/infra"
source "$SCRIPT_DIR/_common.sh"
pulumi stack output mcp_server_url --stack "$ENV" 2>/dev/null || echo "(run 'make deploy-infra' to see the URL)"
