"""Hourly Event Bot — queries BigQuery for day-to-date metrics and posts to Slack.

Triggered at :45 past each hour by Cloud Scheduler. Only produces messages
during active events. For each client with an enabled hourly bot config:
  1. Query BigQuery for orders (Total Sales, Units) and ads (Spend, PPC Sales)
  2. Compute ACoS and TACoS per marketplace
  3. Format a Slack Block Kit message with per-marketplace breakdown
  4. Optionally add a Total row for single-currency clients
  5. Post to the client's Slack channel
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import flask
from google.cloud import bigquery

from shared.firestore_utils import (
    get_client,
    get_live_event,
    list_bot_configs,
    log_bot_activity,
)
from shared.schedule_compute import marketplace_today
from shared.slack_client import (
    MARKETPLACE_CURRENCIES,
    format_currency,
    format_percentage,
    post_message,
)

logger = logging.getLogger(__name__)

_bq_client: bigquery.Client | None = None


def _get_bq() -> bigquery.Client:
    global _bq_client
    if _bq_client is None:
        _bq_client = bigquery.Client(project=os.environ.get("GCP_PROJECT"))
    return _bq_client


@dataclass
class MarketplaceMetrics:
    marketplace: str
    currency: str
    total_sales: float
    units: int
    spend: float
    ppc_sales: float

    @property
    def acos(self) -> float:
        return (self.spend / self.ppc_sales * 100) if self.ppc_sales else 0.0

    @property
    def tacos(self) -> float:
        return (self.spend / self.total_sales * 100) if self.total_sales else 0.0


def handler(request: flask.Request) -> tuple[dict, int]:
    now = datetime.now(timezone.utc)

    live_event = get_live_event()
    if not live_event:
        logger.info("No active event — skipping hourly bot")
        return {"status": "ok", "messages_sent": 0}, 200

    configs = list_bot_configs()
    enabled = [c for c in configs if c.get("hourly_bot", {}).get("enabled")]
    if not enabled:
        logger.info("No enabled hourly bot configs")
        return {"status": "ok", "messages_sent": 0}, 200

    event_name = live_event.get("name", "Event")
    event_start = live_event.get("start_date", "")

    sent = 0
    errors: list[str] = []

    for config in enabled:
        client_id = config["client_id"]
        client = get_client(client_id)
        if not client or not client.get("is_active", True):
            continue

        client_name = client.get("name", client_id)
        client_tz_str = config.get("client_timezone", "America/Los_Angeles")
        client_tz = ZoneInfo(client_tz_str)
        channel_id = config.get("test_channel_id") if config.get("use_test_channel") else config.get("slack_channel_id")
        if not channel_id:
            logger.warning("No Slack channel for client", extra={"client_id": client_id})
            continue

        marketplaces = config.get("marketplaces", [])
        if not marketplaces:
            continue

        day_index = _compute_day_index(event_start, now, client_tz)

        try:
            metrics = _query_metrics(client_id, marketplaces, now)
            blocks = _build_message_blocks(
                client_name=client_name,
                event_name=event_name,
                day_index=day_index,
                now=now,
                client_tz=client_tz,
                metrics=metrics,
                base_currency=config.get("base_currency", "USD"),
            )
            text_fallback = f"Hourly Update — {client_name} | {event_name}"
            result = post_message(channel_id, blocks, text_fallback)

            log_bot_activity({
                "client_id": client_id,
                "event_id": live_event["id"],
                "status": "sent",
                "message_ts": result.get("ts"),
                "marketplaces_reported": marketplaces,
            })
            sent += 1

        except Exception as exc:
            logger.exception("Failed to send hourly bot message", extra={"client_id": client_id})
            errors.append(f"{client_id}: {str(exc)[:100]}")
            log_bot_activity({
                "client_id": client_id,
                "event_id": live_event["id"],
                "status": "failed",
                "error": str(exc)[:500],
            })

    logger.info("Hourly bot run complete", extra={"sent": sent, "errors": len(errors)})
    return {"status": "ok", "messages_sent": sent, "errors": len(errors)}, 200


# ---------------------------------------------------------------------------
# BigQuery queries
# ---------------------------------------------------------------------------

def _query_metrics(
    client_id: str,
    marketplaces: list[str],
    now: datetime,
) -> list[MarketplaceMetrics]:
    """Query BQ for today's orders and ads data, returning per-marketplace metrics.

    Each marketplace is queried with its own local date to match ingestion,
    which stores report_date as the marketplace-local calendar day.
    """
    dataset = os.environ.get("BQ_DATASET", "")
    project = os.environ.get("GCP_PROJECT", "")
    bq = _get_bq()

    results: list[MarketplaceMetrics] = []
    for mkt in marketplaces:
        mkt_today = marketplace_today(mkt, now).isoformat()
        orders = _query_orders(bq, project, dataset, client_id, mkt, mkt_today, now)
        ads = _query_ads(bq, project, dataset, client_id, mkt, mkt_today)
        results.append(MarketplaceMetrics(
            marketplace=mkt,
            currency=MARKETPLACE_CURRENCIES.get(mkt, "USD"),
            total_sales=orders.get("total_sales", 0.0),
            units=orders.get("units", 0),
            spend=ads.get("spend", 0.0),
            ppc_sales=ads.get("ppc_sales", 0.0),
        ))
    return results


def _query_orders(
    bq: bigquery.Client,
    project: str,
    dataset: str,
    client_id: str,
    marketplace: str,
    report_date: str,
    now: datetime,
) -> dict[str, Any]:
    """Sum orders placed today for a single marketplace.

    ``report_date`` is the marketplace-local calendar day (YYYY-MM-DD),
    matching how the ingestion pipeline stores data. ``mkt_midnight`` is
    derived from the same marketplace timezone for the purchase_date filter.
    """
    from shared.config import MARKETPLACE_TIMEZONES

    mkt_tz = ZoneInfo(MARKETPLACE_TIMEZONES.get(marketplace, "America/Los_Angeles"))
    mkt_now = now.astimezone(mkt_tz)
    mkt_midnight = mkt_now.replace(hour=0, minute=0, second=0, microsecond=0)
    mkt_midnight_utc = mkt_midnight.astimezone(ZoneInfo("UTC"))

    query = f"""
        SELECT
            COALESCE(SUM(item_price), 0) AS total_sales,
            COALESCE(SUM(quantity), 0) AS units
        FROM `{project}.{dataset}.orders`
        WHERE client_id = @client_id
          AND report_date = @today
          AND purchase_date >= @mkt_midnight
          AND order_status != 'Cancelled'
          AND marketplace = @marketplace
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
            bigquery.ScalarQueryParameter("today", "DATE", report_date),
            bigquery.ScalarQueryParameter("mkt_midnight", "TIMESTAMP", mkt_midnight_utc.strftime("%Y-%m-%dT%H:%M:%SZ")),
            bigquery.ScalarQueryParameter("marketplace", "STRING", marketplace),
        ]
    )
    for row in bq.query(query, job_config=job_config):
        return {
            "total_sales": float(row["total_sales"]),
            "units": int(row["units"]),
        }
    return {}


def _query_ads(
    bq: bigquery.Client,
    project: str,
    dataset: str,
    client_id: str,
    marketplace: str,
    report_date: str,
) -> dict[str, Any]:
    """Sum ads spend and sales across sp/sb/sd campaigns for a single marketplace.

    ``report_date`` is the marketplace-local calendar day (YYYY-MM-DD),
    matching how the ingestion pipeline stores data.
    """
    tables = ["sp_campaigns", "sb_campaigns", "sd_campaigns"]
    sales_cols = {
        "sp_campaigns": "sales7d",
        "sb_campaigns": "sales",
        "sd_campaigns": "sales",
    }

    unions = []
    for table in tables:
        sales_col = sales_cols[table]
        unions.append(f"""
            SELECT cost, {sales_col} AS ppc_sales
            FROM `{project}.{dataset}.{table}`
            WHERE client_id = @client_id
              AND report_date = @today
              AND marketplace = @marketplace
        """)

    query = f"""
        SELECT
            COALESCE(SUM(cost), 0) AS spend,
            COALESCE(SUM(ppc_sales), 0) AS ppc_sales
        FROM ({' UNION ALL '.join(unions)})
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
            bigquery.ScalarQueryParameter("today", "DATE", report_date),
            bigquery.ScalarQueryParameter("marketplace", "STRING", marketplace),
        ]
    )
    for row in bq.query(query, job_config=job_config):
        return {
            "spend": float(row["spend"]),
            "ppc_sales": float(row["ppc_sales"]),
        }
    return {}


# ---------------------------------------------------------------------------
# Slack message formatting (Step 8)
# ---------------------------------------------------------------------------

_MARKETPLACE_TIMEZONES: dict[str, str] = {
    "US": "America/Los_Angeles",
    "CA": "America/Los_Angeles",
    "MX": "America/Los_Angeles",
    "UK": "Europe/London",
    "DE": "Europe/Paris",
    "FR": "Europe/Paris",
    "IT": "Europe/Paris",
    "ES": "Europe/Paris",
    "NL": "Europe/Paris",
    "TR": "Europe/Istanbul",
    "AU": "Australia/Sydney",
    "SG": "Asia/Singapore",
}

_TZ_ABBREVIATIONS: dict[str, str] = {
    "America/Los_Angeles": "PST",
    "America/New_York": "EST",
    "Europe/London": "GMT",
    "Europe/Paris": "CET",
    "Europe/Istanbul": "TRT",
    "Australia/Sydney": "AEST",
    "Asia/Singapore": "SGT",
}


def _format_local_time(now: datetime, tz: ZoneInfo) -> str:
    """Format time like '6 PM PST'."""
    local = now.astimezone(tz)
    hour = local.strftime("%-I %p")
    tz_name = _TZ_ABBREVIATIONS.get(str(tz), str(tz))
    return f"{hour} {tz_name}"


def _build_message_blocks(
    *,
    client_name: str,
    event_name: str,
    day_index: int,
    now: datetime,
    client_tz: ZoneInfo,
    metrics: list[MarketplaceMetrics],
    base_currency: str,
) -> list[dict]:
    """Build Slack Block Kit blocks for the hourly update."""
    time_str = _format_local_time(now, client_tz)
    day_str = f"Day {day_index}" if day_index > 0 else ""
    subtitle = f"{time_str} | {event_name}"
    if day_str:
        subtitle += f" — {day_str}"

    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":zap: *Hourly Update — {client_name}*\n{subtitle}",
            },
        },
    ]

    for m in metrics:
        mkt_header = f"*{m.marketplace}*"
        mkt_tz_str = _MARKETPLACE_TIMEZONES.get(m.marketplace)
        if mkt_tz_str and mkt_tz_str != str(client_tz):
            mkt_tz = ZoneInfo(mkt_tz_str)
            mkt_time = _format_local_time(now, mkt_tz)
            mkt_header += f" ({mkt_time})"

        lines = [
            mkt_header,
            f"Spend: {format_currency(m.spend, m.currency)}",
            f"PPC Sales: {format_currency(m.ppc_sales, m.currency)}",
            f"ACoS: {format_percentage(m.acos)}",
            f"Total Sales: {format_currency(m.total_sales, m.currency)}",
            f"TACoS: {format_percentage(m.tacos)}",
        ]

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(lines)},
        })

    _maybe_add_total_row(blocks, metrics, base_currency)

    return blocks


def _maybe_add_total_row(
    blocks: list[dict],
    metrics: list[MarketplaceMetrics],
    base_currency: str,
) -> None:
    """Add a Total row if all marketplaces share the same currency."""
    if not metrics:
        return

    currencies = {m.currency for m in metrics}
    if len(currencies) != 1:
        return

    currency = currencies.pop()
    total_spend = sum(m.spend for m in metrics)
    total_ppc = sum(m.ppc_sales for m in metrics)
    total_sales = sum(m.total_sales for m in metrics)
    acos = (total_spend / total_ppc * 100) if total_ppc else 0.0
    tacos = (total_spend / total_sales * 100) if total_sales else 0.0

    lines = [
        "*Total*",
        f"Spend: {format_currency(total_spend, currency)}",
        f"PPC Sales: {format_currency(total_ppc, currency)}",
        f"ACoS: {format_percentage(acos)}",
        f"Total Sales: {format_currency(total_sales, currency)}",
        f"TACoS: {format_percentage(tacos)}",
    ]

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(lines)},
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compute_day_index(event_start: str, now: datetime, tz: ZoneInfo) -> int:
    """Compute 1-based day index within the event using the client's local date."""
    from datetime import date as date_type

    try:
        start = (
            event_start if hasattr(event_start, "year")
            else date_type.fromisoformat(event_start)
        )
        local_today = now.astimezone(tz).date()
        return (local_today - start).days + 1
    except (ValueError, TypeError):
        return 0
