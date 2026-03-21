# Kalilos Amazon Reports Connector

Serverless system on GCP that automatically downloads Amazon SP API and Ads API reports for multiple clients and marketplaces on configurable schedules, and stores them in Google Drive.

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
        ├── 1. Authenticate (LWA / Ads API tokens)
        ├── 2. Create Report (Amazon SP API or Ads API)
        ├── 3. Poll Status (exponential backoff)
        └── 4. Download & Upload to Google Drive
```

**Stack:** Cloud Functions (Python 3.12) · Cloud Workflows · Firestore · Cloud Scheduler · Secret Manager · Google Drive API · React + Vite + shadcn/ui on Firebase Hosting · Pulumi (IaC)

## Key Features

- **Multi-client, multi-marketplace** — one schedule fans out across all selected clients and marketplaces
- **Dual API support** — SP API and Ads API handled through the same pipeline
- **Timezone-aware scheduling** — report dates computed in each marketplace's local timezone
- **Configurable timeframes** — yesterday, today, last N days, rolling windows, last calendar week/month
- **Data reconciliation** — automatic re-pull of stale data at T-3 / T-7
- **Google Drive storage** — configurable folder layouts with date subfolders
- **JSON-to-TSV conversion** — SP API JSON reports converted to spreadsheet-friendly TSV
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
│   ├── api/                     # Frontend REST API
│   └── shared/                  # Shared modules
│       ├── config.py            # Env, marketplace timezones
│       ├── schedule_compute.py  # Timezone-aware date computation
│       ├── workflow_launcher.py # Unified workflow launch helpers
│       ├── drive_client.py      # Drive folder hierarchy + upload
│       ├── report_converter.py  # JSON → TSV flattening
│       ├── sp_api_client.py     # SP API HTTP client
│       ├── ads_api_client.py    # Ads API HTTP client
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
| `make seed-firestore` | Seed test data |
| `make logs-fn NAME=x` | Tail function logs |

## Schedule Configuration

Schedules are configured through the React UI and support:

- **Frequencies:** hourly, daily, weekly (with day-of-week), monthly (with day-of-month)
- **Timeframes:** yesterday (default), today, last N days (with optional data delay offset), rolling window, last calendar week, last calendar month
- **Multi-select:** multiple clients and marketplaces per schedule
- **Custom folders:** configurable Drive folder names and subfolder strategies
- **Reconciliation:** automatic re-pull at T-3 / T-7 for data that Amazon revises after initial reporting

## Conventions

- All operations via `Makefile` — never raw `gcloud`, `pulumi`, or `firebase`
- Python 3.12 with type hints on all function signatures
- Cloud Function handler: `def handler(request: flask.Request) -> tuple[dict, int]`
- All backend timestamps in UTC; report dates in marketplace timezone; frontend displays in browser timezone
- Resource naming: `kalilos-{env}-{resource}`
- Secret naming: `kalilos-{env}-{service}-{client}`
