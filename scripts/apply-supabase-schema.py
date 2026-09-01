#!/usr/bin/env python3
"""Apply the operational schema (clients, schedules, jobs, and supporting
tables) to Supabase.

Idempotent — every statement in infra/supabase/schema.sql uses
CREATE TABLE/INDEX IF NOT EXISTS and DROP POLICY IF EXISTS + CREATE POLICY,
so re-running this is safe.

Usage:
    SUPABASE_DB_URL=postgresql://... python scripts/apply-supabase-schema.py

Requires: psycopg2-binary
    pip install psycopg2-binary
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "infra" / "supabase" / "schema.sql"


def main() -> None:
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        print("SUPABASE_DB_URL is not set.", file=sys.stderr)
        sys.exit(1)

    import psycopg2  # lazy: only required when this script actually runs

    sql = SCHEMA_PATH.read_text()

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()

    print(f"Applied {SCHEMA_PATH}")


if __name__ == "__main__":
    main()
