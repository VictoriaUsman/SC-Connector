"""BigQuery dataset and tables for report ingestion."""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp

# ---------------------------------------------------------------------------
# Common metadata columns prepended to every report table
# ---------------------------------------------------------------------------
_META_COLUMNS = [
    {"name": "client_id", "type": "STRING", "mode": "REQUIRED"},
    {"name": "marketplace", "type": "STRING", "mode": "REQUIRED"},
    {"name": "report_date", "type": "DATE", "mode": "REQUIRED"},
    {"name": "ingested_at", "type": "TIMESTAMP", "mode": "REQUIRED"},
    {"name": "job_id", "type": "STRING", "mode": "NULLABLE"},
]

# ---------------------------------------------------------------------------
# Per-table column definitions (business columns only — meta prepended below)
# ---------------------------------------------------------------------------
_ORDERS_COLUMNS = [
    {"name": "amazon_order_id", "type": "STRING", "mode": "REQUIRED"},
    {"name": "merchant_order_id", "type": "STRING", "mode": "NULLABLE"},
    {"name": "purchase_date", "type": "TIMESTAMP", "mode": "NULLABLE"},
    {"name": "last_updated_date", "type": "TIMESTAMP", "mode": "NULLABLE"},
    {"name": "order_status", "type": "STRING", "mode": "NULLABLE"},
    {"name": "sales_channel", "type": "STRING", "mode": "NULLABLE"},
    {"name": "fulfillment_channel", "type": "STRING", "mode": "NULLABLE"},
    {"name": "sku", "type": "STRING", "mode": "NULLABLE"},
    {"name": "asin", "type": "STRING", "mode": "NULLABLE"},
    {"name": "item_status", "type": "STRING", "mode": "NULLABLE"},
    {"name": "quantity", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "currency", "type": "STRING", "mode": "NULLABLE"},
    {"name": "item_price", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "item_tax", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "shipping_price", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "shipping_tax", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "gift_wrap_price", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "gift_wrap_tax", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "item_promotion_discount", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "ship_promotion_discount", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "ship_city", "type": "STRING", "mode": "NULLABLE"},
    {"name": "ship_state", "type": "STRING", "mode": "NULLABLE"},
    {"name": "ship_postal_code", "type": "STRING", "mode": "NULLABLE"},
    {"name": "ship_country", "type": "STRING", "mode": "NULLABLE"},
    {"name": "is_business_order", "type": "BOOLEAN", "mode": "NULLABLE"},
]

_SP_CAMPAIGNS_COLUMNS = [
    {"name": "date", "type": "DATE", "mode": "NULLABLE"},
    {"name": "campaign_name", "type": "STRING", "mode": "NULLABLE"},
    {"name": "campaign_id", "type": "STRING", "mode": "NULLABLE"},
    {"name": "campaign_status", "type": "STRING", "mode": "NULLABLE"},
    {"name": "campaign_budget_amount", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "campaign_budget_type", "type": "STRING", "mode": "NULLABLE"},
    {"name": "impressions", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "clicks", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "cost", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "purchases1d", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "purchases7d", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "purchases14d", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "purchases30d", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "sales1d", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "sales7d", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "sales14d", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "sales30d", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "units_sold_clicks1d", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "units_sold_clicks7d", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "units_sold_clicks14d", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "units_sold_clicks30d", "type": "INTEGER", "mode": "NULLABLE"},
]

_SB_CAMPAIGNS_COLUMNS = [
    {"name": "date", "type": "DATE", "mode": "NULLABLE"},
    {"name": "campaign_name", "type": "STRING", "mode": "NULLABLE"},
    {"name": "campaign_id", "type": "STRING", "mode": "NULLABLE"},
    {"name": "campaign_status", "type": "STRING", "mode": "NULLABLE"},
    {"name": "campaign_budget_amount", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "impressions", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "clicks", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "cost", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "purchases", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "sales", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "units_sold_clicks", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "detail_page_views_clicks", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "new_to_brand_purchases", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "new_to_brand_sales", "type": "FLOAT", "mode": "NULLABLE"},
]

_SD_CAMPAIGNS_COLUMNS = [
    {"name": "date", "type": "DATE", "mode": "NULLABLE"},
    {"name": "campaign_name", "type": "STRING", "mode": "NULLABLE"},
    {"name": "campaign_id", "type": "STRING", "mode": "NULLABLE"},
    {"name": "campaign_status", "type": "STRING", "mode": "NULLABLE"},
    {"name": "campaign_budget_amount", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "impressions", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "clicks", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "cost", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "purchases", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "sales", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "units_sold_clicks", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "detail_page_views_clicks", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "new_to_brand_purchases", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "new_to_brand_sales", "type": "FLOAT", "mode": "NULLABLE"},
]

_SNS_OFFER_METRICS_COLUMNS = [
    {"name": "window_start", "type": "DATE", "mode": "NULLABLE"},
    {"name": "window_end", "type": "DATE", "mode": "NULLABLE"},
    {"name": "asin", "type": "STRING", "mode": "NULLABLE"},
    {"name": "sku", "type": "STRING", "mode": "NULLABLE"},
    {"name": "brand_name", "type": "STRING", "mode": "NULLABLE"},
    {"name": "fulfillment_channel_type", "type": "STRING", "mode": "NULLABLE"},
    {"name": "currency", "type": "STRING", "mode": "NULLABLE"},
    {"name": "total_subscriptions_revenue", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "shipped_subscription_units", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "active_subscriptions", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "lost_revenue_due_to_oos", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "not_delivered_due_to_oos", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "revenue_penetration", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "coupons_revenue_penetration", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "share_of_coupon_subscriptions", "type": "FLOAT", "mode": "NULLABLE"},
]

_SNS_SP_METRICS_COLUMNS = [
    {"name": "window_start", "type": "DATE", "mode": "NULLABLE"},
    {"name": "window_end", "type": "DATE", "mode": "NULLABLE"},
    {"name": "currency", "type": "STRING", "mode": "NULLABLE"},
    {"name": "total_subscriptions_revenue", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "shipped_subscription_units", "type": "FLOAT", "mode": "NULLABLE"},
]

# Appended (not interleaved) so schema updates only ever add nullable columns
# at the end — see the matching comment in shared.bq_schemas. sku/
# quantity_shipped/fulfillment_network are NULL on rows whose breakdown wasn't
# attributable to a single item (shared.finances_client._product_fields).
_FINANCE_TRANSACTIONS_COLUMNS = [
    {"name": "transaction_id", "type": "STRING", "mode": "NULLABLE"},
    {"name": "transaction_type", "type": "STRING", "mode": "NULLABLE"},
    {"name": "transaction_status", "type": "STRING", "mode": "NULLABLE"},
    {"name": "posted_date", "type": "TIMESTAMP", "mode": "NULLABLE"},
    {"name": "description", "type": "STRING", "mode": "NULLABLE"},
    {"name": "marketplace_id", "type": "STRING", "mode": "NULLABLE"},
    {"name": "related_order_id", "type": "STRING", "mode": "NULLABLE"},
    {"name": "currency", "type": "STRING", "mode": "NULLABLE"},
    {"name": "total_amount", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "breakdown_type", "type": "STRING", "mode": "NULLABLE"},
    {"name": "breakdown_amount", "type": "FLOAT", "mode": "NULLABLE"},
    {"name": "line_index", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "marketplace_name", "type": "STRING", "mode": "NULLABLE"},
    {"name": "account_type", "type": "STRING", "mode": "NULLABLE"},
    {"name": "settlement_id", "type": "STRING", "mode": "NULLABLE"},
    {"name": "release_date", "type": "DATE", "mode": "NULLABLE"},
    {"name": "sku", "type": "STRING", "mode": "NULLABLE"},
    {"name": "quantity_shipped", "type": "INTEGER", "mode": "NULLABLE"},
    {"name": "fulfillment_network", "type": "STRING", "mode": "NULLABLE"},
]

TABLE_DEFS: list[dict] = [
    {
        "name": "orders",
        "columns": _ORDERS_COLUMNS,
        "partition_field": "report_date",
        "clustering": ["client_id", "marketplace"],
        "description": "All Orders by Last Update — individual order line items",
        # orders_latest keeps one row per order line (latest update wins),
        # mirroring the old MERGE on (amazon_order_id, sku).
        "latest_keys": ("amazon_order_id", "sku"),
    },
    {
        "name": "sp_campaigns",
        "columns": _SP_CAMPAIGNS_COLUMNS,
        "partition_field": "report_date",
        "clustering": ["client_id", "marketplace"],
        "description": "Sponsored Products campaign-level metrics",
    },
    {
        "name": "sb_campaigns",
        "columns": _SB_CAMPAIGNS_COLUMNS,
        "partition_field": "report_date",
        "clustering": ["client_id", "marketplace"],
        "description": "Sponsored Brands campaign-level metrics",
    },
    {
        "name": "sd_campaigns",
        "columns": _SD_CAMPAIGNS_COLUMNS,
        "partition_field": "report_date",
        "clustering": ["client_id", "marketplace"],
        "description": "Sponsored Display campaign-level metrics",
    },
    {
        "name": "sns_offer_metrics",
        "columns": _SNS_OFFER_METRICS_COLUMNS,
        "partition_field": "report_date",
        "clustering": ["client_id", "marketplace"],
        "description": "Subscribe & Save offer-level metrics from the Replenishment API",
    },
    {
        "name": "sns_sp_metrics",
        "columns": _SNS_SP_METRICS_COLUMNS,
        "partition_field": "report_date",
        "clustering": ["client_id", "marketplace"],
        "description": "Subscribe & Save account-level metrics from the Replenishment API",
    },
    {
        "name": "finance_transactions",
        "columns": _FINANCE_TRANSACTIONS_COLUMNS,
        "partition_field": "report_date",
        "clustering": ["client_id", "marketplace"],
        "description": "Line-item financial transactions from the Finances API (v2024-06-19)",
        # finance_transactions_latest keeps one row per breakdown line, mirroring
        # the row-level shape shared.finances_client emits. Transactions are
        # immutable once posted (no last_updated_date equivalent), so order by
        # posted_date itself — ties (a re-pulled/backfilled window) fall through
        # to ingested_at, keeping the most recent pull.
        "latest_keys": ("transaction_id", "breakdown_type", "line_index"),
        "latest_order_by": "posted_date",
    },
]


def _build_schema(columns: list[dict]) -> str:
    """Build a JSON schema string for BigQuery from meta + business columns."""
    import json
    return json.dumps(_META_COLUMNS + columns)


def _latest_view_query(
    project: str,
    dataset_id: str,
    table_name: str,
    row_dedup_keys: tuple[str, ...] | None = None,
    order_by_field: str = "last_updated_date",
) -> str:
    """Build a read-side de-dup view query for an append-only base table.

    Ingestion is append-only (no DML, no table lock — this is what removed the
    "concurrent update" alert storm), so a table accumulates rows from every
    pull. The view restores the de-duplicated read semantics the old MERGE /
    DELETE+INSERT loaders provided, with one of two strategies:

    * ``row_dedup_keys`` set (orders, finance_transactions): keep one row per
      (client_id, marketplace, *keys) — the latest by ``order_by_field`` then
      ``ingested_at``. This mirrors the old MERGE on (amazon_order_id, sku) and
      is correct even when the same row is re-pulled under several
      ``report_date`` partitions (multi-day timeframes, or a backfill overlapping
      a later scheduled pull). ``order_by_field`` defaults to orders'
      ``last_updated_date``; a table without that column (e.g. finance
      transactions, which are immutable once posted) passes its own — usually
      just the date/timestamp field a re-pull can't retroactively change, so
      ties fall through to ``ingested_at`` (the real "most recent pull wins").

    * ``row_dedup_keys`` is None (ads / SnS snapshots): keep every row of the
      most-recent pull per (client_id, marketplace, report_date) — DENSE_RANK
      ties all rows sharing the latest ``ingested_at`` at rank 1. This mirrors
      the old DELETE+INSERT "latest cumulative pull wins" behaviour.
    """
    fq = f"`{project}.{dataset_id}.{table_name}`"
    if row_dedup_keys:
        partition = ", ".join(("client_id", "marketplace", *row_dedup_keys))
        return (
            "SELECT * EXCEPT(_row_rank) FROM (\n"
            "  SELECT t.*, ROW_NUMBER() OVER (\n"
            f"    PARTITION BY {partition}\n"
            f"    ORDER BY {order_by_field} DESC, ingested_at DESC\n"
            "  ) AS _row_rank\n"
            f"  FROM {fq} AS t\n"
            ")\nWHERE _row_rank = 1"
        )
    return (
        "SELECT * EXCEPT(_pull_rank) FROM (\n"
        "  SELECT t.*, DENSE_RANK() OVER (\n"
        "    PARTITION BY client_id, marketplace, report_date\n"
        "    ORDER BY ingested_at DESC\n"
        "  ) AS _pull_rank\n"
        f"  FROM {fq} AS t\n"
        ")\nWHERE _pull_rank = 1"
    )


def create(
    env: str,
    project: str,
    region: str,
    depends_on: list[pulumi.Resource],
) -> dict[str, gcp.bigquery.Table]:
    """Create the BigQuery dataset and report tables. Returns dict keyed by table name."""
    opts = pulumi.ResourceOptions(depends_on=depends_on)

    dataset_id = f"kalilos_reports_{env}"

    dataset = gcp.bigquery.Dataset(
        f"kalilos-{env}-bq-dataset",
        dataset_id=dataset_id,
        project=project,
        location=region,
        description=f"Kalilos report data warehouse ({env})",
        default_table_expiration_ms=None,
        opts=opts,
    )

    tables: dict[str, gcp.bigquery.Table] = {}
    for table_def in TABLE_DEFS:
        table_name = table_def["name"]
        resource_name = f"kalilos-{env}-bq-{table_name}"

        table = gcp.bigquery.Table(
            resource_name,
            dataset_id=dataset.dataset_id,
            table_id=table_name,
            project=project,
            schema=_build_schema(table_def["columns"]),
            time_partitioning=gcp.bigquery.TableTimePartitioningArgs(
                type="DAY",
                field=table_def["partition_field"],
            ),
            clusterings=table_def["clustering"],
            description=table_def["description"],
            deletion_protection=False,
            opts=pulumi.ResourceOptions(depends_on=[dataset]),
        )
        tables[table_name] = table

    # ----------------------------------------------------------------------
    # De-dup views — reads should target these, not the append-only base tables
    # ----------------------------------------------------------------------
    for table_def in TABLE_DEFS:
        table_name = table_def["name"]
        view_name = f"{table_name}_latest"
        base_table = tables[table_name]

        gcp.bigquery.Table(
            f"kalilos-{env}-bq-{view_name}",
            dataset_id=dataset.dataset_id,
            table_id=view_name,
            project=project,
            view=gcp.bigquery.TableViewArgs(
                query=_latest_view_query(
                    project, dataset_id, table_name, table_def.get("latest_keys"),
                    order_by_field=table_def.get("latest_order_by", "last_updated_date"),
                ),
                use_legacy_sql=False,
            ),
            description=(
                f"Latest pull per (client, marketplace, report_date) from {table_name}"
            ),
            deletion_protection=False,
            opts=pulumi.ResourceOptions(depends_on=[base_table]),
        )

    pulumi.export("bq_dataset", dataset_id)
    return tables
