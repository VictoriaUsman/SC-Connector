#!/usr/bin/env bash
# Shared helpers and guards for all Kalilos Connector scripts.
# Source this file at the top of every script:
#   source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

set -euo pipefail

REQUIRED_GCP_ACCOUNT="nivbraz90@gmail.com"
GCLOUD_CONFIG_NAME="kalilos-connector"

export CLOUDSDK_ACTIVE_CONFIG_NAME="$GCLOUD_CONFIG_NAME"
export PULUMI_BACKEND_URL="${PULUMI_BACKEND_URL:-file://~/.pulumi-local}"
export PULUMI_CONFIG_PASSPHRASE="${PULUMI_CONFIG_PASSPHRASE:-}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
error() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2; }

check_gcp_account() {
  if ! gcloud config configurations describe "$GCLOUD_CONFIG_NAME" &>/dev/null; then
    error "gcloud configuration '$GCLOUD_CONFIG_NAME' not found."
    error "Create it once with:"
    error "  gcloud config configurations create $GCLOUD_CONFIG_NAME"
    error "  gcloud auth login $REQUIRED_GCP_ACCOUNT --configuration=$GCLOUD_CONFIG_NAME"
    error "  gcloud config set project kalilos-connector-staging --configuration=$GCLOUD_CONFIG_NAME"
    exit 1
  fi

  local current
  current="$(gcloud config get-value account 2>/dev/null || echo "")"
  if [[ "$current" != "$REQUIRED_GCP_ACCOUNT" ]]; then
    error "Wrong account in '$GCLOUD_CONFIG_NAME' configuration: ${current:-<none>}"
    error "Expected: $REQUIRED_GCP_ACCOUNT"
    error "Fix with: gcloud auth login $REQUIRED_GCP_ACCOUNT --configuration=$GCLOUD_CONFIG_NAME"
    exit 1
  fi
}

# When called directly (e.g., from Makefile check-auth target)
if [[ "${1:-}" == "check" ]]; then
  check_gcp_account
  log "GCP account verified: $REQUIRED_GCP_ACCOUNT (config: $GCLOUD_CONFIG_NAME)"
fi
