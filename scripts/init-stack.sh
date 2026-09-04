#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$PROJECT_ROOT/infra"

check_gcp_account

ENV="${1:-}"

if [[ -z "$ENV" ]]; then
  error "Usage: init-stack.sh <dev|staging|prod>"
  exit 1
fi

if [[ "$ENV" != "dev" && "$ENV" != "staging" && "$ENV" != "prod" ]]; then
  error "Invalid environment '$ENV'. Must be 'dev', 'staging', or 'prod'."
  exit 1
fi

GCP_PROJECT="kalilos-connector-$ENV"
GCP_REGION="us-central1"

log "Initializing Pulumi stack '$ENV' for project '$GCP_PROJECT'..."

cd "$INFRA_DIR"

if pulumi stack ls 2>/dev/null | grep -qE "^${ENV}[[:space:]*]"; then
  error "Stack '$ENV' already exists. Use 'make env-$ENV' to switch to it."
  exit 1
fi

pulumi stack init "$ENV"

log "Setting GCP configuration..."
pulumi config set gcp:project "$GCP_PROJECT"
pulumi config set gcp:region "$GCP_REGION"

log "Setting gcloud project..."
gcloud config set project "$GCP_PROJECT" --quiet

echo ""
echo "============================================"
echo "  Stack '$ENV' initialized successfully"
echo "============================================"
echo ""
echo "  Pulumi stack: $ENV"
echo "  GCP project:  $GCP_PROJECT"
echo "  GCP region:   $GCP_REGION"
echo ""
echo "  Next steps:"
echo "    1. Create the GCP project if it doesn't exist:"
echo "       gcloud projects create $GCP_PROJECT"
echo "    2. Set up billing for the project"
echo "    3. Fill in .env.$ENV with the correct values"
echo "    4. Run: make env-$ENV && make preview"
echo ""
echo "  Note: GCP APIs are enabled automatically by Pulumi (infra/resources/apis.py)."
echo ""
