"""Supabase (Postgres) metrics backend — stands in for BigQuery in LOCAL_MODE.

Selected via the METRICS_BACKEND env var ("bigquery", the default, or
"supabase"); the dispatch and the BigQuery implementation both live in
daily_recap/main.py, unchanged. psycopg2 is imported lazily inside
_get_connection() so a production deploy (always on the "bigquery" path)
never needs it installed at import time.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_conn = None


def _day_bounds_utc(report_date: str, client_tz: ZoneInfo) -> tuple[datetime, datetime]:
    """UTC [start, end) datetimes for report_date as a full day in client_tz."""
    day = date.fromisoformat(report_date)
    start_local = datetime(day.year, day.month, day.day, tzinfo=client_tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _get_connection():
    global _conn
    if _conn is None:
        import psycopg2  # lazy: only required when METRICS_BACKEND=supabase

        _conn = psycopg2.connect(os.environ["SUPABASE_DB_URL"])
    return _conn


def get_account_totals(
    client_id: str, marketplaces: list[str], report_date: str, client_tz: ZoneInfo,
) -> dict:
    """Sum Spend, PPC Sales, and Total Sales for report_date across
    marketplaces, from the local Supabase orders/ad_campaign_metrics tables."""
    day_start_utc, day_end_utc = _day_bounds_utc(report_date, client_tz)
    conn = _get_connection()

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(SUM(item_price), 0)
            FROM orders
            WHERE client_id = %s
              AND marketplace = ANY(%s)
              AND purchase_date >= %s
              AND purchase_date < %s
              AND order_status != 'Cancelled'
            """,
            (client_id, marketplaces, day_start_utc, day_end_utc),
        )
        total_sales = float(cur.fetchone()[0])

        cur.execute(
            """
            SELECT COALESCE(SUM(cost), 0), COALESCE(SUM(sales), 0)
            FROM ad_campaign_metrics
            WHERE client_id = %s
              AND marketplace = ANY(%s)
              AND date = %s
            """,
            (client_id, marketplaces, date.fromisoformat(report_date)),
        )
        row = cur.fetchone()
        spend, ppc_sales = float(row[0]), float(row[1])

    return {"spend": spend, "ppc_sales": ppc_sales, "total_sales": total_sales}
