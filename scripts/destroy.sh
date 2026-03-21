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

echo ""
echo "============================================"
echo "  DESTROY ALL INFRASTRUCTURE"
echo "============================================"
echo ""
echo "  Stack:   $STACK"
echo "  Project: $GCP_PROJECT"
echo ""
echo "  This will PERMANENTLY DELETE all resources"
echo "  managed by this Pulumi stack."
echo ""

read -rp "Type the environment name ($STACK) to confirm: " confirm
if [[ "$confirm" != "$STACK" ]]; then
  log "Aborted — confirmation did not match."
  exit 0
fi

read -rp "Are you ABSOLUTELY sure? [y/N] " final_confirm
if [[ "$final_confirm" != "y" && "$final_confirm" != "Y" ]]; then
  log "Aborted."
  exit 0
fi

echo ""
log "Destroying infrastructure for stack '$STACK'..."

pulumi destroy --yes

echo ""
log "Infrastructure destroyed for stack '$STACK'."
echo ""
