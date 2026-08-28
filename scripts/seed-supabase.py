#!/usr/bin/env python3
"""Create the local metrics schema in Supabase and seed one demo day for
test-client/US, so `daily_recap` run locally has real numbers to post.

Usage:
    SUPABASE_DB_URL=postgresql://... python scripts/seed-supabase.py [client_id] [client_timezone]

Note: client_timezone must match whatever client_timezone is actually configured
for that client in Firestore (or the local Firestore shim) — a mismatch causes
daily_recap to silently query the wrong day (an all-zero recap, no error).

Requires: psycopg2-binary
    pip install psycopg2-binary
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg2

DEFAULT_CLIENT_TZ = "America/Los_Angeles"  # matches daily_recap's own fallback default

SCHEMA_SQL = """
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
"""


def main() -> None:
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        print("SUPABASE_DB_URL is not set.", file=sys.stderr)
        sys.exit(1)

    client_id = sys.argv[1] if len(sys.argv) > 1 else "test-client"
    client_tz_name = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_CLIENT_TZ
    client_tz = ZoneInfo(client_tz_name)
    yesterday = (datetime.now(timezone.utc).astimezone(client_tz).date() - timedelta(days=1))

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)

            cur.execute("DELETE FROM orders WHERE client_id = %s", (client_id,))
            orders = [
                (client_id, "US", datetime(yesterday.year, yesterday.month, yesterday.day, 14, 30, tzinfo=timezone.utc), 45.99, "Shipped"),
                (client_id, "US", datetime(yesterday.year, yesterday.month, yesterday.day, 16, 5, tzinfo=timezone.utc), 129.50, "Shipped"),
                (client_id, "US", datetime(yesterday.year, yesterday.month, yesterday.day, 20, 15, tzinfo=timezone.utc), 22.00, "Shipped"),
            ]
            cur.executemany(
                "INSERT INTO orders (client_id, marketplace, purchase_date, item_price, order_status) "
                "VALUES (%s, %s, %s, %s, %s)",
                orders,
            )

            cur.execute("DELETE FROM ad_campaign_metrics WHERE client_id = %s", (client_id,))
            campaigns = [
                (client_id, "US", yesterday, "campaign-1", 18.25, 60.00),
                (client_id, "US", yesterday, "campaign-2", 6.75, 30.00),
            ]
            cur.executemany(
                "INSERT INTO ad_campaign_metrics (client_id, marketplace, date, campaign_id, cost, sales) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                campaigns,
            )
        conn.commit()
    finally:
        conn.close()

    total_sales = sum(o[3] for o in orders)
    total_spend = sum(c[4] for c in campaigns)
    total_ppc_sales = sum(c[5] for c in campaigns)
    print(f"Seeded Supabase for client_id={client_id!r}, date={yesterday.isoformat()}, client_timezone={client_tz_name!r}")
    print(f"  Total Sales: ${total_sales:.2f}")
    print(f"  Spend:       ${total_spend:.2f}")
    print(f"  PPC Sales:   ${total_ppc_sales:.2f}")


if __name__ == "__main__":
    main()
