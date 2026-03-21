#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$PROJECT_ROOT/infra"

check_gcp_account

COMMAND="${1:-}"
shift || true

cd "$INFRA_DIR"
GCP_PROJECT="$(pulumi config get gcp:project 2>/dev/null || true)"
GCP_REGION="$(pulumi config get gcp:region 2>/dev/null || echo "us-central1")"
STACK="$(pulumi stack --show-name 2>/dev/null || true)"

if [[ -z "$GCP_PROJECT" ]]; then
  error "Cannot determine GCP project. Run 'make env-staging' or 'make env-prod' first."
  exit 1
fi

WORKFLOW_NAME="kalilos-${STACK}-report-flow"

case "$COMMAND" in
  list)
    log "Recent workflow executions for '$WORKFLOW_NAME'..."
    echo ""

    gcloud workflows executions list "$WORKFLOW_NAME" \
      --project="$GCP_PROJECT" \
      --location="$GCP_REGION" \
      --limit=20 \
      --format="table(name.basename(), state, startTime, endTime, error.message)" \
      --sort-by="~startTime"
    echo ""
    ;;

  cancel)
    EXECUTION_ID="${1:-}"
    if [[ -z "$EXECUTION_ID" ]]; then
      error "Usage: make workflows-cancel ID=<execution_id>"
      exit 1
    fi

    log "Cancelling workflow execution '$EXECUTION_ID'..."

    gcloud workflows executions cancel "$EXECUTION_ID" \
      --workflow="$WORKFLOW_NAME" \
      --project="$GCP_PROJECT" \
      --location="$GCP_REGION"

    log "Execution '$EXECUTION_ID' cancelled."
    ;;

  *)
    error "Unknown command '$COMMAND'."
    echo "Usage:"
    echo "  make workflows-status           List recent workflow executions"
    echo "  make workflows-cancel ID=<id>   Cancel a workflow execution"
    exit 1
    ;;
esac
