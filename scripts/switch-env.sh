#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$PROJECT_ROOT/infra"

check_gcp_account

ENV="${1:-}"

if [[ -z "$ENV" ]]; then
  error "Usage: switch-env.sh <staging|prod>"
  exit 1
fi

if [[ "$ENV" != "staging" && "$ENV" != "prod" ]]; then
  error "Invalid environment '$ENV'. Must be 'staging' or 'prod'."
  exit 1
fi

if [[ "$ENV" == "prod" ]]; then
  echo ""
  echo "=========================================="
  echo "  WARNING: Switching to PRODUCTION"
  echo "=========================================="
  echo ""
  read -rp "Are you sure? [y/N] " confirm
  if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    log "Aborted."
    exit 0
  fi
fi

log "Switching to $ENV environment..."

cd "$INFRA_DIR"
pulumi stack select "$ENV"

ENV_FILE="$PROJECT_ROOT/.env.$ENV"
if [[ -f "$ENV_FILE" ]]; then
  log "Loading environment from $ENV_FILE"
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a

  if [[ -n "${GCP_PROJECT:-}" ]]; then
    log "Setting gcloud project to $GCP_PROJECT"
    gcloud config set project "$GCP_PROJECT" --quiet
  fi
fi

echo ""
log "Environment switched to: $ENV"
log "Pulumi stack: $(pulumi stack --show-name)"
log "GCP project:  $(gcloud config get-value project 2>/dev/null)"
echo ""
