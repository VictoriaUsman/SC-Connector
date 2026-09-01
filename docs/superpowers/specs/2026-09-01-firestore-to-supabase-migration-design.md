# Firestore → Supabase Migration — Design

## Context

The Kalilos Connector's operational state — `clients`, `schedules`, `jobs`,
plus internal coordination collections (`_job_launch_dedupe`,
`_drive_folder_locks`, `slack_thread_anchors`), `events`, `bot_configs`, and
`bot_activity` — all live in Firestore today. Firestore is touched from three
places: `functions/shared/firestore_utils.py` (the repository module 13
Cloud Function files import), `functions/shared/drive_client.py` (its own
`_drive_folder_locks` collection), and `workflows/report_flow.yaml` (three
direct HTTPS calls to the Firestore REST API for job-status writes on the
error/ingest paths, using the workflow's own service-account IAM). The
frontend reads `clients`/`schedules`/`events`/`bot_configs` through the Flask
API, but the live job dashboard (`hooks/use-jobs.ts`) subscribes to
Firestore directly via `onSnapshot`.

Separately, the local-dev-sandbox Phase 1 work (see
`2026-08-28-local-dev-sandbox-phase1-design.md`) already stood up a Supabase
(Postgres) project holding `orders`/`ad_campaign_metrics` as a BigQuery
substitute for `daily_recap`'s metrics queries, plus a `LOCAL_MODE` JSON-file
Firestore shim (`shared/local_firestore.py`) so the rest of the app could run
without real GCP credentials.

The user wants to consolidate on one datastore: move every Firestore
collection to Postgres in that same Supabase project, rather than running
Firestore and Supabase side by side indefinitely. Decisions made during
brainstorming:

- **One Supabase project, one environment.** No staging/prod split for the
  data layer — this app has one user. The existing GCP staging/prod stacks
  for Cloud Functions/Workflows are untouched; both now point at the same
  Supabase project.
- **The Workflow's direct Firestore writes become direct PostgREST calls**,
  authenticated by a service-role key fetched via a Secret Manager connector
  step at the top of the workflow — the closest parallel to today's
  ambient-IAM design, no new Cloud Function.
- **Start fresh — no data migration.** The ~500 existing job records and the
  one real client/schedule in staging Firestore are not carried over.

## Goals

- Every Firestore collection moves to a Postgres table in the existing
  Supabase project: `clients`, `schedules`, `jobs`, `job_launch_dedupe`,
  `drive_folder_locks`, `events`, `bot_configs`, `bot_activity`,
  `slack_thread_anchors`.
- `functions/shared/firestore_utils.py` is rewritten against `psycopg2` as
  `functions/shared/db.py`, **preserving every function's name, signature,
  and return shape** (dicts keyed by `"id"`), so the 12 files that import it
  need only an import-path change, never a logic change (see §2 for the
  list — `local_firestore.py` only mentions `firestore_utils.py` in a
  docstring and is deleted outright in §6, not updated).
- `report_flow.yaml`'s three Firestore REST calls become PostgREST calls.
- The frontend's real-time job dashboard moves from `firebase/firestore`
  `onSnapshot` to Supabase Realtime, via a rewritten `hooks/use-jobs.ts` —
  no other frontend file changes, since everything else already goes through
  the Flask API.
- RLS policies replace `firestore.rules`, and are tighter: only `jobs` gets
  an anon-read policy (matching what's actually used — `clients`/`schedules`
  reads go through the API, not direct Firestore/Supabase access).
- `functions/shared/local_firestore.py` and its test file are deleted — moot
  once local dev and prod share the one real Supabase project.
- `infra/resources/firestore.py`, `firestore.rules`, the `firestore` key in
  `firebase.json`, and the `firebase`/`reactfire` frontend dependencies are
  removed.

## Non-goals

- Migrating existing Firestore data. Starting fresh (user decision).
- Any change to `daily_recap`'s existing `orders`/`ad_campaign_metrics`
  tables from Phase 1 — additive only, untouched by this spec.
- Frontend deployment target (Firebase Hosting vs. anything else) — out of
  scope; staying local per earlier conversation.
- A staging/prod split for the Supabase project.
- Supabase Auth, Storage, or Edge Functions — not used here. Access control
  is RLS (reads) plus the existing API-key check in `functions/api/main.py`
  (writes), exactly mirroring today's split between public Firestore reads
  and API-gated writes.
- Provisioning the Supabase project itself — already exists from Phase 1.

## Design

### 1. Schema — new tables in the existing Supabase project

Nested/free-form fields (`schedule_config`, `timeframe`, `report_params`,
`error_details`, `last_run_job_count`, `ads_profile_ids`, `manual_ads`,
`channels`, `hourly_bot`) map to `jsonb` columns, matching their use as
opaque config blobs today. Everything queried, filtered, or indexed on stays
a plain column. Exact column list is finalized during planning against
`frontend/src/types/index.ts` (already read for this spec) and current
Firestore document usage — the tables and their access patterns below are
what drive the design:

- **`clients`** — `id text PRIMARY KEY`, `name`, `marketplaces text[]`,
  `account_type`, `is_active boolean`, `sp_api_secret_name`,
  `ads_api_secret_name`, `ads_profile_id`, `ads_profile_ids jsonb`,
  `created_at`/`updated_at`. Access: get by id, list (optionally
  `is_active`), case-insensitive id/name fallback resolution (done in
  Python, as today — no special index needed).
- **`schedules`** — `id uuid PRIMARY KEY DEFAULT gen_random_uuid()`,
  `client_ids text[]`, `is_active boolean`, `next_run_at timestamptz`,
  `last_run_at timestamptz`, `last_run_status text`,
  `last_run_job_count jsonb`, `last_drive_folder_id text`,
  `schedule_config jsonb`, `timeframe jsonb`, `report_params jsonb`, plus
  the plain columns (`api_source`, `report_types text[]`, `marketplaces
  text[]`, `frequency`, `folder_name`, `subfolder_strategy`,
  `reconciliation_days int[]`). Index on `(is_active, next_run_at)` mirrors
  today's Firestore composite index and backs `claim_due_schedule`.
  `client_ids` filtering uses `client_ids @> ARRAY[%s]`.
- **`jobs`** — `id uuid PRIMARY KEY DEFAULT gen_random_uuid()`, `client_id`,
  `schedule_id uuid`, `execution_date date`, `status`, `api_source`,
  `report_type`, `marketplace`, `amazon_report_id`, `gdrive_file_id`,
  `gdrive_folder_id`, `gdrive_path`, `error_details jsonb`, `retry_count
  int`, `poll_count int`, `frequency`, `report_date`, `report_end_date`,
  `trigger`, `ingest_status`, `ingest_error`, `started_at timestamptz`,
  `completed_at timestamptz`. Indexes on `(client_id, started_at desc)`,
  `(status, started_at desc)`, `(schedule_id, started_at desc)`, and
  `(schedule_id, execution_date)` (sibling-status aggregation) — the same
  four composite indexes `infra/resources/firestore.py` defines today.
- **`job_launch_dedupe`** — `dedupe_key text PRIMARY KEY`, `metadata jsonb`,
  `created_at timestamptz DEFAULT now()`. Replaces the SHA-256-hashed
  document-id trick (Postgres has no Firestore id-character restriction, so
  the raw key is usable directly as the primary key).
- **`drive_folder_locks`** — `lock_key text PRIMARY KEY`, `folder_id text`,
  `created_at timestamptz DEFAULT now()`.
- **`events`** — `id uuid PRIMARY KEY DEFAULT gen_random_uuid()`, `name`,
  `start_date date`, `end_date date`, `status`, `prior_event_id`,
  `manual_ads jsonb`, `manually_activated boolean`, `activated_at
  timestamptz`, `created_at`/`updated_at`. Index on `status` (backs
  `get_live_event`'s `where status == "live"`); `list_events` orders by
  `start_date`.
- **`bot_configs`** — `client_id text PRIMARY KEY REFERENCES clients(id)`,
  `channels jsonb`, `slack_channel_id`, `slack_channel_name` (deprecated
  fields, kept per `types/index.ts`'s own back-compat note), `base_currency`,
  `client_timezone`, `marketplaces text[]`, `hourly_bot jsonb`,
  `daily_recap_enabled boolean`, `sku_breakdown_enabled boolean`,
  `test_channel_id`, `use_test_channel boolean`, `created_at`/`updated_at`.
- **`bot_activity`** — `id uuid PRIMARY KEY DEFAULT gen_random_uuid()`,
  `payload jsonb` (free-form, matches today's schemaless Firestore writes),
  `timestamp timestamptz DEFAULT now()`.
- **`slack_thread_anchors`** — `id text PRIMARY KEY` (deterministic
  `event_id__channel_id__event_date`, same construction as today),
  `event_id`, `channel_id`, `event_date date`, `parent_ts text`,
  `created_by_client_id text`, `created_at timestamptz DEFAULT now()`.

Every atomic Firestore pattern becomes a single SQL statement:

| Function | Today (Firestore) | Postgres |
|---|---|---|
| `claim_due_schedule` | transactional read-check-write | `UPDATE schedules SET last_run_at=%s, next_run_at=%s WHERE id=%s AND is_active AND next_run_at<=%s RETURNING id` — claimed iff a row comes back |
| `try_claim_job_launch` | `.create()`, catch `AlreadyExists` | `INSERT INTO job_launch_dedupe (dedupe_key, metadata) VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING dedupe_key` |
| drive folder lock claim | `.create()` in `_drive_folder_locks`, catch `AlreadyExists` | `INSERT INTO drive_folder_locks (lock_key) VALUES (%s) ON CONFLICT DO NOTHING RETURNING lock_key`; `_wait_for_folder_id` keeps its existing poll loop, now `SELECT folder_id FROM drive_folder_locks WHERE lock_key=%s` |
| `set_thread_anchor_ts` | `.create()`, "first writer wins" | `INSERT ... ON CONFLICT (id) DO NOTHING RETURNING id`; caller treats no-row-returned as "someone else already anchored this" |

### 2. Backend data-access layer — `shared/db.py`

`functions/shared/firestore_utils.py` → `functions/shared/db.py`. Every
public function (`get_client`, `list_clients`, `upsert_client`,
`delete_client`, `resolve_client`, the `schedules`/`jobs`/`events`/
`bot_configs`/`bot_activity`/`slack_thread_anchors` functions,
`claim_due_schedule`, `try_claim_job_launch`) keeps its exact name,
parameters, and return shape. Internals swap from `firestore.Client()`
collection/document calls to `psycopg2` queries, reusing the
connection-caching + `autocommit=True` pattern already in
`shared/metrics_repository.py` (from Phase 1, including the fix from
`051909f`'s "poisoned connection" bug). `_maybe_update_schedule_run_status`'s
sibling-aggregation logic is unchanged Python — only the two queries it
issues (fetch siblings, patch the schedule) move to SQL.

Files that import `firestore_utils` today and need only their import line
updated (`from shared.firestore_utils import ...` → `from shared.db import
...`), no logic changes: `api/main.py`, `scheduler/main.py`,
`daily_recap/main.py`, `slack_bot/main.py`, `poll_status/main.py`,
`fetch_api/main.py`, `event_report_scheduler/main.py`,
`download_upload/main.py`, `create_report/main.py`,
`shared/workflow_launcher.py`, `shared/currency.py`, `shared/credentials.py`.
(`shared/local_firestore.py` itself is deleted rather than updated — see
§6.)

`drive_client.py`'s own `_get_db()`/`_drive_folder_locks` logic moves to the
same `psycopg2` connection helper (imported from `shared/db.py` rather than
duplicated), using the `INSERT ... ON CONFLICT` claim pattern from the table
above.

### 3. Workflow YAML → Supabase (`report_flow.yaml`)

A new step near the top of the workflow (only the branches that need it —
mirroring today's scoping) calls the Secret Manager connector
(`googleapis.secretmanager.v1.projects.secrets.versions.access`) to fetch a
new secret, `kalilos-{env}-supabase-service-key`, provisioned in
`infra/resources/secrets.py` alongside the app's other secrets. Each of the
three existing Firestore REST PATCH calls
(`https://firestore.googleapis.com/v1/.../jobs/{id}?updateMask...`) becomes:

```yaml
- update_job_status:
    call: http.patch
    args:
      url: '${SUPABASE_URL + "/rest/v1/jobs?id=eq." + args.job_id}'
      headers:
        apikey: '${supabase_key}'
        Authorization: '${"Bearer " + supabase_key}'
        Prefer: "return=minimal"
      body:
        status: "failed"
        error_details: '${...}'
```

No `updateMask.fieldPaths` needed — PostgREST updates only the columns
present in the body. `SUPABASE_URL` is a plain (non-secret) value, injected
the same way `GOOGLE_CLOUD_PROJECT_ID` reaches the workflow today. The
workflow's service account drops `roles/datastore.user` and gains
`roles/secretmanager.secretAccessor` scoped to the new secret.

`tests/test_report_flow_yaml.py` (structural YAML regression tests) gets new
assertions covering the Secret Manager fetch step and the PostgREST call
shape, replacing the assertions that referenced the Firestore REST URL/
`updateMask`.

### 4. Frontend — Supabase client + realtime jobs

Only two files touch Firestore: `lib/firebase.ts` and `hooks/use-jobs.ts`.
Everything else (`use-clients.ts`, `use-schedules.ts`, `use-events.ts`,
`use-bot-configs.ts`) already goes through the Flask API via react-query and
needs no changes.

- `lib/firebase.ts` → `lib/supabase.ts`:
  ```ts
  import { createClient } from "@supabase/supabase-js";
  export const supabase = createClient(
    import.meta.env.VITE_SUPABASE_URL,
    import.meta.env.VITE_SUPABASE_ANON_KEY,
  );
  ```
- `hooks/use-jobs.ts` — `useRealtimeJobs`/`useRunJobs` each become an initial
  `supabase.from("jobs").select("*").match(...)` fetch followed by a
  `supabase.channel(...).on("postgres_changes", { event: "*", schema:
  "public", table: "jobs", filter: ... }, cb)` subscription that re-fetches
  or patches local state on change, mapped into the same `Job[]` shape
  (`docToJob`'s ISO-string handling simplifies — Postgres `timestamptz`
  columns already serialize as ISO strings over PostgREST, no
  `Timestamp`-to-`Date` conversion needed). Every consumer
  (`dashboard.tsx`, `run-jobs-list.tsx`, `on-demand.tsx`) is untouched.
- `package.json` — remove `firebase` and `reactfire` (confirmed unused
  outside the two files above), add `@supabase/supabase-js`.
- `frontend/.env.example` / `.env` — remove `VITE_FIREBASE_API_KEY`,
  `VITE_FIREBASE_AUTH_DOMAIN`, `VITE_FIREBASE_PROJECT_ID`; add
  `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`. `VITE_API_URL`/
  `VITE_API_KEY` (the Flask API) are untouched.

### 5. Security model — RLS policies

`ALTER TABLE ... ENABLE ROW LEVEL SECURITY` on every new table. Exactly one
anon-read policy, on `jobs` (`CREATE POLICY jobs_public_read ON jobs FOR
SELECT TO anon USING (true)`) — needed for the frontend's realtime
subscription. No other table gets an anon policy: `clients`, `schedules`,
`events`, `bot_configs`, `bot_activity`, `slack_thread_anchors`,
`job_launch_dedupe`, and `drive_folder_locks` are readable/writable only via
the service-role key. `psycopg2` connects using Supabase's `SUPABASE_DB_URL`
connection string (same one `metrics_repository.py` already uses), which
authenticates as the `postgres` role — Supabase grants that role
`BYPASSRLS`, so server-side code is unaffected by the policies above by
construction; only the anon/PostgREST path (the frontend's Supabase client)
is subject to RLS. Same trust boundary as today's "only the API can
write" rule, just enforced at the connection-credential level instead of
Firestore security rules. This is strictly tighter than
today: `firestore.rules` currently allows public read on `clients` and
`schedules` too, even though nothing actually reads them that way.

### 6. Retiring the Firestore/Firebase surface

- Delete `functions/shared/local_firestore.py` and `tests/test_local_firestore.py`
  — moot once local dev and prod share the one Supabase project (local dev
  connects to it directly, the same way `metrics_repository.py` already
  does for `orders`/`ad_campaign_metrics`). `functions/shared/local_secrets.py`
  (Slack/Amazon credential bypass) is unrelated and stays.
- Delete `infra/resources/firestore.py`; remove its import and the `db =
  firestore.create(...)` line from `infra/__main__.py` (confirmed nothing
  else in that file references `db`, so this is a clean two-line removal).
- Delete `firestore.rules` and the top-level `firebase.json`'s `firestore`
  key (its `hosting` key, if Firebase Hosting is still in use for the
  frontend, is untouched — out of scope per Non-goals).
- `scripts/mock-api-data.json` / `scripts/mock-api.py` (the frontend's local
  mock API server) are a separate concern from Firestore and are not
  touched by this migration.

### 7. Testing

- Files with a per-file `_mock_firestore` fixture (there is no shared
  `conftest.py` — each defines its own) get that fixture replaced with a
  `psycopg2` connection/cursor mock, following the fake-cursor pattern
  already established in `tests/test_metrics_repository.py`:
  `test_api.py`, `test_daily_recap.py`, `test_event_report_scheduler.py`,
  `test_events_api.py`, `test_firestore_utils.py` (renamed `test_db.py`),
  `test_removed_reports.py`, `test_scheduler.py`, `test_slack_bot.py`,
  `test_workflow_launcher.py` (confirmed via `grep -rl _mock_firestore
  tests/`). `test_drive_client.py` mocks Firestore separately for the
  `_drive_folder_locks` collection and needs the same treatment.
- `tests/test_local_firestore.py` is deleted (module under test is gone).
- New assertions in `tests/test_report_flow_yaml.py` for the Secret Manager
  fetch + PostgREST call shape (§3).
- Frontend: no existing test file covers `hooks/use-jobs.ts` today: confirm
  during planning whether to add coverage for the new realtime hook, or
  leave it as-is (matching today's coverage level).

## Rollout / verification

Single environment, no data migration, so this is a straight cutover rather
than a dual-write period:

1. Apply the schema + RLS policies (§1, §5) to the existing Supabase
   project via a checked-in SQL file, run with `psycopg2`/`psql` against
   `SUPABASE_DB_URL` (extends the pattern `scripts/seed-supabase.py`
   started in Phase 1).
2. Ship the rewritten backend (`shared/db.py`, `drive_client.py`, the 12
   updated import sites) and workflow (§3), deployed to `staging` first via
   the existing `make deploy-infra`/`deploy-frontend` flow.
3. Manually re-create the one real client + schedule that exist in staging
   Firestore today (per the "start fresh" decision) through the app itself.
4. Ship the frontend (§4) with the new `VITE_SUPABASE_*` env vars.
5. Confirm end-to-end: trigger an on-demand job, watch it move through
   statuses live on the Dashboard (proving the realtime subscription),
   confirm a schedule's due-time claim and a duplicate-launch attempt both
   behave per §1's table.
6. Delete the Firestore database (`infra/resources/firestore.py` already
   gone from code — this is deleting the actual GCP resource) and, once
   confirmed nothing else in the GCP project depends on it, the Firebase
   project itself. This last step is a manual GCP/Firebase console action,
   not automated by this migration.

## Follow-up (not this spec)

- Removing the GCP staging/prod split entirely (a bigger, separate decision
  than this migration) — not addressed here; both stacks currently point at
  the same one Supabase project, which is sufficient for "single
  environment" as scoped.
- Any future move of `daily_recap`'s `METRICS_BACKEND=bigquery` default
  itself (i.e., dropping BigQuery too) — out of scope; this spec only adds
  the operational tables alongside the existing `orders`/
  `ad_campaign_metrics` ones from Phase 1.
