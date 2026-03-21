#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$PROJECT_ROOT/infra"

check_gcp_account

cd "$INFRA_DIR"

STACK="$(pulumi stack --show-name 2>/dev/null || true)"
if [[ -z "$STACK" ]]; then
  error "No Pulumi stack selected. Run 'make env-staging' or 'make env-prod' first."
  exit 1
fi

GCP_PROJECT="$(pulumi config get gcp:project 2>/dev/null || true)"

log "Previewing infrastructure changes..."
log "  Stack:   $STACK"
log "  Project: $GCP_PROJECT"
echo ""

# Load env file if available
ENV_FILE="$PROJECT_ROOT/.env.$STACK"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

pulumi preview --diff

echo ""
log "Preview complete. Run 'make deploy-infra' to apply these changes."
echo ""
