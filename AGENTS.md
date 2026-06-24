# Kalilos Amazon Reports Connector

> **GCP Identity**: This project uses `nivbraz90@gmail.com`. All deployment scripts verify the active gcloud account and abort if wrong. Use gcloud named configurations to avoid switching: `gcloud config configurations activate kalilos`. The Makefile `check-auth` target runs automatically before any deploy. When Pulumi fails with permission errors, set `export GOOGLE_OAUTH_ACCESS_TOKEN=$(gcloud auth print-access-token --account=nivbraz90@gmail.com)` before running `make deploy-infra`.

## Purpose

Serverless connector on GCP that downloads Amazon SP API and Ads API reports, plus synchronous Amazon API data pulls (first: SP-API Replenishment / Subscribe & Save), for multiple clients and marketplaces on configurable schedules. Results are stored in Google Drive and selected tables in BigQuery. An employee-facing React UI allows configuration of clients, schedules, and on-demand report/API requests.

## Architecture

The system is fully serverless on GCP, organized around one Cloud Workflow with two execution modes: the default async report Wait+Poll pipeline for SP API and Ads API reports, and a synchronous API-call branch for non-report Amazon endpoints.

**Orchestration**: GCP Cloud Workflows executes a single `report_flow.yaml` that accepts an `api_source` parameter (`"sp_api"` or `"ads_api"`) plus an optional `mode`. `mode="report"` (default) calls Cloud Functions for each async report step: authenticate, create report request, poll until ready, download and upload to Google Drive. Polling uses exponential backoff (30s base, 120s max). `mode="api_call"` skips create/poll/download and calls `fetch_api` for synchronous REST operations, then runs the same best-effort BigQuery ingestion step. The workflow also passes `folder_name` and `subfolder_strategy` for custom Drive folder paths.

**Compute**: Cloud Functions (Python 3.12, 2nd gen) handle each discrete step. Report functions receive the `api_source` param and branch internally to call the correct Amazon API. `fetch_api` dispatches registered synchronous API operations (currently Replenishment / S&S). Functions are stateless; all state lives in Firestore.

**Storage & Config**: Firestore stores client configurations, report schedules, and job history. Collections: `clients`, `schedules`, `jobs`, `_drive_folder_locks` (transient coordination docs). The frontend reads these in real-time via Firestore listeners.

**Scheduling**: Cloud Scheduler triggers a scheduler Cloud Function on a cron. The scheduler reads active schedules from Firestore, resolves each schedule's `timeframe` strategy into a date range (default: yesterday), and launches Cloud Workflow executions for each `(client, marketplace, report_type)` tuple. When `api_source` is `"both"`, each report type's effective API source is inferred from its membership in Ads report types, API operations, or SP report types. Clients without credentials for the schedule's API source are silently skipped. For normal reports using the default `yesterday` strategy, reconciliation jobs (T-3, T-7) re-pull data updated by Amazon. Multi-day strategies and synchronous API operations skip reconciliation.

**Three-Clock Timezone Architecture**:
- **Backend Clock (UTC)**: All timestamps in Firestore, all scheduler logic runs on UTC.
- **Marketplace Clock (Local)**: `MARKETPLACE_TIMEZONES` in `functions/shared/config.py` maps each marketplace to its IANA timezone. The scheduler uses this to compute "yesterday" in the marketplace's local time and convert to UTC API request timestamps.
- **User Clock (Display)**: Frontend formats UTC timestamps using the browser's local timezone for display only.

**Secrets**: GCP Secret Manager stores Amazon credentials (LWA refresh tokens, Ads API tokens). Secret naming: `kalilos-{env}-{service}-{client}` (e.g., `kalilos-prod-sp-api-acme`).

**Report Storage**: Google Drive via a GCP service account with native IAM. Default folder layout is date-first: `{root}/{YYYY-MM-DD}/{client}/{marketplace}/{report_type}/`. A custom `folder_name` acts as a prefix: `{root}/{folder_name}/{YYYY-MM-DD}/{client}/{marketplace}/{report_type}/`. Setting `subfolder_strategy` to "none" drops the date segment. Folder creation is coordinated across concurrent workflow executions using Firestore-based distributed locks (`_drive_folder_locks` collection) to prevent duplicate folders from Drive's eventually-consistent search API.

**Frontend**: React + Vite + shadcn/ui on Firebase Hosting. Uses Firestore real-time listeners for live job status. Calls an API Gateway backed by a Cloud Function for mutations. The Schedules page supports inline editing (Edit dialog) and immediate triggering (Run Now) from the row dropdown menu. Report types are organized by domain in the selector dropdown (Listings, Orders, FBA, Returns, Financial, Brand Analytics for SP; Sponsored Products/Brands/Display for Ads), with descriptions and constraint warnings visible inline.

**MCP Server (Agentic Interface)**: A FastMCP (Python) server deployed on Cloud Run (`kalilos-{env}-mcp`) that exposes 14 tools for Claude agents to manage reports. The MCP server is a thin proxy that delegates all operations to the existing REST API via HTTP. It supports Streamable HTTP transport for remote access (Claude.ai, Claude Desktop, Anthropic API) and stdio for local development (Cursor, Claude Code). Authentication uses a static bearer token validated against `MCP_API_KEY`. Tools cover clients (list, get), schedules (CRUD + trigger), jobs (list, get, retry), on-demand reports, and report type discovery.

**Workflow Error Handling**: The workflow YAML uses a global try/except pattern — `main` calls a `report_pipeline` subworkflow, and any unhandled error (auth failure, create_report error, etc.) is caught by the global handler which marks the Firestore job as `"failed"` using `args.job_id`. This prevents zombie "pending" jobs. Error serialization uses `json.encode(e)` (not `string(e)`, which crashes on dicts). The workflow service account has `roles/datastore.user` for Firestore REST API access.

**Synchronous API Operations**: Non-report endpoints are registered in `functions/shared/api_operations.py` and launch with `mode="api_call"`. The reusable REST transports are `sp_api_rest.py` and `ads_api_rest.py`; both use cached LWA access tokens and bounded retry for 429/5xx responses. The first supported sync operations are SP-API Replenishment v2022-11-07 for Subscribe & Save:
- `SNS_OFFER_METRICS` -> `/replenishment/2022-11-07/offers/metrics/search` (offer/ASIN metrics)
- `SNS_SP_METRICS` -> `/replenishment/2022-11-07/sellingPartners/metrics/search` (account metrics)
- `SNS_OFFERS` -> `/replenishment/2022-11-07/offers/search` (offer enrollment/config)

Replenishment uses offset pagination (`pagination.limit` + `pagination.offset`) and requires request fields nested under `filters` for `offers/metrics/search`. Amazon's `WEEK` aggregation is Sunday-Saturday; `replenishment_client.py` aligns requested windows to those Amazon weeks.

**Infrastructure as Code**: Pulumi (Python) manages all GCP resources. Shared GCS state backend (`gs://kalilos-connector-pulumi-state`, versioned) so local and CI deploys share one source of truth. Two stacks: `staging` and `prod`, mapping to separate GCP projects (`kalilos-connector-staging` and `kalilos-connector-prod`).

## Data Model

### Schedule (Firestore `schedules` collection)

```python
{
    "name": "Month to Date Reports",         # Optional display name
    "client_ids": ["acme", "globex"],        # Multi-select: one or more clients
    "api_source": "both",                    # "sp_api" | "ads_api" | "both"
    "report_types": ["GET_SALES_AND_TRAFFIC_REPORT", "spCampaigns"],  # One or more
    "marketplaces": ["US", "CA", "UK"],      # Multi-select: one or more marketplaces
    "frequency": "daily",                    # "hourly" | "daily" | "weekly" | "monthly"
    "schedule_config": {                     # Flexible scheduling
        "type": "weekly",
        "time": "03:00",                     # UTC
        "days_of_week": [0, 2, 4],           # 0=Mon..6=Sun (for weekly)
        "day_of_month": 15,                  # (for monthly)
    },
    "timeframe": {                           # Date range strategy (optional, defaults to yesterday)
        "strategy": "last_n_days",           # yesterday | today | last_n_days | rolling_window
                                             # | last_calendar_week | last_calendar_month
        "days": 30,                          # (last_n_days) trailing window size
        "end_offset_days": 3,                # (last_n_days) shift end back for delayed data
        "start_offset": -7,                  # (rolling_window) days from today
        "end_offset": -1,                    # (rolling_window) days from today
        "week_start": 0,                     # (last_calendar_week) 0=Mon..6=Sun
    },
    "folder_name": "WoW Weekly Reports",     # Custom Drive folder (empty = default layout)
    "subfolder_strategy": "date",            # "date" = YYYY-MM-DD subfolders, "none" = flat
    "reconciliation_days": [3, 7],           # Re-pull data from N days ago (yesterday strategy only)
    "report_params": {},
    "is_active": true,
    "next_run_at": "2026-03-21T03:00:00Z",
    "last_run_at": "2026-03-20T03:00:00Z",
}
```

Schedules without `timeframe` default to `{"strategy": "yesterday"}` for backward compatibility. Reconciliation (T-3, T-7 re-pulls) only applies to the `yesterday` strategy — multi-day strategies inherently cover wider windows.

### Marketplace Timezone Mapping

Defined in `functions/shared/config.py`:

| Marketplace | Timezone | Notes |
|-------------|----------|-------|
| US, CA, MX  | America/Los_Angeles | Amazon uses PST/PDT for North America |
| UK          | Europe/London | |
| DE, FR, IT, ES, NL, SE, PL | Europe/Paris | Central European |
| TR          | Europe/Istanbul | |
| AU          | Australia/Sydney | |
| SG          | Asia/Singapore | |

## Project Structure

```
kalilos-connector/
├── AGENTS.md                         # This file — universal agent briefing
├── Makefile                          # All operations entry point
├── .env.example                      # Template for environment variables
├── .env.staging                      # Staging environment config (git-ignored)
├── .env.prod                         # Production environment config (git-ignored)
├── .cursor/
│   ├── rules/                        # Cursor rules (6 .mdc files)
│   └── skills/                       # Cursor skills (5 SKILL.md files)
├── .mcp.json                          # MCP server config for Cursor/Claude Code
├── scripts/
│   ├── _common.sh                    # Shared helpers, GCP guard, Pulumi backend config
│   ├── deploy-infra.sh               # Pulumi deploy wrapper
│   ├── deploy-frontend.sh            # Firebase deploy wrapper
│   ├── deploy-mcp.sh                 # Build + deploy MCP server to Cloud Run
│   ├── health-check.sh               # Post-deploy verification (12 checks)
│   ├── seed-firestore.sh             # Seed test data
│   ├── rotate-secrets.sh             # Secret rotation helper
│   └── wipe-firestore.py             # Wipe all Firestore collections
├── infra/
│   ├── __main__.py                   # Pulumi entry point
│   ├── Pulumi.yaml                   # Pulumi project config
│   ├── Pulumi.staging.yaml           # Staging stack config
│   ├── Pulumi.prod.yaml              # Prod stack config
│   ├── requirements.txt
│   └── resources/
│       ├── functions.py              # Cloud Function definitions
│       ├── workflow.py               # Cloud Workflow definition
│       ├── scheduler.py              # Cloud Scheduler jobs
│       ├── firestore.py              # Firestore indexes and rules
│       ├── api_gateway.py            # API Gateway for frontend
│       ├── mcp_server.py            # MCP server Cloud Run service + Artifact Registry
│       ├── secrets.py                # Secret Manager resources
│       └── iam.py                    # IAM bindings
├── functions/
│   ├── auth/
│   │   └── main.py                   # Token refresh (LWA + Ads API)
│   ├── scheduler/
│   │   └── main.py                   # Read schedules, fan out per (client, marketplace)
│   ├── create_report/
│   │   └── main.py                   # Request report from Amazon
│   ├── poll_status/
│   │   └── main.py                   # Check report generation status
│   ├── download_upload/
│   │   └── main.py                   # Download report, upload to Drive
│   ├── fetch_api/
│   │   └── main.py                   # Synchronous API operations (Replenishment / S&S)
│   ├── api/
│   │   └── main.py                   # Frontend API (CRUD schedules, clients)
│   └── shared/
│       ├── sp_api_client.py          # SP API HTTP client
│       ├── ads_api_client.py         # Ads API HTTP client
│       ├── sp_api_rest.py            # Generic SP-API REST transport for non-report endpoints
│       ├── ads_api_rest.py           # Generic Ads API REST transport for non-report endpoints
│       ├── api_operations.py         # Registry for synchronous API operations
│       ├── replenishment_client.py   # SP-API Replenishment / Subscribe & Save client
│       ├── ads_report_config.py      # Ads report type definitions (columns, groupBy)
│       ├── credentials.py            # SP/Ads credential retrieval from Secret Manager
│       ├── drive_client.py           # Google Drive upload, Firestore-coordinated folder creation
│       ├── report_converter.py       # JSON→TSV flattening for SP API + Ads API reports
│       ├── firestore_utils.py        # Common Firestore operations
│       ├── config.py                 # Env, config, marketplace timezones
│       ├── schedule_compute.py       # Timezone-aware dates, date ranges, flexible next_run_at
│       └── workflow_launcher.py      # Unified workflow launch, retry, per-(client,mkt,report) fan-out
├── mcp-server/
│   ├── pyproject.toml                 # FastMCP + httpx dependencies
│   ├── Dockerfile                     # Cloud Run container image
│   ├── server.py                      # FastMCP app with 14 tools
│   ├── api_client.py                  # Typed async HTTP client wrapping the REST API
│   ├── auth.py                        # Bearer token auth (StaticTokenVerifier)
│   └── README.md                      # Setup guide for all Claude surfaces
├── tests/
│   ├── seed_report_test_schedules.py  # Seed test schedules covering all report types
│   ├── test_drive_client.py
│   ├── test_report_converter.py
│   ├── test_scheduler.py
│   ├── test_workflow_launcher.py
│   ├── test_api.py
│   └── test_schedule_compute.py
├── workflows/
│   └── report_flow.yaml              # Cloud Workflow: unified report pipeline (global error handler)
└── frontend/
    ├── package.json
    ├── vite.config.ts
    ├── tsconfig.json
    ├── index.html
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── pages/
        │   ├── dashboard.tsx          # Job status overview
        │   ├── clients.tsx            # Client management
        │   ├── schedules.tsx          # Schedule configuration (multi-select, flexible freq)
        │   └── on-demand.tsx          # On-demand report requests
        ├── components/                # Shared UI components
        │   ├── report-selector.tsx    # API source + report type multi-select + marketplace selector
        │   ├── sp-report-config.tsx   # SP API report options (dateGranularity, etc.)
        │   ├── ads-report-config.tsx  # Ads API column/dimension picker (collapsible)
        │   ├── report-columns-preview.tsx  # Expandable column preview for any report type
        │   └── folder-config.tsx      # Drive folder name, subfolder strategy, path preview
        ├── data/
        │   ├── report-metadata.ts     # Static SP API report column catalog and options
        │   └── report-categories.ts   # Report type categorization by domain, with descriptions
        ├── hooks/                     # Custom React hooks
        ├── lib/                       # Utilities and Firebase config
        └── types/                     # TypeScript type definitions
```

## Deployment

All operations go through the Makefile. Never run raw `gcloud`, `pulumi`, or `firebase` commands.

Pulumi uses a shared GCS backend (`gs://kalilos-connector-pulumi-state`), configured automatically by `scripts/_common.sh`. Local and CI deploys read/write the same state. (Override with `PULUMI_BACKEND_URL` if needed.)

```bash
# Deploy to staging
make env-staging && make preview && make deploy-all && make health

# Deploy to production
make env-prod && make preview && make deploy-all && make health
```

`make deploy-all` runs infrastructure (Pulumi) first, then MCP server (Cloud Run), then frontend (Firebase Hosting), in order.

To deploy the MCP server independently: `make deploy-mcp`.

### CI/CD (GitHub Actions)

`.github/workflows/deploy-staging.yml` runs on every PR and on merge to `main`:

- **PRs and pushes** run the `checks` job: Python tests (`pytest tests/`) plus a frontend typecheck/build (`npm run build`).
- **Merges to `main`** additionally run `deploy-staging`, which authenticates to GCP via **Workload Identity Federation** (keyless — no SA JSON keys), then runs `make env-staging && make deploy-all`.

Auth: CI impersonates a least-privilege deploy service account (`kalilos-cicd-deployer@kalilos-connector-staging`). The `check_gcp_account` guard in `_common.sh` is skipped when `CI` is set (it uses Application Default Credentials from the WIF step instead). Production is **never** auto-deployed — it stays manual.

**One-time setup**: run `./scripts/bootstrap-cicd.sh` locally (with owner creds). It migrates Pulumi state to GCS, creates the deploy SA + roles, sets up the WIF pool/provider for the `NivOclear/kalilos-connector` repo, and prints the GitHub config values to set.

**GitHub repository variables** (Settings → Secrets and variables → Actions → Variables):

| Variable | Example |
|----------|---------|
| `GCP_WIF_PROVIDER` | `projects/<num>/locations/global/workloadIdentityPools/github-pool/providers/github` |
| `GCP_DEPLOY_SA` | `kalilos-cicd-deployer@kalilos-connector-staging.iam.gserviceaccount.com` |
| `GCP_PROJECT` | `kalilos-connector-staging` |
| `GCP_REGION` | `us-central1` |

**GitHub repository secrets** (scoped to the `staging` Environment):

| Secret | Purpose |
|--------|---------|
| `PULUMI_CONFIG_PASSPHRASE` | Decrypts the Pulumi config secrets |
| `GDRIVE_ROOT_FOLDER_ID` | Staging Drive root folder id |
| `VITE_API_URL`, `VITE_API_KEY` | Frontend build-time API config |
| `VITE_FIREBASE_API_KEY`, `VITE_FIREBASE_AUTH_DOMAIN`, `VITE_FIREBASE_PROJECT_ID` | Frontend Firebase config |

## Conventions

- **Python 3.12** with type hints on all function signatures.
- **Cloud Function signature**: `def handler(request: flask.Request) -> tuple[dict, int]`
- **API source branching**: Functions that interact with Amazon APIs accept an `api_source` parameter and branch:
  ```python
  if event["api_source"] == "sp_api":
      # SP API logic
  elif event["api_source"] == "ads_api":
      # Ads API logic
  ```
- **Resource naming**: `kalilos-{env}-{resource}` (e.g., `kalilos-staging-create-report`)
- **Secret naming**: `kalilos-{env}-{service}-{client}` (e.g., `kalilos-prod-sp-api-acme`)
- **Error handling**: Structured JSON responses with `error` and `code` fields. All errors logged to Cloud Logging.
- **Shell scripts**: Always source `_common.sh` (provides `set -euo pipefail`, logging, GCP guard, Pulumi backend).
- **Frontend**: TypeScript strict mode, no `any` types. Use shadcn/ui (base-ui) components. Multi-select uses Checkbox components, not Popover.
- **Timezone**: All backend timestamps UTC. Report dates computed from marketplace timezone. Frontend displays in user's browser timezone.

## Key Modules

### `functions/shared/schedule_compute.py`

Central module for timezone-aware scheduling:
- `get_marketplace_tz(marketplace)` — returns ZoneInfo for a marketplace
- `marketplace_yesterday(marketplace, utc_now)` — yesterday's date in marketplace local time
- `compute_date_range(marketplace, timeframe, utc_now)` — resolves a timeframe strategy config into `(start_date, end_date)` in marketplace local time. Supports: `yesterday`, `today`, `last_n_days`, `rolling_window`, `last_calendar_week`, `last_calendar_month`
- `compute_report_dates(marketplace, api_source, report_date, report_end_date=None)` — converts marketplace date(s) to API-specific date params. SP API: UTC dataStartTime/dataEndTime spanning the full range. Ads API: YYYY-MM-DD startDate/endDate strings
- `compute_next_run(from_time, schedule_config)` — flexible next run calculation supporting daily/weekly/monthly with time-of-day, day-of-week, day-of-month
- `VALID_TIMEFRAME_STRATEGIES` — set of recognized strategy names, used by API validation

### `functions/shared/workflow_launcher.py`

Unified workflow launch helpers used by the scheduler, API on-demand triggers, and manual triggers:
- `get_workflow_parent()` — builds the fully-qualified Cloud Workflows parent path from env vars
- `build_payload(...)` — constructs the canonical workflow execution payload dict, including `execution_date`
- `launch_execution(parent, payload, job_id)` — starts a Cloud Workflow execution with retry-and-backoff. Marks job as failed in Firestore on exhausted retries
- `launch_for_marketplace(parent, now, schedule, client_id, marketplace)` — launches primary + reconciliation jobs for one `(client, marketplace)` pair across all `report_types`. When `api_source` is `"both"`, infers effective api_source per report type. Computes `execution_date` (marketplace today) once and passes it through. Reconciliation only runs for normal reports using the `yesterday` strategy; API operations launch one `mode="api_call"` job for the full requested range
- Removed report guard: `removed_reports.py` marks permanently removed SP report types (`GET_FBA_SNS_PERFORMANCE_DATA`, `GET_FBA_SNS_FORECAST_DATA`) as deterministic `REPORT_REMOVED` failed jobs instead of launching doomed workflows

### `functions/shared/api_operations.py`

Registry for synchronous Amazon API operations:
- `API_OPERATIONS` maps pseudo-report ids (e.g. `SNS_OFFER_METRICS`) to `api_source`, handler key, labels, and BQ table names
- `is_api_operation(report_type)` lets the scheduler/API route operations to `mode="api_call"`
- Add new non-report endpoints here instead of treating them as SP/Ads report types

### `functions/shared/replenishment_client.py`

SP-API Replenishment v2022-11-07 client for Subscribe & Save:
- `list_offers(client_id, marketplace)` calls `/offers/search`
- `fetch_sp_metrics(...)` calls `/sellingPartners/metrics/search`
- `fetch_offer_metrics(...)` calls `/offers/metrics/search`
- Uses offset pagination, nested `filters`, and Sunday-Saturday week alignment for `aggregationFrequency="WEEK"`

### `functions/shared/drive_client.py`

Google Drive folder hierarchy and upload:
- **Folder date = execution date** (when the report was created), not the report data date. The `execution_date` is computed once at launch time and threaded through the workflow payload, ensuring all marketplace workflows from the same run target the same folder
- **Default path**: `{root}/{execution_date}/{client}/{marketplace}/{report_type}/`
- **Custom folder** (prefix): `{root}/{folder_name}/{execution_date}/{client}/{marketplace}/{report_type}/`
- **subfolder_strategy "none"** drops the date segment from either layout
- `build_folder_path()` returns `(folder_id, human_readable_path)`. Runs `_assert_no_duplicates()` on every folder segment as a runtime dedup guard
- Filename uses the **report data date** (not execution date): `{report_type}_{date}_{client}_{marketplace}.{ext}` for single-day, `{report_type}_{start}_to_{end}_{client}_{marketplace}.{ext}` for ranges
- **Concurrent folder creation** (4-layer defense):
  1. `find_or_create_folder()` — Firestore atomic `document.create()` as distributed lock. Winner creates, losers poll
  2. Stale lock detection — when a lock references a deleted Drive folder, it's cleared and the caller falls through to the dedup path (never recursively retries the lock, which caused a TOCTOU race)
  3. `_create_folder_with_dedup()` — fallback that sleeps, re-checks Drive, creates, then post-creation deduplicates by keeping the oldest folder and deleting extras
  4. `_assert_no_duplicates()` — runtime guard after every folder segment in `build_folder_path()` that detects and cleans up duplicates before the file is uploaded

### `functions/shared/report_converter.py`

JSON-to-TSV conversion for spreadsheet-friendly output:
- `should_convert(report_type, api_source)` — returns True for SP API JSON reports (Sales & Traffic, Brand Analytics, Ledger) and all Ads API reports
- `json_report_to_tsv(raw_bytes, report_type)` — SP API: parses JSON, locates data arrays, recursively flattens nested objects, outputs TSV
- `ads_json_report_to_tsv(raw_bytes)` — Ads API: flattens the columnar JSON format (`columns`/`index`/`data`) to TSV
- Called by `download_upload` before uploading to Drive; files are saved as `.tsv` for Google Sheets compatibility
- `rows_to_tsv(rows, output_columns=None)` is used by `fetch_api` for already-parsed synchronous API JSON rows

### `functions/shared/currency.py`

Live USD-based FX rates for converting per-marketplace metrics into one display currency (used by the hourly Slack bot's combined Total):
- `get_rates()` — returns USD-based rates, refreshing lazily when stale. Resolution order: fresh in-process cache -> fresh Firestore cache (`app_config/currency_rates`, holding `{rates, base, fetched_at}`) -> live fetch from `open.er-api.com` (free, no key). On a failed fetch it reuses the last stale cache (logged WARNING); with no cache at all it returns `{}` so callers skip the Total rather than assuming 1:1. TTL ~24h
- `convert(amount, from_ccy, to_ccy, rates) -> float | None` — identity when currencies match; otherwise cross-converts via the USD base. Returns `None` when either currency is missing (or non-positive), so a missing rate never silently assumes parity
- `app_config/currency_rates` is an auto-managed cache (API-only; the existing catch-all Firestore deny rule covers it — no new infra)

### `functions/slack_bot/main.py` — hourly event bot

Posts day-to-date Prime-Day metrics to Slack each hour during a live event.
- **Automatic multi-marketplace combining**: `account_family(client_id)` strips a trailing `-{marketplace}` suffix (only known marketplace codes; `-vc` and other suffixes like `leonisa-pr` stay separate accounts) to group configs like `moxe` + `moxe-ca` into one family. In the regular hourly slot a family spanning >1 marketplace is posted as a single message (per-marketplace native lines), under the shared per-channel/day thread anchor. The representative member (US member if present, else smallest `client_id`) supplies the display name + `base_currency`. This is automatic for ALL multi-marketplace accounts — no opt-in field
- **Converted Total**: `_maybe_add_total_row` keeps the native Total for single-currency families; for multi-currency it converts each marketplace's spend/ppc/sales into the representative `base_currency` via `currency.convert`, sums, recomputes ACoS/TACoS, and shows the number only. If any FX pair is unavailable it skips the Total and logs `MISSING_FX_RATE` (never assumes 1:1)
- **Per-SKU breakdown** stays Skylight/Ritual-only: a combined allowlisted family appends one per-marketplace breakdown for each marketplace in `_sku_breakdown_marketplaces()` (default US, CA, UK in display order; override via `SKU_BREAKDOWN_MARKETPLACES`). Each section is built from `_query_sku_breakdown(member_client_id, [marketplace], now)` and reconciled to that marketplace's own single-currency account line (USD/CAD/GBP), with the marketplace shown in the section header. Marketplaces the family doesn't cover, or any reconciliation miss, are silently skipped — every section is additive and independent. The orders/SKU day window is computed by the shared `_marketplace_day_window()` helper (live day-to-date for hourly, full calendar day for recaps), so the account and SKU queries provably share one window
- **Day-end recap breakdown**: the midnight "Day N Recap" also appends a per-SKU breakdown by reusing the same `_append_sku_breakdown` with `full_day=True` + `report_date=recap_date`. The recap stays per-config (one recap per account, single-currency), so each Skylight regional account's breakdown lands in its own recap message (all threaded under the same day anchor); it covers the completed recap day only (no cumulative SKU rows). The year-round `daily_recap` bot is untouched

## Common Agent Tasks

### Add a new Cloud Function

1. Create a new directory under `functions/` (e.g., `functions/my_function/main.py`).
2. Implement `def handler(request: flask.Request) -> tuple[dict, int]`.
3. Add the function resource to `infra/resources/functions.py`, following the existing pattern.
4. Add IAM bindings in `infra/resources/iam.py` if the function needs to call other services.
5. Deploy: `make deploy-infra`.

### Add a new report type

**For a new SP API report (TSV format):**
1. Add the report type ID to `SP_REPORT_TYPES` in `frontend/src/types/index.ts`.
2. Add it to the appropriate category in `frontend/src/data/report-categories.ts` (with label, description, and optional constraint).
3. Add column metadata to `SP_REPORT_METADATA` in `frontend/src/data/report-metadata.ts`.
4. Add it to `ALL_SP_REPORT_TYPES` in `tests/seed_report_test_schedules.py` (and to `_MONTHLY_ONLY_SP` if it requires `last_calendar_month`).
5. Re-run: `python3 tests/seed_report_test_schedules.py` to update test schedules.

**For a new SP API report (JSON format):**
All of the above, plus:
6. Add to `_SP_API_JSON_REPORTS` in `functions/shared/drive_client.py`.
7. Add the report type and its array key(s) to `_SP_API_ARRAY_KEYS` in `functions/shared/report_converter.py`.

**For a new Brand Analytics report:** also add to `_BRAND_ANALYTICS_REPORT_PERIOD` in `functions/create_report/main.py` with its allowed periods (DAY, WEEK, MONTH, QUARTER). The `create_report` function auto-injects `reportOptions.reportPeriod` based on the date range when not explicitly provided.

**For a new Ads API report:**
1. Add the config to `functions/shared/ads_report_config.py` (adProduct, groupBy, columns).
2. Add to `ADS_REPORT_TYPES` in `frontend/src/types/index.ts`.
3. Add to the appropriate category in `frontend/src/data/report-categories.ts`.
4. Add a label entry to `ADS_REPORT_LABELS` in `frontend/src/lib/format.ts`.
5. Re-run: `python3 tests/seed_report_test_schedules.py`.

**For a new synchronous API operation (non-report endpoint):**
1. Add the operation id/config to `functions/shared/api_operations.py`.
2. Add or extend a client module using `sp_api_rest.py` or `ads_api_rest.py` (do not force synchronous endpoints through create_report/poll/download).
3. Add TSV conversion through `rows_to_tsv()` or a purpose-built flattener if the response shape is unusual.
4. Add a BigQuery schema in `functions/shared/bq_schemas.py` and a matching table in `infra/resources/bigquery.py` if it should ingest to BQ.
5. Add the operation to the frontend selector and metadata (`frontend/src/types/index.ts`, `report-categories.ts`, `report-metadata.ts`).
6. Add tests for transport, client request shape/pagination, workflow_launcher `mode="api_call"` routing, and converter/BQ mapping.

### Report timeframe requirements

Not all reports support all timeframe strategies. Key constraints:

| Report Group | Supported Timeframes | Notes |
|---|---|---|
| Most SP API reports | Any (yesterday, last_n_days, etc.) | Standard TSV reports |
| Sales & Traffic | Any | Auto-injects `dateGranularity: DAY`, `asinGranularity: CHILD` |
| Brand Analytics Search Terms | Any | Supports DAY reportPeriod |
| Brand Analytics (Market Basket, Repeat Purchase) | `last_calendar_week`, `last_calendar_month` | No DAY support — dates must align to period boundaries |
| Brand Analytics (Search Query/Catalog Performance) | `last_calendar_week`, `last_calendar_month` | Same as above |
| Settlement Reports | N/A | Auto-generated by Amazon, cannot be requested |
| Subscribe & Save legacy reports | N/A | `GET_FBA_SNS_PERFORMANCE_DATA` and `GET_FBA_SNS_FORECAST_DATA` were removed by Amazon; use Replenishment operations instead |
| Replenishment / S&S operations | Prefer `last_calendar_week` | Amazon `WEEK` aggregation is Sunday-Saturday; `replenishment_client.py` aligns overlapping ranges to Amazon weeks |
| All Ads API reports | Any | Standard date range support |

### Test report schedules

`tests/seed_report_test_schedules.py` creates test schedules covering all report types:

```bash
python3 tests/seed_report_test_schedules.py                    # staging (default)
python3 tests/seed_report_test_schedules.py --project kalilos-connector-prod --client acme
python3 tests/seed_report_test_schedules.py --dry-run          # preview without writing
```

Creates inactive schedules (trigger via "Run Now" in the UI):
- **Test: SP Daily Reports** — all SP reports that work with `yesterday` timeframe
- **Test: SP Monthly Reports** — Brand Analytics reports needing `last_calendar_month`
- **Test: Ads Daily Reports** — all Ads API reports
- **Test: Subscribe & Save (Replenishment API)** — synchronous API operations (`SNS_OFFER_METRICS`, `SNS_SP_METRICS`, `SNS_OFFERS`)
- **Test: Vendor (1P) Reports** — vendor reports, only when `--vendor-client` is supplied

Re-running is idempotent — removes existing test schedules and recreates from current report registry. Non-requestable reports (settlements) are automatically skipped.

### Trigger a schedule immediately (Run Now)

`POST /schedules/<schedule_id>/trigger` fans out workflow executions for all (client, marketplace, report_type) tuples, identical to what the cron scheduler does. Clients without credentials for the API source are silently skipped. Reconciliation re-pulls are only included when the schedule's timeframe strategy is `yesterday` (the default). Multi-day strategies (`last_n_days`, `last_calendar_week`, etc.) skip reconciliation since they already cover wider windows. The frontend Schedules page exposes this via the "Run Now" dropdown action.

### Add a new marketplace

1. Add entry to `MARKETPLACE_IDS` in `functions/shared/config.py`.
2. Add entry to `MARKETPLACE_TIMEZONES` in `functions/shared/config.py`.
3. Add entry to `MARKETPLACE_TO_REGION` in `functions/shared/config.py`.
4. Add entry to `MARKETPLACES` array in `frontend/src/types/index.ts`.
5. Deploy: `make deploy-all`.

### Modify the workflow

1. Edit `workflows/report_flow.yaml`.
2. Preview: `make env-staging && make preview`.
3. Deploy: `make deploy-infra`.
4. Test with an on-demand report request via the UI.

### Add a new client

Use the frontend Clients page, or seed via `make seed-firestore`. The create form accepts name, marketplace IDs, and optionally SP API Refresh Token and Ads API Profile ID (auto-connected on creation). Credentials can also be added later via the row dropdown menu.

### Deploy to staging

```bash
make env-staging && make preview && make deploy-all && make health
```

### Deploy to production

```bash
make env-prod && make preview && make deploy-all && make health
```
Production requires an interactive confirmation prompt.

## Observability & Log Access

This project does **not** use Coralogix or any external log aggregator. All logs live in **GCP Cloud Logging** and **Firestore**.

### Log contract (structured logging)

Every Cloud Function and the report workflow emit **structured JSON** logs (set up
by `functions/shared/logging_setup.py`; the workflow uses `sys.log(json=...)`).
Each line lands in Cloud Logging as `jsonPayload` with a **stable set of fields**,
so you filter by field (e.g. `jsonPayload.job_id="..."`) instead of grepping text.
A correlated request/execution shares one `request_id` (API) or `job_id`
(pipeline), so a single filter returns the whole story.

| Field (`jsonPayload.*`) | Meaning |
|---|---|
| `severity` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` (top-level LogEntry severity) |
| `message` | Human-readable log message |
| `component` | Emitter: function name (`create-report`, `api`, ...) or `report-flow` |
| `phase` | Pipeline stage: `create_report`, `poll_status`, `download_upload`, `ingest_bigquery`, `workflow_error`, `report_failed`, `poll_timeout` |
| `job_id` | Firestore job id — the primary correlation key across functions + workflow |
| `request_id` | Per-API-request id (also returned in the `X-Request-Id` response header) |
| `client_id` | Client slug |
| `api_source` | `sp_api` or `ads_api` |
| `report_type` | Amazon report type |
| `marketplace` | Marketplace code (`US`, `UK`, ...) |
| `error_code` / `error_type` | Classification on failure logs |

When adding logs, keep these names stable and pass context via
`logger.info("msg", extra={"job_id": job_id, ...})` (it is flattened into
`jsonPayload` automatically). Per-request/job correlation ids are bound once at
the entrypoint via `bind_log_context(...)`. Log level is controlled by the
`LOG_LEVEL` env var (Pulumi config `kalilos:log-level`, default `INFO`).

### Alerting, health & config

Cloud Monitoring is provisioned by `infra/resources/monitoring.py` (kept lean):
- **Error-log alert** — fires when ERROR+ logs across functions + workflow exceed
  a threshold in 5 min (log-based metric `kalilos-{env}-error-logs`).
- **Uptime + alert** — an uptime check hits the API `/health` every 5 min and
  alerts when it fails.
- Notifications go to the email in Pulumi config `kalilos:alert-email` (unset =
  policies exist but stay silent).

Health endpoints on the API function:
- `GET /health` — cheap static liveness (used by the uptime check).
- `GET /health?deep=1` — readiness; verifies Firestore reachability, returns 503
  if degraded. `make health` probes both after deploy.

Optional Pulumi config keys (set with `pulumi config set kalilos:<key> <val>`):
`log-level` (default `INFO`), `alert-email`, `alert-error-threshold` (default `5`).

### Querying Cloud Function logs

```bash
# Errors from a specific function (last 2 hours)
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="kalilos-staging-create-report" AND severity>=ERROR AND timestamp>="2026-04-08T10:00:00Z"' \
  --project=kalilos-connector-staging --limit=20 --format=json

# Everything about one job across all functions + the workflow (the fast path)
gcloud logging read \
  'jsonPayload.job_id="JOB_ID_HERE"' \
  --project=kalilos-connector-staging --limit=50 --format=json --order=asc

# One API request end to end
gcloud logging read 'jsonPayload.request_id="REQUEST_ID_HERE"' \
  --project=kalilos-connector-staging --limit=50 --format=json

# All function names follow: kalilos-{env}-{function}
# Functions: auth, create-report, poll-status, download-upload, scheduler, api,
#            ingest-bigquery, event-report-scheduler, slack-bot, daily-recap
```

### Querying Cloud Workflow logs

```bash
# Workflow execution errors (most useful for debugging failed jobs)
gcloud logging read \
  'resource.type="workflows.googleapis.com/Workflow" AND severity>=ERROR AND timestamp>="2026-04-08T10:00:00Z"' \
  --project=kalilos-connector-staging --limit=20 --format=json

# A specific workflow phase for one job (structured)
gcloud logging read \
  'resource.type="workflows.googleapis.com/Workflow" AND jsonPayload.job_id="JOB_ID_HERE"' \
  --project=kalilos-connector-staging --limit=50 --format=json --order=asc
```

### Searching for specific errors

```bash
# A failed report's raw Amazon status (structured field)
gcloud logging read \
  'resource.type="workflows.googleapis.com/Workflow" AND jsonPayload.phase="report_failed" AND jsonPayload.raw_status="FATAL"' \
  --project=kalilos-connector-staging --limit=10 --format=json

# Free-text fallback still works for messages/errors
gcloud logging read \
  'resource.type="workflows.googleapis.com/Workflow" AND jsonPayload.error:"invalid values"' \
  --project=kalilos-connector-staging --limit=10 --format=json
```

### Looking up Firestore job records

```python
# Get a specific job by ID
python3 -c "
from google.cloud import firestore
db = firestore.Client(project='kalilos-connector-staging')
doc = db.collection('jobs').document('JOB_ID_HERE').get()
if doc.exists:
    import json
    print(json.dumps(doc.to_dict(), default=str, indent=2))
"
```

### Key log patterns

| What you see | Where to look | Log filter |
|---|---|---|
| Job stuck in "Pending" | Workflow errors | `jsonPayload.phase="workflow_error"` |
| "Report generation failed: FATAL" | Workflow | `jsonPayload.phase="report_failed" AND jsonPayload.raw_status="FATAL"` — Amazon has no data or account lacks access |
| "invalid values" (Ads API columns) | Workflow + create-report | `jsonPayload.error:"invalid values"` — check `ads_report_config.py` |
| Throttling | create-report / workflow | `jsonPayload.error_code="THROTTLED" OR jsonPayload.phase="throttle"` — retry later |
| BQ ingestion failed | Workflow | `jsonPayload.phase="ingest_bigquery" AND severity>=ERROR` — report still delivered to Drive; job stays `status="completed"` but gets `ingest_status="failed"` + `ingest_error` in Firestore |
| Job shows "failed" but no error | Global error handler | `jsonPayload.job_id="<id>"` across functions + workflow; full error also in the Firestore `error_details` field |

### Dashboard error display

The frontend categorizes errors for employees:
- **No data available** (grey info icon): Amazon returned FATAL/CANCELLED — account may lack Brand Registry or no data for the date range. Not retriable.
- **Rate limited** (amber icon): Throttled/QuotaExceeded — shows a **Retry** button.
- **Error** (red icon): Actual failures (invalid config, auth issues, etc.) — shows a **Retry** button and the error detail.

### Retry mechanism

Failed jobs can be retried via the dashboard (Retry button) or API: `POST /jobs/{job_id}/retry`. This creates a new job with the same parameters and launches a fresh workflow execution.

## Further Reference

- **Cursor Rules**: `.cursor/rules/` — six rules covering project context, cloud functions, Pulumi infra, workflow YAML, frontend, and devops.
- **Cursor Skills**: `.cursor/skills/` — deep reference for Amazon SP API, Amazon Ads API, Cloud Workflows, Pulumi GCP, Firestore patterns, and frontend React app conventions.
