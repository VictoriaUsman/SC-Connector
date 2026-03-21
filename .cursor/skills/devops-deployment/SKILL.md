---
name: devops-deployment
description: Deployment, operations, and troubleshooting for the Kalilos connector. Use when deploying infrastructure, managing environments, viewing logs, managing secrets, running health checks, or troubleshooting failed workflows. Use when the user asks to deploy, check status, view logs, or fix issues.
---

# DevOps & Deployment

## Deployment Commands

**All operations go through the Makefile.** Never run raw `gcloud`, `pulumi`, or `firebase` commands.

### Environment Selection

```bash
make env-staging          # Switch to staging (kalilos-connector-staging)
make env-prod             # Switch to production (requires confirmation)
make env-status           # Show current environment
```

### Deploy

```bash
make preview              # Dry run — show planned changes
make deploy-infra         # Deploy Pulumi infrastructure (functions, workflows, scheduler, IAM)
make deploy-frontend      # Build + deploy React app to Firebase Hosting
make deploy-all           # Both infra + frontend, in order
make health               # Verify all services are up and responding
```

### Standard Deploy Flow

```bash
# Staging
make env-staging && make preview && make deploy-all && make health

# Production (includes interactive confirmation)
make env-prod && make preview && make deploy-all && make health
```

## Environment Architecture

| | Staging | Production |
|---|---------|------------|
| GCP Project | `kalilos-connector-staging` | `kalilos-connector-prod` |
| Pulumi Stack | `staging` | `prod` |
| Env File | `.env.staging` | `.env.prod` |
| Firebase Site | `kalilos-connector-staging` | `kalilos-connector-prod` |
| Resource Prefix | `kalilos-staging-*` | `kalilos-prod-*` |

## Secret Management

Secrets are stored in GCP Secret Manager. Naming convention: `kalilos-{env}-{service}-{client}`.

```bash
# List all secrets
make secret-list

# Get a secret value
make secret-get NAME=kalilos-staging-sp-api-acme

# Set/update a secret (JSON value for API credentials)
make secret-set NAME=kalilos-staging-sp-api-acme VALUE='{"refresh_token":"Atzr|...","client_id":"amzn1.application-oa2-client.abc123","client_secret":"abc123"}'

# Set Ads API credentials
make secret-set NAME=kalilos-staging-ads-api-acme VALUE='{"refresh_token":"Atzr|...","client_id":"amzn1.application-oa2-client.abc123","client_secret":"abc123","profile_id":"1234567890"}'
```

### Secret Structure

**SP API credentials:**
```json
{
  "refresh_token": "Atzr|...",
  "client_id": "amzn1.application-oa2-client.abc123",
  "client_secret": "abc123secret"
}
```

**Ads API credentials:**
```json
{
  "refresh_token": "Atzr|...",
  "client_id": "amzn1.application-oa2-client.abc123",
  "client_secret": "abc123secret",
  "profile_id": "1234567890"
}
```

## Log Access

```bash
# Tail logs for a specific Cloud Function
make logs-fn NAME=create_report
make logs-fn NAME=poll_status
make logs-fn NAME=download_upload
make logs-fn NAME=auth
make logs-fn NAME=scheduler
make logs-fn NAME=api

# Tail Cloud Workflow execution logs
make logs-workflow

# List recent workflow executions with status
make workflows-status
```

### Reading Logs in GCP Console

Structured log queries for Cloud Logging:

```
# All errors from Cloud Functions
resource.type="cloud_function"
severity>=ERROR

# Specific function logs
resource.type="cloud_function"
resource.labels.function_name="kalilos-staging-create-report"

# Workflow execution logs
resource.type="workflows.googleapis.com/Workflow"
resource.labels.workflow_id="kalilos-staging-report-flow"

# Filter by client
jsonPayload.client_id="acme"
```

## Workflow Operations

```bash
# List recent executions
make workflows-status

# Cancel a stuck workflow
make workflows-cancel ID=projects/kalilos-connector-staging/locations/us-central1/workflows/kalilos-staging-report-flow/executions/abc123

# Trigger a manual workflow execution (for testing)
make workflow-trigger API_SOURCE=sp_api CLIENT=acme REPORT_TYPE=GET_FLAT_FILE_OPEN_LISTINGS_DATA MARKETPLACE=ATVPDKIKX0DER
```

## Local Development

```bash
# Run a Cloud Function locally
make local-fn NAME=create_report PORT=8080

# Run the React frontend dev server
make local-frontend

# Seed Firestore with test data
make seed-firestore
```

### Testing a Function Locally

```bash
# Start the function
make local-fn NAME=create_report PORT=8080

# In another terminal, send a test request
curl -X POST http://localhost:8080 \
  -H "Content-Type: application/json" \
  -d '{"api_source": "sp_api", "client_id": "acme", "report_type": "GET_FLAT_FILE_OPEN_LISTINGS_DATA", "marketplace_id": "ATVPDKIKX0DER"}'
```

## Troubleshooting Guide

### 429 Rate Limit Errors

**Symptom:** Cloud Function returns 429 from Amazon APIs.

**Cause:** Too many concurrent workflows hitting Amazon API rate limits.

**Fix:**
1. Check concurrent workflow count: `make workflows-status`
2. Reduce scheduler frequency in Firestore `schedules` collection
3. Add concurrency limits in the scheduler function
4. SP API `createReport` is limited to 0.0167 req/sec (1 per minute sustained)

### Token Expired / 401 Unauthorized

**Symptom:** Auth function returns 401 from LWA endpoint.

**Cause:** Refresh token has been revoked or is invalid.

**Fix:**
1. Verify the secret: `make secret-get NAME=kalilos-{env}-sp-api-{client}`
2. Get a new refresh token from Amazon Seller Central / Ads Console
3. Update: `make secret-set NAME=... VALUE='{"refresh_token":"new_token",...}'`

### Google Drive Quota / Permission Errors

**Symptom:** `download_upload` function fails with 403 from Drive API.

**Cause:** Service account lacks access, or Drive API quota exceeded.

**Fix:**
1. Check `make health` for Drive status
2. Verify service account email has Editor access on the root Drive folder
3. Check Drive API quota in GCP Console (APIs & Services > Dashboard)

### Workflow Stuck / Not Completing

**Symptom:** Workflow execution shows IN_PROGRESS for an unusually long time.

**Cause:** Poll loop may be stuck, or a Cloud Function is timing out.

**Fix:**
1. Check logs: `make logs-workflow`
2. Check the specific function: `make logs-fn NAME=poll_status`
3. Cancel if needed: `make workflows-cancel ID=...`
4. If recurring, check if the report type is valid for the marketplace

### Cloud Function Timeout

**Symptom:** Function logs show "deadline exceeded" or execution time exceeds configured timeout.

**Cause:** Report download is too large, or external API is slow.

**Fix:**
1. Check function memory/timeout in `infra/resources/functions.py`
2. Increase timeout (max 540s for HTTP-triggered functions, 3600s for event-triggered)
3. Increase memory if OOM (check for "memory limit exceeded" in logs)
4. Deploy: `make deploy-infra`

### Firestore Permission Denied

**Symptom:** Frontend shows permission errors, or API function can't read/write Firestore.

**Fix:**
1. Check Firestore security rules in `infra/resources/firestore.py`
2. Verify the Cloud Function service account has `roles/datastore.user`
3. Check IAM bindings in `infra/resources/iam.py`

## Health Check Details

`make health` verifies:

1. All Cloud Functions are deployed and responding (HTTP 200 on health endpoint)
2. Cloud Workflow exists and is in ACTIVE state
3. Cloud Scheduler jobs are configured and not paused
4. Firestore is accessible
5. Google Drive root folder is accessible
6. Secret Manager secrets exist (does not read values)

## Reference

See `runbooks.md` in this directory for detailed step-by-step operational runbooks.
