#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$PROJECT_ROOT/infra"

echo ""
echo "============================================"
echo "  Kalilos Connector — Environment Status"
echo "============================================"
echo ""

cd "$INFRA_DIR"

STACK="$(pulumi stack --show-name 2>/dev/null || echo '<not set>')"
GCP_PROJECT="$(pulumi config get gcp:project 2>/dev/null || echo '<not set>')"
GCP_REGION="$(pulumi config get gcp:region 2>/dev/null || echo '<not set>')"
GCLOUD_PROJECT="$(gcloud config get-value project 2>/dev/null || echo '<not set>')"
GCLOUD_ACCOUNT="$(gcloud config get-value account 2>/dev/null || echo '<not set>')"

printf "  %-20s %s\n" "Pulumi stack:" "$STACK"
printf "  %-20s %s\n" "GCP project:" "$GCP_PROJECT"
printf "  %-20s %s\n" "GCP region:" "$GCP_REGION"
printf "  %-20s %s\n" "gcloud project:" "$GCLOUD_PROJECT"
printf "  %-20s %s\n" "gcloud account:" "$GCLOUD_ACCOUNT"

# Warn if gcloud project doesn't match Pulumi config
if [[ "$GCP_PROJECT" != '<not set>' && "$GCLOUD_PROJECT" != "$GCP_PROJECT" ]]; then
  echo ""
  error "gcloud project ($GCLOUD_PROJECT) does not match Pulumi config ($GCP_PROJECT)!"
  error "Run 'make env-staging' or 'make env-prod' to synchronize."
fi

echo ""
