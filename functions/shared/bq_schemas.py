"""BigQuery schema registry — maps (report_type, api_source) to table name + column spec.

Used by the ingest_bigquery function to determine which table to load into and
how to cast TSV columns. Report types not in this registry are gracefully skipped
(not all reports need BQ ingestion).

Column entries use the format: (bq_column_name, bq_type, tsv_header_name)
where tsv_header_name is the header as it appears in the downloaded TSV file.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnMapping:
    bq_name: str
    bq_type: str  # STRING, INTEGER, FLOAT, BOOLEAN, DATE, TIMESTAMP
    tsv_header: str


@dataclass(frozen=True)
class TableSchema:
    table_name: str
    columns: tuple[ColumnMapping, ...]
    dedup_key: tuple[str, ...] | None = None  # columns for MERGE dedup (None = replace)


# ---------------------------------------------------------------------------
# Orders — GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL
# ---------------------------------------------------------------------------
_ORDERS_COLUMNS = (
    ColumnMapping("amazon_order_id", "STRING", "amazon-order-id"),
    ColumnMapping("merchant_order_id", "STRING", "merchant-order-id"),
    ColumnMapping("purchase_date", "TIMESTAMP", "purchase-date"),
    ColumnMapping("last_updated_date", "TIMESTAMP", "last-updated-date"),
    ColumnMapping("order_status", "STRING", "order-status"),
    ColumnMapping("sales_channel", "STRING", "sales-channel"),
    ColumnMapping("fulfillment_channel", "STRING", "fulfillment-channel"),
    ColumnMapping("sku", "STRING", "sku"),
    ColumnMapping("asin", "STRING", "asin"),
    ColumnMapping("item_status", "STRING", "item-status"),
    ColumnMapping("quantity", "INTEGER", "quantity"),
    ColumnMapping("currency", "STRING", "currency"),
    ColumnMapping("item_price", "FLOAT", "item-price"),
    ColumnMapping("item_tax", "FLOAT", "item-tax"),
    ColumnMapping("shipping_price", "FLOAT", "shipping-price"),
    ColumnMapping("shipping_tax", "FLOAT", "shipping-tax"),
    ColumnMapping("gift_wrap_price", "FLOAT", "gift-wrap-price"),
    ColumnMapping("gift_wrap_tax", "FLOAT", "gift-wrap-tax"),
    ColumnMapping("item_promotion_discount", "FLOAT", "item-promotion-discount"),
    ColumnMapping("ship_promotion_discount", "FLOAT", "ship-promotion-discount"),
    ColumnMapping("ship_city", "STRING", "ship-city"),
    ColumnMapping("ship_state", "STRING", "ship-state"),
    ColumnMapping("ship_postal_code", "STRING", "ship-postal-code"),
    ColumnMapping("ship_country", "STRING", "ship-country"),
    ColumnMapping("is_business_order", "BOOLEAN", "is-business-order"),
)

_ORDERS_SCHEMA = TableSchema(
    table_name="orders",
    columns=_ORDERS_COLUMNS,
    dedup_key=("amazon_order_id", "sku"),
)

# ---------------------------------------------------------------------------
# SP Campaigns — spCampaigns (Sponsored Products)
# ---------------------------------------------------------------------------
_SP_CAMPAIGNS_COLUMNS = (
    ColumnMapping("date", "DATE", "date"),
    ColumnMapping("campaign_name", "STRING", "campaignName"),
    ColumnMapping("campaign_id", "STRING", "campaignId"),
    ColumnMapping("campaign_status", "STRING", "campaignStatus"),
    ColumnMapping("campaign_budget_amount", "FLOAT", "campaignBudgetAmount"),
    ColumnMapping("campaign_budget_type", "STRING", "campaignBudgetType"),
    ColumnMapping("impressions", "INTEGER", "impressions"),
    ColumnMapping("clicks", "INTEGER", "clicks"),
    ColumnMapping("cost", "FLOAT", "cost"),
    ColumnMapping("purchases1d", "INTEGER", "purchases1d"),
    ColumnMapping("purchases7d", "INTEGER", "purchases7d"),
    ColumnMapping("purchases14d", "INTEGER", "purchases14d"),
    ColumnMapping("purchases30d", "INTEGER", "purchases30d"),
    ColumnMapping("sales1d", "FLOAT", "sales1d"),
    ColumnMapping("sales7d", "FLOAT", "sales7d"),
    ColumnMapping("sales14d", "FLOAT", "sales14d"),
    ColumnMapping("sales30d", "FLOAT", "sales30d"),
    ColumnMapping("units_sold_clicks1d", "INTEGER", "unitsSoldClicks1d"),
    ColumnMapping("units_sold_clicks7d", "INTEGER", "unitsSoldClicks7d"),
    ColumnMapping("units_sold_clicks14d", "INTEGER", "unitsSoldClicks14d"),
    ColumnMapping("units_sold_clicks30d", "INTEGER", "unitsSoldClicks30d"),
)

_SP_CAMPAIGNS_SCHEMA = TableSchema(
    table_name="sp_campaigns",
    columns=_SP_CAMPAIGNS_COLUMNS,
    dedup_key=None,  # replace strategy: delete + insert for same (client, mkt, date)
)

# ---------------------------------------------------------------------------
# SB Campaigns — sbCampaigns (Sponsored Brands)
# ---------------------------------------------------------------------------
_SB_CAMPAIGNS_COLUMNS = (
    ColumnMapping("date", "DATE", "date"),
    ColumnMapping("campaign_name", "STRING", "campaignName"),
    ColumnMapping("campaign_id", "STRING", "campaignId"),
    ColumnMapping("campaign_status", "STRING", "campaignStatus"),
    ColumnMapping("campaign_budget_amount", "FLOAT", "campaignBudgetAmount"),
    ColumnMapping("impressions", "INTEGER", "impressions"),
    ColumnMapping("clicks", "INTEGER", "clicks"),
    ColumnMapping("cost", "FLOAT", "cost"),
    ColumnMapping("purchases", "INTEGER", "purchases"),
    ColumnMapping("sales", "FLOAT", "sales"),
    ColumnMapping("units_sold_clicks", "INTEGER", "unitsSoldClicks"),
    ColumnMapping("detail_page_views_clicks", "INTEGER", "detailPageViewsClicks"),
    ColumnMapping("new_to_brand_purchases", "INTEGER", "newToBrandPurchases"),
    ColumnMapping("new_to_brand_sales", "FLOAT", "newToBrandSales"),
)

_SB_CAMPAIGNS_SCHEMA = TableSchema(
    table_name="sb_campaigns",
    columns=_SB_CAMPAIGNS_COLUMNS,
    dedup_key=None,
)

# ---------------------------------------------------------------------------
# SD Campaigns — sdCampaigns (Sponsored Display)
# ---------------------------------------------------------------------------
_SD_CAMPAIGNS_COLUMNS = (
    ColumnMapping("date", "DATE", "date"),
    ColumnMapping("campaign_name", "STRING", "campaignName"),
    ColumnMapping("campaign_id", "STRING", "campaignId"),
    ColumnMapping("campaign_status", "STRING", "campaignStatus"),
    ColumnMapping("campaign_budget_amount", "FLOAT", "campaignBudgetAmount"),
    ColumnMapping("impressions", "INTEGER", "impressions"),
    ColumnMapping("clicks", "INTEGER", "clicks"),
    ColumnMapping("cost", "FLOAT", "cost"),
    ColumnMapping("purchases", "INTEGER", "purchases"),
    ColumnMapping("sales", "FLOAT", "sales"),
    ColumnMapping("units_sold_clicks", "INTEGER", "unitsSoldClicks"),
    ColumnMapping("detail_page_views_clicks", "INTEGER", "detailPageViewsClicks"),
    ColumnMapping("new_to_brand_purchases", "INTEGER", "newToBrandPurchases"),
    ColumnMapping("new_to_brand_sales", "FLOAT", "newToBrandSales"),
)

_SD_CAMPAIGNS_SCHEMA = TableSchema(
    table_name="sd_campaigns",
    columns=_SD_CAMPAIGNS_COLUMNS,
    dedup_key=None,
)

# ---------------------------------------------------------------------------
# Subscribe & Save offer metrics — SNS_OFFER_METRICS (Replenishment API)
# ---------------------------------------------------------------------------
# Synchronous API operation (see shared.api_operations). The TSV headers below
# are the flattened response field names verified against Moxe US live
# Replenishment responses. Unmapped headers are ignored by ingestion.
_SNS_OFFER_METRICS_COLUMNS = (
    ColumnMapping("window_start", "DATE", "window_start"),
    ColumnMapping("window_end", "DATE", "window_end"),
    ColumnMapping("asin", "STRING", "asin"),
    ColumnMapping("sku", "STRING", "sku"),
    ColumnMapping("brand_name", "STRING", "brandName"),
    ColumnMapping("fulfillment_channel_type", "STRING", "fulfillmentChannelType"),
    ColumnMapping("currency", "STRING", "currencyCode"),
    ColumnMapping("total_subscriptions_revenue", "FLOAT", "totalSubscriptionsRevenue"),
    ColumnMapping("shipped_subscription_units", "FLOAT", "shippedSubscriptionUnits"),
    ColumnMapping("active_subscriptions", "INTEGER", "activeSubscriptions"),
    ColumnMapping("lost_revenue_due_to_oos", "FLOAT", "lostRevenueDueToOOS"),
    ColumnMapping("not_delivered_due_to_oos", "FLOAT", "notDeliveredDueToOOS"),
    ColumnMapping("revenue_penetration", "FLOAT", "revenuePenetration"),
    ColumnMapping("coupons_revenue_penetration", "FLOAT", "couponsRevenuePenetration"),
    ColumnMapping("share_of_coupon_subscriptions", "FLOAT", "shareOfCouponSubscriptions"),
)

_SNS_OFFER_METRICS_SCHEMA = TableSchema(
    table_name="sns_offer_metrics",
    columns=_SNS_OFFER_METRICS_COLUMNS,
    dedup_key=None,
)

# ---------------------------------------------------------------------------
# Subscribe & Save account metrics — SNS_SP_METRICS (Replenishment API)
# ---------------------------------------------------------------------------
_SNS_SP_METRICS_COLUMNS = (
    ColumnMapping("window_start", "DATE", "window_start"),
    ColumnMapping("window_end", "DATE", "window_end"),
    ColumnMapping("currency", "STRING", "currencyCode"),
    ColumnMapping("total_subscriptions_revenue", "FLOAT", "totalSubscriptionsRevenue"),
    ColumnMapping("shipped_subscription_units", "FLOAT", "shippedSubscriptionUnits"),
)

_SNS_SP_METRICS_SCHEMA = TableSchema(
    table_name="sns_sp_metrics",
    columns=_SNS_SP_METRICS_COLUMNS,
    dedup_key=None,
)

# ---------------------------------------------------------------------------
# Registry: (report_type, api_source) -> TableSchema
# ---------------------------------------------------------------------------
_REGISTRY: dict[tuple[str, str], TableSchema] = {
    ("GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL", "sp_api"): _ORDERS_SCHEMA,
    ("spCampaigns", "ads_api"): _SP_CAMPAIGNS_SCHEMA,
    ("sbCampaigns", "ads_api"): _SB_CAMPAIGNS_SCHEMA,
    ("sdCampaigns", "ads_api"): _SD_CAMPAIGNS_SCHEMA,
    ("SNS_OFFER_METRICS", "sp_api"): _SNS_OFFER_METRICS_SCHEMA,
    ("SNS_SP_METRICS", "sp_api"): _SNS_SP_METRICS_SCHEMA,
}


def get_table_schema(report_type: str, api_source: str) -> TableSchema | None:
    """Look up the BQ table schema for a report type. Returns None if not registered."""
    return _REGISTRY.get((report_type, api_source))


def is_registered(report_type: str, api_source: str) -> bool:
    """Check whether a report type has a BigQuery ingestion schema."""
    return (report_type, api_source) in _REGISTRY


# ---------------------------------------------------------------------------
# Type casting helpers for TSV -> BQ row conversion
# ---------------------------------------------------------------------------
def cast_value(raw: str, bq_type: str) -> str | int | float | bool | None:
    """Cast a raw TSV string value to the appropriate Python type for BQ loading."""
    if not raw or raw.strip() == "":
        return None

    raw = raw.strip()
    if bq_type == "STRING":
        return raw
    if bq_type == "INTEGER":
        try:
            return int(float(raw))
        except (ValueError, TypeError):
            return None
    if bq_type == "FLOAT":
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None
    if bq_type == "BOOLEAN":
        return raw.lower() in ("true", "1", "yes")
    if bq_type in ("DATE", "TIMESTAMP"):
        return raw
    return raw
