# Kalilos Amazon Reports Connector

> **GCP Identity**: This project uses `nivbraz90@gmail.com`. All deployment scripts verify the active gcloud account and abort if wrong. To authenticate: `gcloud auth login nivbraz90@gmail.com`. The Makefile `check-auth` target runs automatically before any deploy.

## Purpose

Serverless connector on GCP that downloads Amazon SP API and Ads API reports for multiple clients and marketplaces on configurable schedules, stores them in Google Drive. An employee-facing React UI allows configuration of clients, schedules, and on-demand report requests.

## Architecture

The system is fully serverless on GCP, organized around a unified Wait+Poll flow that handles both SP API and Ads API reports through the same pipeline.

**Orchestration**: GCP Cloud Workflows executes a single `report_flow.yaml` that accepts an `api_source` parameter (`"sp_api"` or `"ads_api"`). The workflow calls Cloud Functions for each step: authenticate, create report request, poll until ready, download and upload to Google Drive. Polling uses exponential backoff (30s base, 120s max). The workflow also passes `folder_name` and `subfolder_strategy` to the download_upload step for custom Drive folder paths.

**Compute**: Cloud Functions (Python 3.12, 2nd gen) handle each discrete step. Each function receives the `api_source` param and branches internally to call the correct Amazon API. Functions are stateless; all state lives in Firestore.

**Storage & Config**: Firestore stores client configurations, report schedules, and job history. Collections: `clients`, `schedules`, `jobs`, `_drive_folder_locks` (transient coordination docs). The frontend reads these in real-time via Firestore listeners.

**Scheduling**: Cloud Scheduler triggers a scheduler Cloud Function on a cron. The scheduler reads active schedules from Firestore, resolves each schedule's `timeframe` strategy into a date range (default: yesterday), and launches Cloud Workflow executions for each `(client, marketplace)` pair. For the default `yesterday` strategy it also launches reconciliation jobs (T-3, T-7) to re-pull data that may have been updated by Amazon. Multi-day strategies skip reconciliation.

**Three-Clock Timezone Architecture**:
- **Backend Clock (UTC)**: All timestamps in Firestore, all scheduler logic runs on UTC.
- **Marketplace Clock (Local)**: `MARKETPLACE_TIMEZONES` in `functions/shared/config.py` maps each marketplace to its IANA timezone. The scheduler uses this to compute "yesterday" in the marketplace's local time and convert to UTC API request timestamps.
- **User Clock (Display)**: Frontend formats UTC timestamps using the browser's local timezone for display only.

**Secrets**: GCP Secret Manager stores Amazon credentials (LWA refresh tokens, Ads API tokens). Secret naming: `kalilos-{env}-{service}-{client}` (e.g., `kalilos-prod-sp-api-acme`).

**Report Storage**: Google Drive via a GCP service account with native IAM. Default folder layout is date-first: `{root}/{YYYY-MM-DD}/{client}/{marketplace}/{report_type}/`. Custom folder names and subfolder strategies can be configured per schedule. Folder creation is coordinated across concurrent workflow executions using Firestore-based distributed locks (`_drive_folder_locks` collection) to prevent duplicate folders from Drive's eventually-consistent search API.

**Frontend**: React + Vite + shadcn/ui on Firebase Hosting. Uses Firestore real-time listeners for live job status. Calls an API Gateway backed by a Cloud Function for mutations. The Schedules page supports inline editing (Edit dialog) and immediate triggering (Run Now) from the row dropdown menu.

**Infrastructure as Code**: Pulumi (Python) manages all GCP resources. Local file backend (`file://~/.pulumi-local`). Two stacks: `staging` and `prod`, mapping to separate GCP projects (`kalilos-connector-staging` and `kalilos-connector-prod`).

## Data Model

### Schedule (Firestore `schedules` collection)

```python
{
    "client_ids": ["acme", "globex"],       # Multi-select: one or more clients
    "api_source": "sp_api",                  # "sp_api" or "ads_api"
    "report_type": "GET_SALES_AND_TRAFFIC_REPORT",
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
| BR          | America/Sao_Paulo | |
| UK          | Europe/London | |
| DE, FR, IT, ES, NL, SE, PL | Europe/Paris | Central European |
| TR          | Europe/Istanbul | |
| JP          | Asia/Tokyo | |
| AU          | Australia/Sydney | |
| IN          | Asia/Kolkata | |
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
├── scripts/
│   ├── _common.sh                    # Shared helpers, GCP guard, Pulumi backend config
│   ├── deploy-infra.sh               # Pulumi deploy wrapper
│   ├── deploy-frontend.sh            # Firebase deploy wrapper
│   ├── health-check.sh               # Post-deploy verification (12 checks)
│   ├── seed-firestore.sh             # Seed test data
│   └── rotate-secrets.sh             # Secret rotation helper
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
│   ├── api/
│   │   └── main.py                   # Frontend API (CRUD schedules, clients)
│   └── shared/
│       ├── sp_api_client.py          # SP API HTTP client
│       ├── ads_api_client.py         # Ads API HTTP client
│       ├── drive_client.py           # Google Drive upload, Firestore-coordinated folder creation
│       ├── report_converter.py       # JSON→TSV flattening for spreadsheet-friendly output
│       ├── firestore_utils.py        # Common Firestore operations
│       ├── config.py                 # Env, config, marketplace timezones
│       ├── schedule_compute.py       # Timezone-aware dates, date ranges, flexible next_run_at
│       └── workflow_launcher.py      # Unified workflow launch, retry, per-marketplace fan-out
├── workflows/
│   └── report_flow.yaml              # Cloud Workflow: unified report pipeline
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
        ├── hooks/                     # Custom React hooks
        ├── lib/                       # Utilities and Firebase config
        └── types/                     # TypeScript type definitions
```

## Deployment

All operations go through the Makefile. Never run raw `gcloud`, `pulumi`, or `firebase` commands.

Pulumi uses a local file backend (`file://~/.pulumi-local`), configured automatically by `scripts/_common.sh`.

```bash
# Deploy to staging
make env-staging && make preview && make deploy-all && make health

# Deploy to production
make env-prod && make preview && make deploy-all && make health
```

`make deploy-all` runs infrastructure (Pulumi) first, then frontend (Firebase Hosting), in order.

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
- `launch_for_marketplace(parent, now, schedule, client_id, marketplace)` — launches primary + reconciliation jobs for one `(client, marketplace)` pair. Computes `execution_date` (marketplace today) once and passes it through to all jobs. Reads the schedule's `timeframe` config (default: `yesterday`) and calls `compute_date_range()`. Reconciliation only runs for `yesterday` strategy

### `functions/shared/drive_client.py`

Google Drive folder hierarchy and upload:
- **Folder date = execution date** (when the report was created), not the report data date. The `execution_date` is computed once at launch time and threaded through the workflow payload, ensuring all marketplace workflows from the same run target the same folder
- **Default path**: `{root}/{execution_date}/{client}/{marketplace}/{report_type}/`
- **Custom folder**: `{root}/{folder_name}/{optional execution_date}/`
- `build_folder_path()` returns `(folder_id, human_readable_path)`. Runs `_assert_no_duplicates()` on every folder segment as a runtime dedup guard
- Filename uses the **report data date** (not execution date): `{report_type}_{date}_{client}_{marketplace}.{ext}` for single-day, `{report_type}_{start}_to_{end}_{client}_{marketplace}.{ext}` for ranges
- **Concurrent folder creation** (4-layer defense):
  1. `find_or_create_folder()` — Firestore atomic `document.create()` as distributed lock. Winner creates, losers poll
  2. Stale lock detection — when a lock references a deleted Drive folder, it's cleared and the caller falls through to the dedup path (never recursively retries the lock, which caused a TOCTOU race)
  3. `_create_folder_with_dedup()` — fallback that sleeps, re-checks Drive, creates, then post-creation deduplicates by keeping the oldest folder and deleting extras
  4. `_assert_no_duplicates()` — runtime guard after every folder segment in `build_folder_path()` that detects and cleans up duplicates before the file is uploaded

### `functions/shared/report_converter.py`

JSON-to-TSV conversion for spreadsheet-friendly output:
- `should_convert(report_type)` — returns True for SP API reports that return JSON (Sales & Traffic, Brand Analytics, Ledger)
- `json_report_to_tsv(raw_bytes, report_type)` — parses JSON, locates the main data array(s), recursively flattens nested objects (e.g. `salesByDate.orderedProductSales.amount`), outputs TSV with header row
- Called by `download_upload` before uploading to Drive; the file is saved as `.tsv` for Google Sheets compatibility

## Common Agent Tasks

### Add a new Cloud Function

1. Create a new directory under `functions/` (e.g., `functions/my_function/main.py`).
2. Implement `def handler(request: flask.Request) -> tuple[dict, int]`.
3. Add the function resource to `infra/resources/functions.py`, following the existing pattern.
4. Add IAM bindings in `infra/resources/iam.py` if the function needs to call other services.
5. Deploy: `make deploy-infra`.

### Add a new report type

If the report type follows the standard SP API or Ads API pattern, add it to the report type configuration in Firestore (via the UI or seed script). No code changes needed — the workflow handles arbitrary report types parametrically.

For non-standard report types, add a handler branch in `functions/create_report/main.py` and `functions/download_upload/main.py`.

**If the report returns JSON** (not TSV/XML/CSV):
1. Add the report type to `_SP_API_JSON_REPORTS` in `functions/shared/drive_client.py` (for correct MIME type when NOT converting).
2. Add the report type and its main data array key(s) to `_JSON_REPORT_ARRAY_KEYS` in `functions/shared/report_converter.py` (for JSON→TSV flattening).
3. Add the report type to the frontend: `SP_REPORT_TYPES` in `frontend/src/types/index.ts`.

### Trigger a schedule immediately (Run Now)

`POST /schedules/<schedule_id>/trigger` fans out workflow executions for all (client, marketplace) pairs, identical to what the cron scheduler does. Reconciliation re-pulls are only included when the schedule's timeframe strategy is `yesterday` (the default). Multi-day strategies (`last_n_days`, `last_calendar_week`, etc.) skip reconciliation since they already cover wider windows. The frontend Schedules page exposes this via the "Run Now" dropdown action.

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

Use the frontend Clients page, or seed via `make seed-firestore`. The client config includes: name, marketplace IDs, SP API credentials reference, Ads API credentials reference, Drive folder ID.

### Deploy to staging

```bash
make env-staging && make preview && make deploy-all && make health
```

### Deploy to production

```bash
make env-prod && make preview && make deploy-all && make health
```
Production requires an interactive confirmation prompt.

## Further Reference

- **Cursor Rules**: `.cursor/rules/` — six rules covering project context, cloud functions, Pulumi infra, workflow YAML, frontend, and devops.
- **Cursor Skills**: `.cursor/skills/` — deep reference for Amazon SP API, Amazon Ads API, Cloud Workflows, Pulumi GCP, and Firestore patterns.
