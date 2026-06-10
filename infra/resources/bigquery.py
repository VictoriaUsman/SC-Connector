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

TABLE_DEFS: list[dict] = [
    {
        "name": "orders",
        "columns": _ORDERS_COLUMNS,
        "partition_field": "report_date",
        "clustering": ["client_id", "marketplace"],
        "description": "All Orders by Last Update — individual order line items",
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
]


def _build_schema(columns: list[dict]) -> str:
    """Build a JSON schema string for BigQuery from meta + business columns."""
    import json
    return json.dumps(_META_COLUMNS + columns)


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

    pulumi.export("bq_dataset", dataset_id)
    return tables
