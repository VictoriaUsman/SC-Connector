"""Daily Recap Bot — posts each client's previous-day account totals to Slack.

A year-round morning recap, independent of the hourly event bot and the
comparison "daily pulse". For every client whose bot config has
``daily_recap_enabled`` set, it:

  1. Computes the previous full calendar day in the client's configured timezone
  2. Sums that day's metrics across the client's marketplaces from BigQuery
     (Spend, PPC Sales, Total Sales — the same shared tables the hourly bot reads)
  3. Derives ACoS (Spend / PPC Sales) and TACoS (Spend / Total Sales)
  4. Posts a flat, single-day recap to the client's configured Slack channel

The message body is exactly a date line (MM/DD/YY) followed by five bulleted
lines, matching the hourly bot's visual style. It contains no comparison logic
(no DoD/WoW/MoM/YoY, no event-day indexing).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import flask
from google.cloud import bigquery

from shared.firestore_utils import (
    get_client,
    list_bot_configs,
    log_bot_activity,
)
from shared.slack_client import (
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
class AccountTotals:
    """Account-level totals for a single calendar day (summed across marketplaces)."""

    spend: float
    ppc_sales: float
    total_sales: float

    @property
    def acos(self) -> float:
        return (self.spend / self.ppc_sales * 100) if self.ppc_sales else 0.0

    @property
    def tacos(self) -> float:
        return (self.spend / self.total_sales * 100) if self.total_sales else 0.0


def handler(request: flask.Request) -> tuple[dict, int]:
    now = datetime.now(timezone.utc)

    configs = list_bot_configs()
    enabled = [c for c in configs if c.get("daily_recap_enabled")]
    if not enabled:
        logger.info("No clients with daily_recap_enabled — skipping daily recap")
        return {"status": "ok", "messages_sent": 0}, 200

    sent = 0
    errors: list[str] = []

    for config in enabled:
        client_id = config["client_id"]
        client = get_client(client_id)
        if not client or not client.get("is_active", True):
            continue

        channel_id = (
            config.get("test_channel_id")
            if config.get("use_test_channel")
            else config.get("slack_channel_id")
        )
        if not channel_id:
            logger.warning("No Slack channel for client", extra={"client_id": client_id})
            continue

        marketplaces = config.get("marketplaces", [])
        if not marketplaces:
            continue

        client_tz = ZoneInfo(config.get("client_timezone", "America/Los_Angeles"))
        recap_date = _previous_calendar_day(now, client_tz)
        currency = config.get("base_currency", "USD")

        try:
            totals = _query_account_totals(client_id, marketplaces, recap_date.isoformat())
            blocks = _build_recap_blocks(recap_date, totals, currency)
            text_fallback = f"Daily Recap — {recap_date.strftime('%m/%d/%y')}"
            result = post_message(channel_id, blocks, text_fallback)

            log_bot_activity({
                "client_id": client_id,
                "bot": "daily_recap",
                "status": "sent",
                "message_ts": result.get("ts"),
                "recap_date": recap_date.isoformat(),
                "marketplaces_reported": marketplaces,
            })
            sent += 1

        except Exception as exc:
            logger.exception("Failed to send daily recap", extra={"client_id": client_id})
            errors.append(f"{client_id}: {str(exc)[:100]}")
            log_bot_activity({
                "client_id": client_id,
                "bot": "daily_recap",
                "status": "failed",
                "recap_date": recap_date.isoformat(),
                "error": str(exc)[:500],
            })

    logger.info("Daily recap run complete", extra={"sent": sent, "errors": len(errors)})
    return {"status": "ok", "messages_sent": sent, "errors": len(errors)}, 200


# ---------------------------------------------------------------------------
# Date computation
# ---------------------------------------------------------------------------

def _previous_calendar_day(now: datetime, client_tz: ZoneInfo):
    """The previous full calendar day in the client's configured timezone."""
    return now.astimezone(client_tz).date() - timedelta(days=1)


# ---------------------------------------------------------------------------
# BigQuery — sum the prior full calendar day across marketplaces
# ---------------------------------------------------------------------------

def _query_account_totals(
    client_id: str,
    marketplaces: list[str],
    report_date: str,
) -> AccountTotals:
    """Sum Spend, PPC Sales, and Total Sales for ``report_date`` across marketplaces.

    Reads the full calendar-day partition from the shared report tables. Both the
    orders and ads tables keep the most-recent value per (client, marketplace,
    report_date) bucket via the ingestion pipeline's dedup, so summing here yields
    post-restatement totals.
    """
    dataset = os.environ.get("BQ_DATASET", "")
    project = os.environ.get("GCP_PROJECT", "")
    bq = _get_bq()

    orders = _query_orders_total(bq, project, dataset, client_id, marketplaces, report_date)
    ads = _query_ads_total(bq, project, dataset, client_id, marketplaces, report_date)

    return AccountTotals(
        spend=ads.get("spend", 0.0),
        ppc_sales=ads.get("ppc_sales", 0.0),
        total_sales=orders.get("total_sales", 0.0),
    )


def _query_orders_total(
    bq: bigquery.Client,
    project: str,
    dataset: str,
    client_id: str,
    marketplaces: list[str],
    report_date: str,
) -> dict[str, Any]:
    query = f"""
        SELECT COALESCE(SUM(item_price), 0) AS total_sales
        FROM `{project}.{dataset}.orders`
        WHERE client_id = @client_id
          AND report_date = @report_date
          AND order_status != 'Cancelled'
          AND marketplace IN UNNEST(@marketplaces)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
            bigquery.ScalarQueryParameter("report_date", "DATE", report_date),
            bigquery.ArrayQueryParameter("marketplaces", "STRING", marketplaces),
        ]
    )
    for row in bq.query(query, job_config=job_config):
        return {"total_sales": float(row["total_sales"])}
    return {}


def _query_ads_total(
    bq: bigquery.Client,
    project: str,
    dataset: str,
    client_id: str,
    marketplaces: list[str],
    report_date: str,
) -> dict[str, Any]:
    tables = {
        "sp_campaigns": "sales7d",
        "sb_campaigns": "sales",
        "sd_campaigns": "sales",
    }
    unions = []
    for table, sales_col in tables.items():
        unions.append(f"""
            SELECT cost, {sales_col} AS ppc_sales
            FROM `{project}.{dataset}.{table}`
            WHERE client_id = @client_id
              AND report_date = @report_date
              AND marketplace IN UNNEST(@marketplaces)
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
            bigquery.ScalarQueryParameter("report_date", "DATE", report_date),
            bigquery.ArrayQueryParameter("marketplaces", "STRING", marketplaces),
        ]
    )
    for row in bq.query(query, job_config=job_config):
        return {
            "spend": float(row["spend"]),
            "ppc_sales": float(row["ppc_sales"]),
        }
    return {}


# ---------------------------------------------------------------------------
# Slack message formatting
# ---------------------------------------------------------------------------

def _build_recap_blocks(recap_date, totals: AccountTotals, currency: str) -> list[dict]:
    """Build the flat single-day recap: a date line + five bulleted metric lines."""
    lines = [
        recap_date.strftime("%m/%d/%y"),
        f"• Spend: {format_currency(totals.spend, currency)}",
        f"• PPC Sales: {format_currency(totals.ppc_sales, currency)}",
        f"• ACoS: {format_percentage(totals.acos)}",
        f"• Total Sales: {format_currency(totals.total_sales, currency)}",
        f"• TACoS: {format_percentage(totals.tacos)}",
    ]
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(lines)},
        },
    ]
