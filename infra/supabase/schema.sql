-- =====================================================================
-- Kalilos Connector — operational schema (Firestore -> Supabase migration)
--
-- Applied via scripts/apply-supabase-schema.py. Idempotent: every
-- statement is safe to re-run (CREATE TABLE/INDEX IF NOT EXISTS,
-- DROP POLICY IF EXISTS + CREATE POLICY).
--
-- Note: CREATE TABLE IF NOT EXISTS does not detect drift — re-running this
-- against an existing table with a different shape silently does nothing.
-- Future column additions need explicit ALTER TABLE ... ADD COLUMN IF NOT
-- EXISTS statements, not edits to the CREATE TABLE block below.
--
-- Does NOT touch `orders`/`ad_campaign_metrics` — those are created by
-- scripts/seed-supabase.py (Phase 1, daily_recap's BigQuery substitute)
-- and are untouched by this migration.
--
-- See docs/superpowers/specs/2026-09-01-firestore-to-supabase-migration-design.md
-- section 1 for the design this implements.
-- =====================================================================

CREATE TABLE IF NOT EXISTS clients (
    id text PRIMARY KEY,
    name text NOT NULL,
    marketplaces text[] NOT NULL DEFAULT '{}',
    account_type text NOT NULL DEFAULT 'seller',
    is_active boolean NOT NULL DEFAULT true,
    sp_api_secret_name text,
    ads_api_secret_name text,
    ads_profile_id text,
    ads_profile_ids jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS schedules (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text,
    client_ids text[] NOT NULL DEFAULT '{}',
    api_source text NOT NULL,
    report_types text[] NOT NULL DEFAULT '{}',
    marketplaces text[] NOT NULL DEFAULT '{}',
    frequency text NOT NULL,
    schedule_config jsonb NOT NULL DEFAULT '{}'::jsonb,
    timeframe jsonb,
    folder_name text,
    subfolder_strategy text NOT NULL DEFAULT 'date',
    reconciliation_days int[] NOT NULL DEFAULT '{}',
    report_params jsonb NOT NULL DEFAULT '{}'::jsonb,
    is_active boolean NOT NULL DEFAULT true,
    last_run_at timestamptz,
    last_run_status text,
    last_run_job_count jsonb,
    last_drive_folder_id text,
    next_run_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules (is_active, next_run_at);
CREATE INDEX IF NOT EXISTS idx_schedules_client_ids ON schedules USING gin (client_ids);

-- client_id and schedule_id are deliberately not FKs (unlike bot_configs
-- and events below): on-demand jobs have no schedule, and this avoids
-- blocking delete_schedule/delete_client on historical job rows.
CREATE TABLE IF NOT EXISTS jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id text NOT NULL,
    schedule_id uuid,
    execution_date date,
    status text NOT NULL DEFAULT 'pending',
    api_source text,
    report_type text,
    marketplace text,
    amazon_report_id text,
    gdrive_file_id text,
    gdrive_folder_id text,
    gdrive_path text,
    error_details jsonb,
    retry_count int NOT NULL DEFAULT 0,
    poll_count int NOT NULL DEFAULT 0,
    frequency text,
    report_date text,
    report_end_date text,
    trigger text,
    ingest_status text,
    ingest_error text,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE INDEX IF NOT EXISTS idx_jobs_client_started ON jobs (client_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status_started ON jobs (status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_schedule_started ON jobs (schedule_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_schedule_execution ON jobs (schedule_id, execution_date);
CREATE INDEX IF NOT EXISTS idx_jobs_started ON jobs (started_at DESC);

CREATE TABLE IF NOT EXISTS job_launch_dedupe (
    dedupe_key text PRIMARY KEY,
    metadata jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS drive_folder_locks (
    lock_key text PRIMARY KEY,
    folder_id text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL,
    status text NOT NULL DEFAULT 'upcoming',
    prior_event_id uuid REFERENCES events(id) ON DELETE SET NULL,
    manual_ads jsonb,
    manually_activated boolean NOT NULL DEFAULT false,
    activated_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz
);

CREATE INDEX IF NOT EXISTS idx_events_status ON events (status);

CREATE TABLE IF NOT EXISTS bot_configs (
    client_id text PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
    channels jsonb,
    slack_channel_id text,
    slack_channel_name text,
    base_currency text,
    client_timezone text,
    marketplaces text[] NOT NULL DEFAULT '{}',
    hourly_bot jsonb,
    daily_recap_enabled boolean NOT NULL DEFAULT false,
    sku_breakdown_enabled boolean NOT NULL DEFAULT false,
    test_channel_id text,
    use_test_channel boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bot_activity (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    "timestamp" timestamptz NOT NULL DEFAULT now() -- reserved word: always quote, e.g. SELECT "timestamp" FROM bot_activity
);

CREATE TABLE IF NOT EXISTS slack_thread_anchors (
    id text PRIMARY KEY,
    event_id text NOT NULL,
    channel_id text NOT NULL,
    event_date date NOT NULL,
    parent_ts text NOT NULL,
    created_by_client_id text,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- Row Level Security — only `jobs` is readable by the anon/PostgREST
-- role (the frontend's realtime dashboard). Every other table is
-- service-role-only: server-side code connects via psycopg2 as the
-- `postgres` role, which has BYPASSRLS, so these policies never apply
-- to the backend — only to the frontend's anon Supabase client.
-- ---------------------------------------------------------------------

ALTER TABLE clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE schedules ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_launch_dedupe ENABLE ROW LEVEL SECURITY;
ALTER TABLE drive_folder_locks ENABLE ROW LEVEL SECURITY;
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_configs ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_activity ENABLE ROW LEVEL SECURITY;
ALTER TABLE slack_thread_anchors ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS jobs_public_read ON jobs;
CREATE POLICY jobs_public_read ON jobs FOR SELECT TO anon USING (true);

-- Supabase Realtime only emits postgres_changes events for tables in this
-- publication — required for the frontend's live job-status subscription
-- (design spec §4). Guarded because plain ALTER PUBLICATION ... ADD TABLE
-- errors if jobs is already a member (not idempotent on its own).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_publication_tables
        WHERE pubname = 'supabase_realtime' AND schemaname = 'public' AND tablename = 'jobs'
    ) THEN
        ALTER PUBLICATION supabase_realtime ADD TABLE jobs;
    END IF;
END $$;
