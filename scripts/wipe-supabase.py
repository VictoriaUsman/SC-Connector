#!/usr/bin/env python3
"""Delete all rows from specified Supabase tables.

Usage:
    SUPABASE_DB_URL=postgresql://... python scripts/wipe-supabase.py [--tables schedules jobs drive_folder_locks]
    SUPABASE_DB_URL=postgresql://... python scripts/wipe-supabase.py --all   # wipe schedules, jobs, drive_folder_locks, and clients

Requires: psycopg2-binary
    pip install psycopg2-binary
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg2
from urllib.parse import urlparse

# Every table this script is allowed to target — validated before any SQL is
# built, so a typo'd or malicious --tables value can never reach a query.
ALLOWED_TABLES = {
    "clients", "schedules", "jobs", "job_launch_dedupe", "drive_folder_locks",
    "drive_file_index", "oauth_states", "app_config", "events", "bot_configs",
    "bot_activity", "slack_thread_anchors",
}

DEFAULT_TABLES = ["schedules", "jobs", "drive_folder_locks"]
# clients cascades ON DELETE to bot_configs (bot_configs.client_id
# REFERENCES clients(id) ON DELETE CASCADE in schema.sql) — list it
# explicitly so it's named in the confirmation prompt and counted in the
# output, instead of vanishing silently as a side effect of wiping clients.
ALL_TABLES = DEFAULT_TABLES + ["bot_configs", "clients"]


def delete_table(conn, name: str) -> int:
    """Delete all rows in a table. Returns count of deleted rows."""
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {name}")  # nosec: name is allowlist-validated in main()
        deleted = cur.rowcount
    conn.commit()
    return deleted


def _redacted_db_target(url: str) -> str:
    """Host + database name only, no credentials — safe to print before a
    destructive confirmation prompt."""
    parsed = urlparse(url)
    return f"{parsed.hostname}{parsed.path}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Wipe Supabase tables")
    parser.add_argument(
        "--tables",
        nargs="*",
        default=None,
        help=f"Tables to wipe (default: {DEFAULT_TABLES})",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Wipe all tables including clients",
    )
    parser.add_argument("--yes", action="store_true", help="Skip confirmation")
    args = parser.parse_args()

    tables = ALL_TABLES if args.all else (args.tables or DEFAULT_TABLES)

    unknown = [t for t in tables if t not in ALLOWED_TABLES]
    if unknown:
        print(f"Error: unknown table(s): {unknown}", file=sys.stderr)
        print(f"Allowed tables: {sorted(ALLOWED_TABLES)}", file=sys.stderr)
        sys.exit(1)

    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        print("SUPABASE_DB_URL is not set.", file=sys.stderr)
        sys.exit(1)

    db_target = _redacted_db_target(db_url)

    print(f"Target: {db_target}")
    print(f"Tables: {', '.join(tables)}")
    if args.all:
        print("(--all includes bot_configs — clients cascades to it via a foreign key)")
    print()

    if not args.yes:
        answer = input(f"Delete ALL rows in these tables on {db_target}? [y/N] ")
        if answer.lower() != "y":
            print("Aborted.")
            sys.exit(0)

    conn = psycopg2.connect(db_url)
    try:
        for name in tables:
            count = delete_table(conn, name)
            print(f"  {name}: {count} row{'s' if count != 1 else ''} deleted")
    finally:
        conn.close()

    print("\nDone.")


if __name__ == "__main__":
    main()
