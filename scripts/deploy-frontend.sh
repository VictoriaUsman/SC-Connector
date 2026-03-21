#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$PROJECT_ROOT/infra"
FRONTEND_DIR="$PROJECT_ROOT/frontend"

check_gcp_account

if [[ ! -d "$FRONTEND_DIR" ]]; then
  error "Frontend directory not found at $FRONTEND_DIR"
  exit 1
fi

cd "$INFRA_DIR"
STACK="$(pulumi stack --show-name 2>/dev/null || true)"
if [[ -z "$STACK" ]]; then
  error "No Pulumi stack selected. Run 'make env-staging' or 'make env-prod' first."
  exit 1
fi

FIREBASE_PROJECT="kalilos-connector-$STACK"

log "Building and deploying frontend..."
log "  Stack:            $STACK"
log "  Firebase project: $FIREBASE_PROJECT"
echo ""

# Load env file for the build
ENV_FILE="$PROJECT_ROOT/.env.$STACK"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

cd "$FRONTEND_DIR"

log "Installing dependencies..."
npm ci --silent

log "Building frontend..."
npm run build

log "Deploying to Firebase Hosting..."
firebase deploy --only hosting --project "$FIREBASE_PROJECT"

echo ""
log "Frontend deployment complete."
log "  Project: $FIREBASE_PROJECT"
echo ""
