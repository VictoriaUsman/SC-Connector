#!/usr/bin/env python3
"""Load a manually-exported Amazon Ads campaign report into the
`ads_prior_year_reference` BigQuery table, aggregated to one row per day.

Used to backfill a YoY baseline for daily_recap when no year-over-year data
exists yet in the normal ingestion pipeline (the pipeline is too new). Expects
the "official format" export: a CSV with at least `Date`, `Total cost`,
`Sales`, `Purchases`, and `Units sold` columns (campaign-level rows are summed
per day — campaign identity itself is discarded, only the daily account total
across all campaigns in the file matters here).

Idempotent: re-running for the same (client_id, marketplace) replaces any
existing rows in that date range rather than duplicating them, so a corrected
or re-exported CSV can just be reloaded.

Usage:
    python scripts/load_prior_year_reference.py \\
        --csv path/to/report.csv --client-id itsbodily [--marketplace US] \\
        [--project kalilos-connector-dev] [--dataset kalilos_reports_dev]
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from datetime import datetime, timezone

from google.cloud import bigquery

TABLE_NAME = "ads_prior_year_reference"

SCHEMA = [
    bigquery.SchemaField("client_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("marketplace", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("spend", "FLOAT", mode="REQUIRED"),
    bigquery.SchemaField("ppc_sales", "FLOAT", mode="REQUIRED"),
    bigquery.SchemaField("purchases", "INTEGER", mode="REQUIRED"),
    bigquery.SchemaField("units_sold", "INTEGER", mode="REQUIRED"),
    bigquery.SchemaField("source_file", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("ingested_at", "TIMESTAMP", mode="REQUIRED"),
]


def ensure_table(client: bigquery.Client, project: str, dataset: str) -> str:
    table_ref = f"{project}.{dataset}.{TABLE_NAME}"
    try:
        client.get_table(table_ref)
    except Exception:
        table = bigquery.Table(table_ref, schema=SCHEMA)
        table.time_partitioning = bigquery.TimePartitioning(field="date")
        table.clustering_fields = ["client_id", "marketplace"]
        table.description = (
            "Manually-loaded prior-year daily ad totals (Spend, PPC Sales, "
            "Purchases, Units sold) used as a YoY baseline for daily_recap "
            "when no real year-over-year data exists in the normal pipeline "
            "yet. Loaded via scripts/load_prior_year_reference.py."
        )
        client.create_table(table)
        print(f"Created table {table_ref}")
    return table_ref


def aggregate_by_day(csv_path: str) -> dict[str, dict]:
    """Sum Total cost / Sales / Purchases / Units sold per day across all campaigns."""
    totals: dict[str, dict] = defaultdict(lambda: {"spend": 0.0, "ppc_sales": 0.0, "purchases": 0, "units_sold": 0})

    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"Date", "Total cost", "Sales", "Purchases", "Units sold"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV is missing required columns: {sorted(missing)}")

        for row in reader:
            raw_date = row["Date"]
            iso_date = datetime.strptime(raw_date, "%b %d, %Y").date().isoformat()
            bucket = totals[iso_date]
            bucket["spend"] += float(row["Total cost"] or 0)
            bucket["ppc_sales"] += float(row["Sales"] or 0)
            bucket["purchases"] += int(float(row["Purchases"] or 0))
            bucket["units_sold"] += int(float(row["Units sold"] or 0))

    return totals


def load(
    csv_path: str,
    client_id: str,
    marketplace: str,
    project: str,
    dataset: str,
) -> int:
    bq = bigquery.Client(project=project)
    table_ref = ensure_table(bq, project, dataset)

    daily = aggregate_by_day(csv_path)
    if not daily:
        print("No rows found in CSV — nothing to load.")
        return 0

    dates = sorted(daily.keys())
    now = datetime.now(timezone.utc).isoformat()

    # Idempotent reload: clear any existing rows for this client/marketplace/date
    # range before inserting, so re-running with a corrected export doesn't
    # duplicate rows.
    delete_query = f"""
        DELETE FROM `{table_ref}`
        WHERE client_id = @client_id AND marketplace = @marketplace
          AND date BETWEEN @start_date AND @end_date
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
        bigquery.ScalarQueryParameter("marketplace", "STRING", marketplace),
        bigquery.ScalarQueryParameter("start_date", "DATE", dates[0]),
        bigquery.ScalarQueryParameter("end_date", "DATE", dates[-1]),
    ])
    bq.query(delete_query, job_config=job_config).result()

    rows = [
        {
            "client_id": client_id,
            "marketplace": marketplace,
            "date": d,
            "spend": round(v["spend"], 2),
            "ppc_sales": round(v["ppc_sales"], 2),
            "purchases": v["purchases"],
            "units_sold": v["units_sold"],
            "source_file": csv_path.split("/")[-1].split("\\")[-1],
            "ingested_at": now,
        }
        for d, v in sorted(daily.items())
    ]

    errors = bq.insert_rows_json(table_ref, rows)
    if errors:
        raise RuntimeError(f"BigQuery insert errors: {errors}")

    print(f"Loaded {len(rows)} day(s) into {table_ref} for {client_id}/{marketplace} "
          f"({dates[0]}..{dates[-1]})")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="Path to the exported Amazon Ads campaign CSV")
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--marketplace", default="US")
    parser.add_argument("--project", default="kalilos-connector-dev")
    parser.add_argument("--dataset", default="kalilos_reports_dev")
    args = parser.parse_args()

    try:
        load(args.csv, args.client_id, args.marketplace, args.project, args.dataset)
    except Exception as exc:  # noqa: BLE001
        print(f"Load failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
