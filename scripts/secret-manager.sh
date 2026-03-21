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

if [[ -z "$GCP_PROJECT" ]]; then
  error "Cannot determine GCP project. Run 'make env-staging' or 'make env-prod' first."
  exit 1
fi

case "$COMMAND" in
  set)
    SECRET_NAME="${1:-}"
    SECRET_VALUE="${2:-}"

    if [[ -z "$SECRET_NAME" || -z "$SECRET_VALUE" ]]; then
      error "Usage: make secret-set NAME=<name> VALUE=<value>"
      exit 1
    fi

    log "Setting secret '$SECRET_NAME' in project '$GCP_PROJECT'..."

    if gcloud secrets describe "$SECRET_NAME" --project="$GCP_PROJECT" &>/dev/null; then
      echo -n "$SECRET_VALUE" | gcloud secrets versions add "$SECRET_NAME" \
        --project="$GCP_PROJECT" --data-file=-
      log "Added new version for existing secret '$SECRET_NAME'."
    else
      echo -n "$SECRET_VALUE" | gcloud secrets create "$SECRET_NAME" \
        --project="$GCP_PROJECT" --data-file=- --replication-policy="automatic"
      log "Created new secret '$SECRET_NAME'."
    fi
    ;;

  get)
    SECRET_NAME="${1:-}"

    if [[ -z "$SECRET_NAME" ]]; then
      error "Usage: make secret-get NAME=<name>"
      exit 1
    fi

    log "Fetching secret '$SECRET_NAME' from project '$GCP_PROJECT'..."
    gcloud secrets versions access latest \
      --secret="$SECRET_NAME" --project="$GCP_PROJECT"
    echo ""
    ;;

  list)
    log "Listing secrets in project '$GCP_PROJECT'..."
    echo ""
    gcloud secrets list --project="$GCP_PROJECT" \
      --format="table(name.basename(), createTime, replication.automatic)"
    echo ""
    ;;

  *)
    error "Unknown command '$COMMAND'."
    echo "Usage: secret-manager.sh <set|get|list> [args...]"
    echo ""
    echo "  set <name> <value>   Create or update a secret"
    echo "  get <name>           Retrieve the latest version of a secret"
    echo "  list                 List all secrets in the current project"
    exit 1
    ;;
esac
