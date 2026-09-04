#!/usr/bin/env bash
# Shared helpers and guards for all Kalilos Connector scripts.
# Source this file at the top of every script:
#   source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

set -euo pipefail

REQUIRED_GCP_ACCOUNT="ian@kalilos.com"
GCLOUD_CONFIG_NAME="kalilos-connector"

# Detect CI (GitHub Actions sets CI=true). In CI, auth comes from Workload
# Identity Federation (ADC), not the local gcloud named configuration.
is_ci() { [[ -n "${CI:-}" || -n "${KALILOS_CI:-}" ]]; }

# Only force the local gcloud configuration outside of CI; in CI the active
# config is the WIF-provided ADC and must not be overridden.
if ! is_ci; then
  export CLOUDSDK_ACTIVE_CONFIG_NAME="$GCLOUD_CONFIG_NAME"
fi

# Shared Pulumi state lives in GCS so local and CI share one source of truth.
export PULUMI_BACKEND_URL="${PULUMI_BACKEND_URL:-gs://sc-connector-pulumi-state}"
export PULUMI_CONFIG_PASSPHRASE="${PULUMI_CONFIG_PASSPHRASE:-}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
error() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2; }

check_gcp_account() {
  # In CI, authentication is provided via Workload Identity Federation (ADC);
  # the local gcloud account guard does not apply.
  if is_ci; then
    log "CI detected — skipping local gcloud account guard (using ADC)."
    return 0
  fi

  if ! gcloud config configurations describe "$GCLOUD_CONFIG_NAME" &>/dev/null; then
    error "gcloud configuration '$GCLOUD_CONFIG_NAME' not found."
    error "Create it once with:"
    error "  gcloud config configurations create $GCLOUD_CONFIG_NAME"
    error "  gcloud auth login $REQUIRED_GCP_ACCOUNT --configuration=$GCLOUD_CONFIG_NAME"
    error "  gcloud config set project kalilos-connector-dev --configuration=$GCLOUD_CONFIG_NAME"
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
