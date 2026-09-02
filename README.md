# Kalilos Amazon Reports Connector

Serverless system on GCP that automatically downloads Amazon SP API and Ads API reports, plus synchronous Amazon API data pulls (first: SP-API Replenishment / Subscribe & Save), for multiple clients and marketplaces on configurable schedules. Results are stored in Google Drive and selected BigQuery tables.

## Architecture

```
Cloud Scheduler (cron)
        │
        ▼
  Scheduler Function ──── Firestore (schedules, jobs)
        │
        ├── fan-out per (client, marketplace)
        │
        ▼
  Cloud Workflow (report_flow.yaml)
        │
        ├── mode=report
        │   ├── 1. Authenticate (LWA / Ads API tokens)
        │   ├── 2. Create Report (Amazon SP API or Ads API)
        │   ├── 3. Poll Status (exponential backoff)
        │   └── 4. Download & Upload to Google Drive
        │
        └── mode=api_call
            └── fetch_api (synchronous REST API -> TSV -> Drive -> BigQuery)
```

**Stack:** Cloud Functions (Python 3.12) · Cloud Workflows · Firestore · Cloud Scheduler · Secret Manager · Google Drive API · React + Vite + shadcn/ui on Firebase Hosting · Pulumi (IaC)

## Key Features

- **Multi-client, multi-marketplace** — one schedule fans out across all selected clients and marketplaces
- **Dual API support** — SP API and Ads API reports handled through the same pipeline
- **Synchronous API operations** — non-report endpoints such as SP-API Replenishment / Subscribe & Save route through `fetch_api`
- **Timezone-aware scheduling** — report dates computed in each marketplace's local timezone
- **Configurable timeframes** — yesterday, today, last N days, rolling windows, last calendar week/month
- **Data reconciliation** — automatic re-pull of stale data at T-3 / T-7
- **Google Drive storage** — configurable folder layouts with date subfolders
- **JSON-to-TSV conversion** — SP API JSON reports and synchronous API rows converted to spreadsheet-friendly TSV
- **BigQuery ingestion** — selected outputs are loaded into managed BigQuery tables after Drive delivery
- **Concurrent folder safety** — Firestore-based distributed locks prevent duplicate Drive folders
- **Real-time UI** — React dashboard with Firestore listeners for live job status

## Environments

| Environment | GCP Project | Pulumi Stack |
|---|---|---|
| Staging | `kalilos-connector-staging` | `staging` |
| Production | `kalilos-connector-prod` | `prod` |

## Quick Start

### Prerequisites

- Google Cloud SDK (`gcloud`) authenticated as the project owner
- Python 3.12+
- Node.js 18+
- Pulumi CLI

### Setup

```bash
# Clone and install
cp .env.example .env.staging    # fill in values
cd frontend && npm install && cd ..
pip install -r infra/requirements.txt

# Initialize Pulumi stack (one-time)
make init-staging
```

### Deploy

```bash
# Staging
make env-staging && make preview && make deploy-all && make health

# Production
make env-prod && make preview && make deploy-all && make health
```

### Local Development

```bash
make local-frontend              # Start React dev server
make local-fn NAME=create_report # Run a Cloud Function locally
make test                        # Run all tests
```

## Project Structure

```
├── AGENTS.md                    # AI agent briefing (architecture + conventions)
├── Makefile                     # All operations entry point
├── .env.example                 # Environment template
├── functions/
│   ├── auth/                    # Amazon token refresh
│   ├── scheduler/               # Schedule fan-out orchestration
│   ├── create_report/           # Submit report request to Amazon
│   ├── poll_status/             # Poll report generation status
│   ├── download_upload/         # Download from Amazon, upload to Drive
│   ├── fetch_api/               # Synchronous API pulls (Replenishment / S&S)
│   ├── api/                     # Frontend REST API
│   └── shared/                  # Shared modules
│       ├── config.py            # Env, marketplace timezones
│       ├── schedule_compute.py  # Timezone-aware date computation
│       ├── workflow_launcher.py # Unified workflow launch helpers
│       ├── drive_client.py      # Drive folder hierarchy + upload
│       ├── report_converter.py  # JSON → TSV flattening
│       ├── sp_api_client.py     # SP API Reports client
│       ├── ads_api_client.py    # Ads API Reports client
│       ├── sp_api_rest.py       # Generic SP-API REST client for non-report endpoints
│       ├── ads_api_rest.py      # Generic Ads API REST client for non-report endpoints
│       ├── api_operations.py    # Registry for synchronous API operations
│       ├── replenishment_client.py # Subscribe & Save / Replenishment client
│       └── firestore_utils.py   # Firestore CRUD
├── workflows/
│   └── report_flow.yaml         # Cloud Workflow definition
├── frontend/                    # React + Vite + shadcn/ui
├── infra/                       # Pulumi IaC (Python)
├── scripts/                     # Deployment + operations scripts
└── tests/                       # pytest test suite
```

## Makefile Commands

Run `make help` to see all available targets. Key commands:

| Command | Description |
|---|---|
| `make env-staging` | Switch to staging environment |
| `make env-prod` | Switch to production (requires confirmation) |
| `make preview` | Preview infrastructure changes (dry run) |
| `make deploy-all` | Deploy infrastructure + frontend |
| `make health` | Run post-deploy health checks |
| `make test` | Run all tests |
| `make seed-test-client` | Seed test data |
| `make logs-fn NAME=x` | Tail function logs |

## Schedule Configuration

Schedules are configured through the React UI and support:

- **Frequencies:** hourly, daily, weekly (with day-of-week), monthly (with day-of-month)
- **Timeframes:** yesterday (default), today, last N days (with optional data delay offset), rolling window, last calendar week, last calendar month
- **Multi-select:** multiple clients and marketplaces per schedule
- **Custom folders:** configurable Drive folder names and subfolder strategies
- **Reconciliation:** automatic re-pull at T-3 / T-7 for data that Amazon revises after initial reporting

Some selectable entries are **synchronous API operations** rather than Amazon report types. These use the same schedule/job UI but launch `mode="api_call"` and route to `functions/fetch_api` instead of create-report/poll/download. Current operation ids:

| Operation | Source | Description |
|---|---|---|
| `SNS_OFFER_METRICS` | SP-API Replenishment | Subscribe & Save offer/ASIN metrics |
| `SNS_SP_METRICS` | SP-API Replenishment | Account-level Subscribe & Save metrics |
| `SNS_OFFERS` | SP-API Replenishment | Subscribe & Save offer enrollment/config |

Legacy `GET_FBA_SNS_PERFORMANCE_DATA` and `GET_FBA_SNS_FORECAST_DATA` were removed by Amazon and should not be used for new schedules.

## Conventions

- All operations via `Makefile` — never raw `gcloud`, `pulumi`, or `firebase`
- Python 3.12 with type hints on all function signatures
- Cloud Function handler: `def handler(request: flask.Request) -> tuple[dict, int]`
- All backend timestamps in UTC; report dates in marketplace timezone; frontend displays in browser timezone
- Resource naming: `kalilos-{env}-{resource}`
- Secret naming: `kalilos-{env}-{service}-{client}`
