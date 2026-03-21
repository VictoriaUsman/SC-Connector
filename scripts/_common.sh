#!/usr/bin/env bash
# Shared helpers and guards for all Kalilos Connector scripts.
# Source this file at the top of every script:
#   source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

set -euo pipefail

REQUIRED_GCP_ACCOUNT="nivbraz90@gmail.com"

export PULUMI_BACKEND_URL="${PULUMI_BACKEND_URL:-file://~/.pulumi-local}"
export PULUMI_CONFIG_PASSPHRASE="${PULUMI_CONFIG_PASSPHRASE:-}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
error() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2; }

check_gcp_account() {
  local current
  current="$(gcloud config get-value account 2>/dev/null || echo "")"
  if [[ "$current" != "$REQUIRED_GCP_ACCOUNT" ]]; then
    error "Wrong GCP account active: ${current:-<none>}"
    error "This project requires: $REQUIRED_GCP_ACCOUNT"
    error "Run: gcloud auth login $REQUIRED_GCP_ACCOUNT"
    exit 1
  fi
}

# When called directly (e.g., from Makefile check-auth target)
if [[ "${1:-}" == "check" ]]; then
  check_gcp_account
  log "GCP account verified: $REQUIRED_GCP_ACCOUNT"
fi
