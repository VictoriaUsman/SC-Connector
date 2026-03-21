#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$PROJECT_ROOT/infra"

check_gcp_account

MODE="${1:-}"
NAME="${2:-}"
LIMIT="${LIMIT:-50}"

cd "$INFRA_DIR"
GCP_PROJECT="$(pulumi config get gcp:project 2>/dev/null || true)"
STACK="$(pulumi stack --show-name 2>/dev/null || true)"

if [[ -z "$GCP_PROJECT" ]]; then
  error "Cannot determine GCP project. Run 'make env-staging' or 'make env-prod' first."
  exit 1
fi

case "$MODE" in
  function)
    if [[ -z "$NAME" ]]; then
      error "Usage: make logs-fn NAME=<function_name>"
      exit 1
    fi

    FUNCTION_NAME="kalilos-${STACK}-${NAME}"
    log "Tailing logs for Cloud Function '$FUNCTION_NAME' (last $LIMIT entries)..."
    echo ""

    gcloud logging read \
      "resource.type=\"cloud_function\" AND resource.labels.function_name=\"$FUNCTION_NAME\"" \
      --project="$GCP_PROJECT" \
      --limit="$LIMIT" \
      --format="table(timestamp, severity, textPayload)" \
      --order=desc
    ;;

  workflow)
    log "Tailing Cloud Workflow logs (last $LIMIT entries)..."
    echo ""

    gcloud logging read \
      "resource.type=\"workflows.googleapis.com/Workflow\"" \
      --project="$GCP_PROJECT" \
      --limit="$LIMIT" \
      --format="table(timestamp, severity, textPayload)" \
      --order=desc
    ;;

  *)
    error "Unknown mode '$MODE'."
    echo "Usage:"
    echo "  make logs-fn NAME=<function_name>   Tail Cloud Function logs"
    echo "  make logs-workflow                   Tail Cloud Workflow logs"
    exit 1
    ;;
esac

echo ""
