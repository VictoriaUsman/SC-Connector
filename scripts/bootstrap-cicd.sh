#!/usr/bin/env bash
# One-time CI/CD bootstrap for the Kalilos Connector.
#
# Run this ONCE, locally, with your owner credentials (ian@kalilos.com).
# It is idempotent — safe to re-run. It performs:
#   1. Creates a GCS bucket to hold shared Pulumi state.
#   2. Migrates the existing local-file Pulumi state (staging + prod) into it.
#   3. Creates a least-privilege deploy service account for GitHub Actions.
#   4. Creates a Workload Identity Federation pool + GitHub OIDC provider.
#   5. Lets the GitHub repo impersonate the deploy SA (keyless auth).
#   6. Prints the GitHub repo variables/secrets to configure.
#
# After this runs, all `make` commands (local + CI) read/write the same
# Pulumi state from GCS — no more laptop-only state.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_common.sh"

PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$PROJECT_ROOT/infra"

# --- Configuration -----------------------------------------------------------
STATE_PROJECT="kalilos-connector-dev"       # project that owns the state bucket + WIF + deploy SA
STATE_BUCKET="gs://sc-connector-pulumi-state"
GCS_BACKEND_URL="$STATE_BUCKET"
LOCAL_BACKEND_URL="file://~/.pulumi-local"

DEPLOY_SA_NAME="kalilos-cicd-deployer"
DEPLOY_SA="${DEPLOY_SA_NAME}@${STATE_PROJECT}.iam.gserviceaccount.com"

WIF_POOL="github-pool"
WIF_PROVIDER="github"
GITHUB_REPO="VictoriaUsman/SC-Connector"

# Roles granted to the deploy SA on the staging project. Pragmatic
# least-privilege: roles/editor covers most resource CRUD, plus the IAM /
# service-enablement / hosting roles editor lacks. Tighten later if desired.
# Note: roles/editor deliberately excludes several services' IAM-management
# permissions even though it covers most other CRUD — each Pulumi resource
# that calls SetIamPolicy on a per-resource basis needs its own admin role:
#   - SecretIamMember (workflow SA -> Supabase key secret)  needs secretmanager.admin
#   - ServiceIamMember (workflow SA -> Cloud Run invoker)   needs run.admin
DEPLOY_SA_ROLES=(
  "roles/editor"
  "roles/resourcemanager.projectIamAdmin"
  "roles/iam.serviceAccountAdmin"
  "roles/serviceusage.serviceUsageAdmin"
  "roles/firebasehosting.admin"
  "roles/iam.serviceAccountUser"
  "roles/secretmanager.admin"
  "roles/run.admin"
)

# Stacks to migrate from the local backend into GCS.
STACKS=("staging" "prod")
# -----------------------------------------------------------------------------

check_gcp_account

log "Verifying required CLIs are installed..."
for cli in gcloud pulumi; do
  command -v "$cli" >/dev/null 2>&1 || { error "'$cli' not found on PATH."; exit 1; }
done

PROJECT_NUMBER="$(gcloud projects describe "$STATE_PROJECT" --format='value(projectNumber)')"
if [[ -z "$PROJECT_NUMBER" ]]; then
  error "Could not resolve project number for $STATE_PROJECT."
  exit 1
fi
log "State project: $STATE_PROJECT (number: $PROJECT_NUMBER)"

# === 1. State bucket =========================================================
log "Ensuring Pulumi state bucket exists: $STATE_BUCKET"
if gcloud storage buckets describe "$STATE_BUCKET" --project "$STATE_PROJECT" &>/dev/null; then
  log "  Bucket already exists."
else
  gcloud storage buckets create "$STATE_BUCKET" \
    --project "$STATE_PROJECT" \
    --location "us-central1" \
    --uniform-bucket-level-access
  log "  Bucket created."
fi
log "Enabling object versioning on the state bucket..."
gcloud storage buckets update "$STATE_BUCKET" --versioning >/dev/null

# === 2. Migrate Pulumi state (local file backend -> GCS) =====================
cd "$INFRA_DIR"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

for stack in "${STACKS[@]}"; do
  log "Migrating Pulumi stack '$stack'..."

  # Skip if the stack already exists in the GCS backend (idempotency).
  if PULUMI_BACKEND_URL="$GCS_BACKEND_URL" pulumi stack ls 2>/dev/null \
       | grep -qE "^${stack}[[:space:]*]"; then
    log "  Stack '$stack' already present in GCS backend — skipping migration."
    continue
  fi

  # Export the stack from the local file backend.
  export_file="$TMP_DIR/${stack}.checkpoint.json"
  if ! PULUMI_BACKEND_URL="$LOCAL_BACKEND_URL" pulumi stack select "$stack" &>/dev/null; then
    log "  Stack '$stack' not found in local backend — nothing to migrate, skipping."
    continue
  fi
  PULUMI_BACKEND_URL="$LOCAL_BACKEND_URL" pulumi stack export --stack "$stack" --file "$export_file"
  log "  Exported local state for '$stack'."

  # Initialize and import into the GCS backend.
  PULUMI_BACKEND_URL="$GCS_BACKEND_URL" pulumi stack init "$stack"
  PULUMI_BACKEND_URL="$GCS_BACKEND_URL" pulumi stack import --stack "$stack" --file "$export_file"
  log "  Imported state for '$stack' into $GCS_BACKEND_URL."
done

log "State migration complete. Run 'make env-staging && make preview' afterwards to confirm zero drift."

# === 3. Deploy service account ===============================================
log "Ensuring deploy service account exists: $DEPLOY_SA"
if gcloud iam service-accounts describe "$DEPLOY_SA" --project "$STATE_PROJECT" &>/dev/null; then
  log "  Service account already exists."
else
  gcloud iam service-accounts create "$DEPLOY_SA_NAME" \
    --project "$STATE_PROJECT" \
    --display-name "Kalilos CI/CD deployer (GitHub Actions)"
  log "  Service account created."
fi

# === 4. Grant roles ==========================================================
log "Granting project roles to the deploy SA..."
for role in "${DEPLOY_SA_ROLES[@]}"; do
  log "  + $role"
  gcloud projects add-iam-policy-binding "$STATE_PROJECT" \
    --member "serviceAccount:${DEPLOY_SA}" \
    --role "$role" \
    --condition=None \
    --quiet >/dev/null
done

log "Granting state-bucket access to the deploy SA..."
gcloud storage buckets add-iam-policy-binding "$STATE_BUCKET" \
  --member "serviceAccount:${DEPLOY_SA}" \
  --role "roles/storage.admin" \
  --quiet >/dev/null

# === 5. Workload Identity Federation =========================================
log "Ensuring Workload Identity pool '$WIF_POOL' exists..."
if gcloud iam workload-identity-pools describe "$WIF_POOL" \
     --project "$STATE_PROJECT" --location global &>/dev/null; then
  log "  Pool already exists."
else
  gcloud iam workload-identity-pools create "$WIF_POOL" \
    --project "$STATE_PROJECT" --location global \
    --display-name "GitHub Actions pool"
  log "  Pool created."
fi

log "Ensuring OIDC provider '$WIF_PROVIDER' exists..."
if gcloud iam workload-identity-pools providers describe "$WIF_PROVIDER" \
     --project "$STATE_PROJECT" --location global \
     --workload-identity-pool "$WIF_POOL" &>/dev/null; then
  log "  Provider already exists."
else
  gcloud iam workload-identity-pools providers create-oidc "$WIF_PROVIDER" \
    --project "$STATE_PROJECT" --location global \
    --workload-identity-pool "$WIF_POOL" \
    --display-name "GitHub OIDC" \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition "assertion.repository == '${GITHUB_REPO}'"
  log "  Provider created."
fi

# === 6. Let the GitHub repo impersonate the deploy SA ========================
PRINCIPAL_SET="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/attribute.repository/${GITHUB_REPO}"
log "Binding repo principalSet -> workloadIdentityUser on the deploy SA..."
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" \
  --project "$STATE_PROJECT" \
  --role "roles/iam.workloadIdentityUser" \
  --member "$PRINCIPAL_SET" \
  --quiet >/dev/null

WIF_PROVIDER_RESOURCE="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}/providers/${WIF_PROVIDER}"

# === Done ====================================================================
echo ""
echo "============================================================"
echo "  CI/CD bootstrap complete"
echo "============================================================"
echo ""
echo "Set these as GitHub repository VARIABLES (Settings > Secrets and variables > Actions > Variables):"
echo ""
echo "  GCP_WIF_PROVIDER = ${WIF_PROVIDER_RESOURCE}"
echo "  GCP_DEPLOY_SA    = ${DEPLOY_SA}"
echo "  GCP_PROJECT      = ${STATE_PROJECT}"
echo "  GCP_REGION       = us-central1"
echo ""
echo "Set these as GitHub repository SECRETS (scoped to the 'dev' Environment):"
echo ""
echo "  PULUMI_CONFIG_PASSPHRASE = <new passphrase for the 'dev' stack>"
echo "  GDRIVE_ROOT_FOLDER_ID    = <dev Drive root folder id>"
echo "  VITE_API_URL             = <dev API URL, e.g. https://us-central1-${STATE_PROJECT}.cloudfunctions.net/kalilos-dev-api>"
echo "  VITE_API_KEY             = <dev API key>"
echo "  VITE_SUPABASE_URL        = <Supabase project URL>"
echo "  VITE_SUPABASE_ANON_KEY   = <Supabase anon key>"
echo ""
echo "This also needs a Pulumi secret (not a GitHub secret) for Cloud Functions"
echo "to reach Postgres — set it once against the 'dev' stack:"
echo ""
echo "  cd infra && pulumi stack select dev"
echo "  pulumi config set --secret kalilos:supabase-db-url <postgresql://...>"
echo ""
echo "Next steps:"
echo "  1. ./scripts/init-stack.sh dev         # one-time: create the 'dev' Pulumi stack"
echo "  2. make env-dev && make preview        # confirm the plan before first deploy"
echo "  3. Configure the GitHub vars/secrets above."
echo "  4. Merge a PR to main to trigger the deploy."
echo ""
