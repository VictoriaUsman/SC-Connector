#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$PROJECT_ROOT/infra"

check_gcp_account

PASS=0
FAIL=0
CHECKS=()

check() {
  local name="$1"
  shift
  if "$@" &>/dev/null; then
    CHECKS+=("  ✓  $name")
    PASS=$((PASS + 1))
  else
    CHECKS+=("  ✗  $name")
    FAIL=$((FAIL + 1))
  fi
}

cd "$INFRA_DIR"

STACK="$(pulumi stack --show-name 2>/dev/null || true)"
if [[ -z "$STACK" ]]; then
  error "No Pulumi stack selected. Run 'make env-staging' or 'make env-prod' first."
  exit 1
fi

GCP_PROJECT="$(pulumi config get gcp:project 2>/dev/null || true)"
GCP_REGION="$(pulumi config get gcp:region 2>/dev/null || echo "us-central1")"

echo ""
echo "============================================"
echo "  Health Check — $STACK"
echo "  Project: $GCP_PROJECT"
echo "============================================"
echo ""

log "Running health checks..."
echo ""

# 1. Pulumi stack
check "Pulumi stack is configured" \
  pulumi stack --show-name

# 2. GCP project accessible
check "GCP project is accessible" \
  gcloud projects describe "$GCP_PROJECT"

# 3. Firestore
check "Firestore is accessible" \
  gcloud firestore databases describe --project="$GCP_PROJECT"

# 4. Cloud Scheduler
check "Cloud Scheduler jobs exist" \
  bash -c "gcloud scheduler jobs list --project='$GCP_PROJECT' --location='$GCP_REGION' --format='value(name)' 2>/dev/null | head -1 | grep -q ."

# 5. Cloud Workflow
WORKFLOW_NAME="kalilos-${STACK}-report-flow"
check "Cloud Workflow '$WORKFLOW_NAME' is deployed" \
  gcloud workflows describe "$WORKFLOW_NAME" --project="$GCP_PROJECT" --location="$GCP_REGION"

# 6. Cloud Functions (all deployed functions — keep in sync with infra FUNCTION_DEFS)
EXPECTED_FUNCTIONS=(
  "auth" "scheduler" "create-report" "poll-status" "download-upload" "api"
  "ingest-bigquery" "event-report-scheduler" "slack-bot" "daily-recap"
)
for fn in "${EXPECTED_FUNCTIONS[@]}"; do
  FULL_NAME="kalilos-${STACK}-${fn}"
  check "Cloud Function '$FULL_NAME' is active" \
    gcloud functions describe "$FULL_NAME" --project="$GCP_PROJECT" --region="$GCP_REGION" --gen2
done

# 6b. API actually responds over HTTP (deploy can "pass" while the app is broken)
API_URL="$(pulumi stack output api_url 2>/dev/null || true)"
if [[ -n "$API_URL" ]]; then
  check "API /health responds 200" \
    bash -c "curl -fsS --max-time 15 -o /dev/null '${API_URL%/}/health'"
  check "API /health?deep=1 readiness (Firestore reachable)" \
    bash -c "curl -fsS --max-time 20 -o /dev/null '${API_URL%/}/health?deep=1'"
else
  CHECKS+=("  ✗  API /health responds 200 (could not resolve api_url)")
  ((FAIL++))
fi

# 7. Google Drive folder
ENV_FILE="$PROJECT_ROOT/.env.$STACK"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

if [[ -n "${GDRIVE_ROOT_FOLDER_ID:-}" && "$GDRIVE_ROOT_FOLDER_ID" != *"<"* ]]; then
  check "Google Drive root folder is configured" \
    test -n "$GDRIVE_ROOT_FOLDER_ID"
else
  CHECKS+=("  ✗  Google Drive root folder is configured")
  ((FAIL++))
fi

# Print results
echo "  Results:"
echo "  --------"
for c in "${CHECKS[@]}"; do
  echo "$c"
done

echo ""
echo "  Passed: $PASS  |  Failed: $FAIL"
echo ""

if [[ "$FAIL" -gt 0 ]]; then
  error "$FAIL health check(s) failed."
  exit 1
else
  log "All health checks passed."
fi
