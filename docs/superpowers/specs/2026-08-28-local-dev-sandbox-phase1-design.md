# Local Dev Sandbox — Phase 1: `daily_recap` with real Slack + Supabase

## Context

The Kalilos Connector is a serverless GCP system (Cloud Functions, Firestore,
BigQuery, Secret Manager). Local development currently means either deploying
to `staging`, or running a single function locally (`make local-fn`) against
real GCP-backed Firestore/BigQuery/Secret Manager — all of which require GCP
credentials this environment doesn't have configured.

The user wants to run parts of this system fully locally, using resources
they already have: a real Slack workspace/bot token, a real Google Drive
account, and a Supabase (Postgres) project. A real Amazon seller account is
also available, but the Amazon report pipeline (`create_report` →
`poll_status` → `download_upload`) is explicitly **out of scope for this
phase** — see Follow-up phases below.

This phase makes `functions/daily_recap` runnable locally end-to-end: it
reads bot configs from a local JSON-backed Firestore substitute, queries
Supabase (standing in for BigQuery) for a day's metrics, and posts a real
message to a real Slack channel using a locally-supplied bot token. No GCP
credentials are required to run it.

`functions/slack_bot` (the hourly bot) is intentionally excluded — it has
live-event gating, multi-marketplace combining, prior-year YoY, and SKU
breakdown reconciliation that aren't needed to prove out this sandbox and
would roughly double the design surface. It's Phase 3 (see below).

## Goals

- Run `functions/daily_recap`'s `handler()` locally via `functions-framework`,
  producing a real Slack post in a real (test) channel, with zero calls to
  GCP (no Firestore, no BigQuery, no Secret Manager).
- Keep production behavior for `daily_recap`, `credentials.py`, and
  `slack_client.py` byte-for-byte unchanged when `LOCAL_MODE` is unset —
  every new code path is additive and opt-in.
- Reuse `scripts/mock-api-data.json` (already built for the frontend mock
  API) as the backing store for the local Firestore shim, so editing a
  client/bot-config in the running frontend is immediately visible to a local
  `daily_recap` run.

## Non-goals (this phase)

- The Amazon SP-API/Ads API report pipeline (`create_report`, `poll_status`,
  `download_upload`) — Phase 2.
- Google Drive integration — not used by `daily_recap` at all; not touched
  in this phase.
- `functions/slack_bot` (hourly bot) — Phase 3.
- Any change to the real BigQuery schema or production Firestore data.

## Design

### 1. Local secrets bypass — `shared/local_secrets.py`

New module:

```python
def is_local_mode() -> bool:
    return os.environ.get("LOCAL_MODE", "").lower() == "true"

def resolve_secret(secret_name: str) -> str:
    """Return a secret's raw payload string.

    In LOCAL_MODE, reads scripts/local-secrets.json (gitignored) keyed by
    secret_name, raising a clear error naming the missing key. Otherwise
    calls Secret Manager exactly as today.
    """
```

`scripts/local-secrets.json` (gitignored, created by the user or via
`make local-secret-set NAME=x VALUE=y`) is a flat `{secret_name: payload}`
map using the exact same secret names Secret Manager uses today, e.g.:

```json
{
  "kalilos-staging-slack-bot-token": "xoxb-...",
  "kalilos-staging-sp-api-app-credentials": "{\"client_id\":\"...\",\"client_secret\":\"...\"}"
}
```

Call sites changed:
- `shared/slack_client.py::_get_slack_token()` — replace the
  `_get_sm().access_secret_version(...)` call with
  `local_secrets.resolve_secret(secret_name)` when `is_local_mode()`, else
  unchanged.
- `shared/credentials.py::_read_secret()` — same swap (note: this function
  additionally does `json.loads()` on the payload; that stays unchanged,
  only the "get the raw string" step swaps).

New Makefile target:
```
local-secret-set:  ## Set a local secret. Usage: make local-secret-set NAME=x VALUE=y
	@./scripts/local-secret-manager.sh set $(NAME) $(VALUE)
```
`scripts/local-secret-manager.sh` reads/writes `scripts/local-secrets.json`
(create the file with `{}` if absent).

### 2. Supabase metrics backend — `shared/metrics_repository.py`

New module, one public function:

```python
def get_account_totals(
    client_id: str, marketplaces: list[str], report_date: str, client_tz: ZoneInfo,
) -> dict:  # {"spend": float, "ppc_sales": float, "total_sales": float}
```

Branches on `METRICS_BACKEND` env var (default `"bigquery"`):
- `"bigquery"` — today's `_query_orders_total`/`_query_ads_total`/
  `_day_bounds_utc` logic from `daily_recap/main.py`, relocated here
  unchanged (same SQL, same BigQuery client caching pattern).
- `"supabase"` — new `psycopg2`-based queries (see schema below) against
  `SUPABASE_DB_URL`, with a cached module-level connection mirroring
  `_get_bq()`'s caching. `import psycopg2` is done lazily inside this branch
  (not at module top-level), so a production deploy — which always takes the
  `"bigquery"` branch — never pays an import cost or hard-requires the
  package at cold start even though it's listed in requirements.txt.

`daily_recap/main.py::_query_account_totals` becomes a thin wrapper:
```python
def _query_account_totals(client_id, marketplaces, report_date, client_tz) -> AccountTotals:
    totals = metrics_repository.get_account_totals(client_id, marketplaces, report_date, client_tz)
    return AccountTotals(**totals)
```

#### Schema (Postgres, via `scripts/seed-supabase.py`)

```sql
CREATE TABLE IF NOT EXISTS orders (
    client_id text NOT NULL,
    marketplace text NOT NULL,
    purchase_date timestamptz NOT NULL,
    item_price numeric NOT NULL,
    order_status text NOT NULL DEFAULT 'Shipped'
);

CREATE TABLE IF NOT EXISTS ad_campaign_metrics (
    client_id text NOT NULL,
    marketplace text NOT NULL,
    date date NOT NULL,
    campaign_id text NOT NULL,
    cost numeric NOT NULL,
    sales numeric NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now()
);
```

Only the columns `daily_recap` actually reads — not a full BigQuery schema
replica (no `ads_report_config` per-ad-product split; `ad_campaign_metrics`
collapses sp/sb/sd campaigns into one table since the recap only needs
account-level totals).

`scripts/seed-supabase.py`:
- Creates both tables (`CREATE TABLE IF NOT EXISTS`, idempotent).
- Inserts one full day of demo rows for `client_id="test-client"`,
  `marketplace="US"` (a handful of orders summing to a readable total; two or
  three ad_campaign_metrics rows with distinct `campaign_id`s summing cost
  and sales) dated "yesterday" relative to run time, so a `daily_recap` run
  immediately has a non-zero recap to post.
- Reads `SUPABASE_DB_URL` from the environment; refuses to run without it
  rather than silently defaulting anywhere.

### 3. Local Firestore shim — `shared/local_firestore.py`

A JSON-file-backed fake implementing the slice of the `firestore.Client`
surface `firestore_utils.py` uses:

```python
class LocalFirestoreClient:
    def collection(self, name: str) -> LocalCollectionRef: ...

class LocalCollectionRef:
    def document(self, doc_id: str | None = None) -> LocalDocRef: ...  # auto id when None
    def stream(self) -> list[LocalDocSnapshot]: ...

class LocalDocRef:
    def get(self) -> LocalDocSnapshot: ...
    def set(self, data: dict, merge: bool = False) -> None: ...

class LocalDocSnapshot:
    id: str
    exists: bool
    def to_dict(self) -> dict: ...
```

Backed by `scripts/mock-api-data.json` — read fully into memory on first
use per process, written back to disk after every `set()`. Same file the
frontend's `scripts/mock-api.py` already reads/writes, so a client or
bot-config edited via the running frontend is immediately visible to a local
`daily_recap` run. `firestore_utils.get_db()`:

```python
def get_db() -> firestore.Client:
    global _db
    if _db is None:
        _db = local_firestore.LocalFirestoreClient(DATA_FILE) if is_local_mode() else firestore.Client()
    return _db
```

`log_bot_activity`'s writes (`collection("bot_activity").document().set(...)`)
land in a new `bot_activity` key in the same JSON file — inspectable after a
run to confirm what was "sent".

### 4. Wiring — `.env.local`, `run-local.sh`, Makefile

New gitignored `.env.local`, plus a checked-in `.env.local.example` template
(same pattern as the existing `.env.example`) so the values-needed list is
discoverable without reading this spec:
```
LOCAL_MODE=true
METRICS_BACKEND=supabase
SUPABASE_DB_URL=postgresql://...
GCP_PROJECT=local
ENVIRONMENT=staging
BQ_DATASET=unused
```

`scripts/run-local.sh` gets one addition: after sourcing `.env.staging` (as
today), also source `.env.local` if present, so local values win. This keeps
the script generic for Phase 2/3 reuse.

New Makefile target:
```
local-daily-recap: ## Run daily recap locally against Supabase + real Slack
	@$(MAKE) local-fn NAME=daily_recap PORT=8082
```

`.gitignore` additions: `.env.local`, `scripts/local-secrets.json`,
`scripts/mock-api-data.json` (holds live client/bot-config data including
whatever Slack channel IDs get typed into the dev UI — not secret, but not
meant to be committed either).

Trigger: `curl -X POST http://localhost:8082 -d '{}'` (same pattern as
today's `local-fn` functions).

### 5. Testing

- `tests/test_metrics_repository.py` — new file, Supabase path tested by
  mocking the DB-API connection (same style as `test_daily_recap.py`'s
  existing `_FakeBQ` for the BigQuery path): verifies the SQL issued and
  parameters bound, without a real Supabase connection. BigQuery path
  covered by existing `test_daily_recap.py` tests (unchanged).
- `tests/test_local_firestore.py` — new file: get/set/merge/stream/auto-id
  semantics, round-tripping through a temp JSON file.
- No changes needed to `test_daily_recap.py`'s existing tests — they patch
  `daily_recap.main._query_account_totals` directly or its callees, which
  keep the same external contract.

## Rollout / verification

1. `pip install psycopg2-binary` added to `functions/daily_recap/requirements.txt`
   (dev-only concern — production `daily_recap` never imports
   `metrics_repository`'s Supabase branch since `METRICS_BACKEND` defaults to
   `bigquery`).
2. `python scripts/seed-supabase.py` — creates schema + seeds demo data.
3. `make local-secret-set NAME=kalilos-staging-slack-bot-token VALUE=xoxb-...`
4. `cp .env.local.example .env.local` (a checked-in example file) and fill in
   `SUPABASE_DB_URL`.
5. `make local-daily-recap` in one terminal, `curl -X POST
   http://localhost:8082 -d '{}'` in another.
6. Confirm: a real message lands in the configured Slack test channel;
   `scripts/mock-api-data.json`'s `bot_activity` key shows a `"status": "sent"`
   entry.

## Follow-up phases (not this spec)

- **Phase 2 — real Amazon pipeline**: `create_report` → `poll_status` →
  `download_upload` against the real SP-API/Ads API (credentials via the
  existing manual-connect flow, stored in `scripts/local-secrets.json`),
  uploading to real Google Drive, ingesting into Supabase instead of
  BigQuery (extends the `orders`/`ad_campaign_metrics` tables with a real
  ingestion path instead of `seed-supabase.py`'s canned rows).
- **Phase 3 — `functions/slack_bot` (hourly) full parity**: live-event
  gating (a `live` event doc in the local Firestore shim), multi-marketplace
  combining, prior-year YoY, SKU breakdown — extends
  `metrics_repository.py` with the additional query shapes `slack_bot` needs.
