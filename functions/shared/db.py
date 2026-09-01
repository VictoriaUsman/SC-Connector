"""Postgres (Supabase) operations for clients, schedules, jobs, and the
other operational tables — replaces functions/shared/firestore_utils.py as
part of the Firestore-to-Supabase migration (see
docs/superpowers/specs/2026-09-01-firestore-to-supabase-migration-design.md).

Every public function keeps its original name, parameters, and return shape
(dicts keyed by "id", column values type-matched to what Firestore returned
— see the module-level notes below) so the files that import this module
(a later, separate plan swaps their import from firestore_utils to here)
need only an import-path change, never a logic change.

Connection caching + autocommit=True follow the pattern already established
in shared/metrics_repository.py (including the fix from commit 051909f:
autocommit is set immediately after connect() so a query error can never
poison the cached connection for the rest of the process).
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import date, datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_conn = None

# Columns that are `jsonb` in infra/supabase/schema.sql and therefore need
# explicit psycopg2.extras.Json(...) wrapping on write. Every other
# list/dict-valued column in this codebase is a real Postgres array
# (text[]/int[]) and psycopg2 adapts a Python list to those natively —
# wrapping those in Json() would be wrong (writes a JSON string into an
# array column and fails).
_JSONB_COLUMNS: dict[str, set[str]] = {
    "clients": {"ads_profile_ids"},
    "schedules": {"schedule_config", "timeframe", "report_params", "last_run_job_count"},
    "jobs": {"error_details"},
    "events": {"manual_ads"},
    "bot_configs": {"channels", "hourly_bot"},
    "bot_activity": {"payload"},
    "job_launch_dedupe": {"metadata"},
}


def _get_connection():
    global _conn
    if _conn is None:
        import psycopg2  # lazy: only required when this module is actually used

        _conn = psycopg2.connect(os.environ["SUPABASE_DB_URL"])
        _conn.autocommit = True
    return _conn


def _row_to_dict(cur, row) -> dict[str, Any]:
    """Column-name-keyed dict from a cursor row, with two type corrections
    so callers see exactly what firestore_utils.py always returned:
    - uuid.UUID -> str (Firestore document ids were always plain strings)
    - plain date -> "YYYY-MM-DD" str (execution_date/start_date/end_date
      were always written and read as ISO strings, never native dates —
      confirmed against workflow_launcher.py and api/main.py's event
      endpoints). datetime is a subclass of date, so this must NOT also
      catch timestamptz columns (created_at, started_at, etc.), which were
      always native datetimes under Firestore and stay that way here.
    """
    columns = [d[0] for d in cur.description]
    result = dict(zip(columns, row))
    for key, value in result.items():
        if isinstance(value, uuid.UUID):
            result[key] = str(value)
        elif type(value) is date:
            result[key] = value.isoformat()
    return result


def _merge_upsert(table: str, key_col: str, key_val: Any, data: dict[str, Any]) -> None:
    """INSERT ... ON CONFLICT (key_col) DO UPDATE using only the columns
    present in `data`, mirroring Firestore's `.set(data, merge=True)`:
    an existing row's other columns (including created_at) are left
    untouched, and a brand-new row picks up the schema's own DEFAULTs
    (created_at now(), is_active true, etc.) for every column `data`
    doesn't specify — replacing the original's Python-side
    `if not doc_ref.get().exists: data.setdefault(...)` pre-check, which a
    schema DEFAULT makes unnecessary.
    """
    import psycopg2.sql as sql
    from psycopg2.extras import Json

    jsonb_cols = _JSONB_COLUMNS.get(table, set())
    columns = [key_col] + list(data.keys())
    values: list[Any] = [key_val]
    for col, val in data.items():
        values.append(Json(val) if col in jsonb_cols and val is not None else val)

    insert_cols = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    placeholders = sql.SQL(", ").join(sql.Placeholder() * len(columns))
    update_cols = sql.SQL(", ").join(
        sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c))
        for c in data.keys()
    )

    query = sql.SQL(
        "INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
        "ON CONFLICT ({key}) DO UPDATE SET {updates}"
    ).format(
        table=sql.Identifier(table),
        cols=insert_cols,
        placeholders=placeholders,
        key=sql.Identifier(key_col),
        updates=update_cols,
    )

    conn = _get_connection()
    with conn.cursor() as cur:
        cur.execute(query, values)


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def get_client(client_id: str) -> dict[str, Any] | None:
    conn = _get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM clients WHERE id = %s", (client_id,))
        row = cur.fetchone()
        if row is None:
            return None
        return _row_to_dict(cur, row)


def resolve_client(identifier: str) -> dict[str, Any] | None:
    """Resolve a client from a selected identifier to its canonical record.

    The Connect SP API (OAuth) flow passes a client identifier that must map to
    exactly one client record. A plain ``get_client`` only matches the exact
    id, which is brittle: an identifier that differs from the stored id by
    casing/whitespace resolves to nothing ("Client not found"), and any
    looser matching risks connecting the wrong account.

    Resolution order (first match wins):
      1. Exact id match.
      2. Case-insensitive id match.
      3. Trimmed, case-insensitive display-name match.

    The fallbacks (2 and 3) only resolve when **exactly one** client matches.
    Ambiguous matches return ``None`` so the caller never silently authorizes
    the wrong client.
    """
    if not identifier:
        return None

    exact = get_client(identifier)
    if exact:
        return exact

    needle = identifier.strip().casefold()
    if not needle:
        return None

    clients = list_clients()

    id_matches = [c for c in clients if str(c.get("id", "")).strip().casefold() == needle]
    if len(id_matches) == 1:
        return id_matches[0]

    name_matches = [c for c in clients if str(c.get("name", "")).strip().casefold() == needle]
    if len(name_matches) == 1:
        return name_matches[0]

    return None


def list_clients(active_only: bool = False) -> list[dict[str, Any]]:
    conn = _get_connection()
    with conn.cursor() as cur:
        if active_only:
            cur.execute("SELECT * FROM clients WHERE is_active = true")
        else:
            cur.execute("SELECT * FROM clients")
        rows = cur.fetchall()
        return [_row_to_dict(cur, row) for row in rows]


def upsert_client(client_id: str, data: dict[str, Any]) -> None:
    data = dict(data)
    data["updated_at"] = datetime.now(timezone.utc)
    _merge_upsert("clients", "id", client_id, data)


def delete_client(client_id: str) -> None:
    conn = _get_connection()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM clients WHERE id = %s", (client_id,))
