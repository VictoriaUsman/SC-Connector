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
GCP_REGION="$(pulumi config get gcp:region 2>/dev/null || echo "us-central1")"
WORKFLOW_NAME="kalilos-${STACK}-report-flow"

# Parse arguments
API_SOURCE="${1:?Usage: make workflow-trigger API_SOURCE=sp_api CLIENT_ID=test-client MARKETPLACE=US REPORT_TYPE=GET_FLAT_FILE_OPEN_LISTINGS_DATA}"
CLIENT_ID="${2:?Missing CLIENT_ID}"
MARKETPLACE="${3:-US}"
REPORT_TYPE="${4:?Missing REPORT_TYPE}"
START_DATE="${5:-}"
END_DATE="${6:-}"

# Build report_params based on api_source
if [[ "$API_SOURCE" == "sp_api" ]]; then
  if [[ -n "$START_DATE" && -n "$END_DATE" ]]; then
    REPORT_PARAMS="{\"dataStartTime\":\"${START_DATE}T00:00:00Z\",\"dataEndTime\":\"${END_DATE}T23:59:59Z\"}"
  else
    REPORT_PARAMS="{}"
  fi
elif [[ "$API_SOURCE" == "ads_api" ]]; then
  if [[ -z "$START_DATE" || -z "$END_DATE" ]]; then
    error "Ads API requires START_DATE and END_DATE (YYYY-MM-DD)"
    exit 1
  fi
  REPORT_PARAMS="{\"startDate\":\"${START_DATE}\",\"endDate\":\"${END_DATE}\",\"adProduct\":\"SPONSORED_PRODUCTS\",\"groupBy\":[\"campaign\"],\"columns\":[\"campaignName\",\"campaignId\",\"impressions\",\"clicks\",\"cost\"],\"timeUnit\":\"DAILY\"}"
else
  error "API_SOURCE must be 'sp_api' or 'ads_api'"
  exit 1
fi

PAYLOAD=$(cat <<EOF
{
  "api_source": "${API_SOURCE}",
  "client_id": "${CLIENT_ID}",
  "marketplace": "${MARKETPLACE}",
  "report_type": "${REPORT_TYPE}",
  "report_params": ${REPORT_PARAMS},
  "frequency": "on_demand"
}
EOF
)

echo ""
log "Triggering workflow: $WORKFLOW_NAME"
log "  Project:     $GCP_PROJECT"
log "  API source:  $API_SOURCE"
log "  Client:      $CLIENT_ID"
log "  Marketplace: $MARKETPLACE"
log "  Report type: $REPORT_TYPE"
echo ""
log "Payload:"
echo "$PAYLOAD" | python3 -m json.tool 2>/dev/null || echo "$PAYLOAD"
echo ""

RESULT=$(gcloud workflows run "$WORKFLOW_NAME" \
  --project="$GCP_PROJECT" \
  --location="$GCP_REGION" \
  --data="$PAYLOAD" \
  --format=json 2>&1)

EXEC_NAME=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['name'])" 2>/dev/null || echo "")

if [[ -n "$EXEC_NAME" ]]; then
  log "Workflow execution started!"
  log "  Execution: $EXEC_NAME"
  echo ""
  log "Monitor with:"
  log "  make workflows-status"
  log "  gcloud workflows executions describe '$EXEC_NAME' --location=$GCP_REGION"
  log "  make logs-fn NAME=create_report"
else
  echo "$RESULT"
  error "Failed to start workflow execution"
  exit 1
fi
