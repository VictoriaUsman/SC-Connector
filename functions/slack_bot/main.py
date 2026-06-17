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
from datetime import date as date_type, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import flask
from google.cloud import bigquery

from shared.firestore_utils import (
    get_client,
    get_event,
    get_live_event,
    get_thread_anchor_ts,
    list_bot_configs,
    log_bot_activity,
    set_thread_anchor_ts,
)
from shared.logging_setup import init_logging
from shared.schedule_compute import marketplace_today
from shared.slack_client import (
    MARKETPLACE_CURRENCIES,
    format_currency,
    format_delta,
    format_delta_bps,
    format_percentage,
    post_message,
)

logger = logging.getLogger(__name__)
init_logging("slack-bot")

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


# Half-cent tolerance so currency rounding noise (e.g. total == ppc within a
# fraction of a cent) does not trip the invariant; only real violations fail.
_INVARIANT_TOLERANCE = 0.005


class TotalSalesInvariantError(Exception):
    """Raised when an hourly row has PPC Sales greater than Total Sales.

    Total ordered sales include ad-attributed sales, so PPC Sales can never
    exceed Total Sales. A violation means Total Sales is stale/frozen (the very
    bug this guard protects against), so we alert instead of posting a
    misleading update.
    """


def _check_total_sales_invariant(metrics: list[MarketplaceMetrics]) -> None:
    """Assert Total Sales >= PPC Sales for every hourly row.

    Raises ``TotalSalesInvariantError`` listing each offending marketplace.
    """
    violations = [
        m for m in metrics
        if m.ppc_sales > m.total_sales + _INVARIANT_TOLERANCE
    ]
    if violations:
        detail = "; ".join(
            f"{m.marketplace}: PPC Sales={m.ppc_sales:.2f} > Total Sales={m.total_sales:.2f}"
            for m in violations
        )
        raise TotalSalesInvariantError(detail)


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
    invariant_skips: list[str] = []

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
        recap_day = day_index - 1 if day_index >= 2 else 0
        is_midnight_recap = _is_midnight_recap_slot(now, client_tz) and recap_day >= 1

        try:
            if is_midnight_recap:
                recap_date = _report_date_for_event_day(event_start, recap_day, client_tz)
                # The recap reports the just-completed day, so it threads under
                # that day's anchor (not the new day that just rolled over).
                anchor_date = date_type.fromisoformat(recap_date)
                anchor_day = recap_day
                metrics = _query_metrics(
                    client_id, marketplaces, now, report_date=recap_date, full_day=True,
                )
                prior_single, prior_cumulative = _fetch_prior_yoy_metrics(
                    live_event.get("prior_event_id"),
                    client_id,
                    marketplaces,
                    recap_day,
                    client_tz,
                )
                current_cumulative = (
                    _query_cumulative_metrics(
                        client_id, marketplaces, event_start, recap_day, client_tz,
                    )
                    if recap_day >= 2
                    else None
                )
                blocks = _build_recap_blocks(
                    client_name=client_name,
                    event_name=event_name,
                    recap_day=recap_day,
                    now=now,
                    client_tz=client_tz,
                    metrics=metrics,
                    prior_single=prior_single,
                    prior_cumulative=prior_cumulative,
                    current_cumulative=current_cumulative,
                    base_currency=config.get("base_currency", "USD"),
                )
                text_fallback = f"Day {recap_day} Recap — {client_name} | {event_name}"
            else:
                metrics = _query_metrics(client_id, marketplaces, now)
                _check_total_sales_invariant(metrics)
                # Hourly updates report the current local day.
                anchor_date = now.astimezone(client_tz).date()
                anchor_day = day_index
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

            # One thin parent message per event-day acts as the thread anchor;
            # every update for that day (hourly + the next-morning recap) lands
            # as a threaded reply under it. The anchor is created lazily on the
            # first update of the day and reused thereafter, so a mid-day
            # restart never spawns a duplicate parent.
            parent_ts = _ensure_day_anchor(
                channel_id=channel_id,
                event_id=live_event["id"],
                client_id=client_id,
                event_name=event_name,
                event_date=anchor_date,
                day_index=anchor_day,
            )
            result = post_message(channel_id, blocks, text_fallback, thread_ts=parent_ts)

            log_bot_activity({
                "client_id": client_id,
                "event_id": live_event["id"],
                "status": "sent",
                "message_ts": result.get("ts"),
                "parent_ts": parent_ts,
                "marketplaces_reported": marketplaces,
            })
            sent += 1

        except TotalSalesInvariantError as exc:
            # Expected transient: this hour's orders pull has not ingested yet,
            # so Total Sales lags PPC Sales. The append-only ingest + *_latest
            # views make this self-heal on the next pull, so it is a WARNING
            # (skip this post) rather than an ERROR that pages on-call.
            logger.warning(
                "Total Sales invariant violated — skipping hourly post (orders not yet ingested)",
                extra={
                    "client_id": client_id,
                    "phase": "total_sales_invariant",
                    "error_code": "TOTAL_SALES_LT_PPC",
                    "detail": str(exc),
                },
            )
            invariant_skips.append(f"{client_id}: invariant: {str(exc)[:100]}")
            log_bot_activity({
                "client_id": client_id,
                "event_id": live_event["id"],
                "status": "failed",
                "error": f"Total Sales invariant violated: {str(exc)[:500]}",
            })

        except Exception as exc:
            logger.exception("Failed to send hourly bot message", extra={"client_id": client_id})
            errors.append(f"{client_id}: {str(exc)[:100]}")
            log_bot_activity({
                "client_id": client_id,
                "event_id": live_event["id"],
                "status": "failed",
                "error": str(exc)[:500],
            })

    if errors:
        logger.error(
            "Hourly bot run completed with errors",
            extra={
                "sent": sent,
                "errors": len(errors),
                "invariant_skips": len(invariant_skips),
                "error_code": "PARTIAL_FAILURE",
                "failures": errors[:20],
            },
        )
    elif invariant_skips:
        logger.warning(
            "Hourly bot run completed with invariant skips (orders not yet ingested)",
            extra={
                "sent": sent,
                "invariant_skips": len(invariant_skips),
                "skipped": invariant_skips[:20],
            },
        )
    else:
        logger.info("Hourly bot run complete", extra={"sent": sent, "errors": 0})
    return {
        "status": "ok",
        "messages_sent": sent,
        "errors": len(errors),
        "invariant_skips": len(invariant_skips),
    }, 200


# ---------------------------------------------------------------------------
# BigQuery queries
# ---------------------------------------------------------------------------

def _query_metrics(
    client_id: str,
    marketplaces: list[str],
    now: datetime,
    *,
    report_date: str | None = None,
    full_day: bool = False,
) -> list[MarketplaceMetrics]:
    """Query BQ for orders and ads data, returning per-marketplace metrics.

    When ``report_date`` is set, queries that calendar day (used for day-end
    recaps). ``full_day=True`` uses the complete report_date partition without
    filtering orders to purchases since midnight (hourly updates use partial day).
    """
    dataset = os.environ.get("BQ_DATASET", "")
    project = os.environ.get("GCP_PROJECT", "")
    bq = _get_bq()

    results: list[MarketplaceMetrics] = []
    for mkt in marketplaces:
        mkt_date = report_date or marketplace_today(mkt, now).isoformat()
        orders = _query_orders(
            bq, project, dataset, client_id, mkt, mkt_date, now, full_day=full_day,
        )
        ads = _query_ads(bq, project, dataset, client_id, mkt, mkt_date)
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
    *,
    full_day: bool = False,
) -> dict[str, Any]:
    """Sum orders for a single marketplace on ``report_date``.

    Hourly updates filter to purchases within the current marketplace-local day
    (midnight today up to next midnight). Day-end recaps sum the completed day's
    full window (00:00–23:59 in the marketplace's local timezone).

    Both paths key off each order's ``purchase_date`` rather than the ingestion
    ``report_date`` partition. The ``report_date`` is the *start* of the report's
    pulled range, so a multi-day pull (e.g. by-last-update orders) stamps rows
    spanning many purchase days with a single range-start date — and later
    hourly pulls land today's orders under a different range-start partition.
    Filtering on ``report_date`` therefore (a) summed several days of orders
    into one recap day, roughly doubling Total Sales, and (b) froze the hourly
    Total Sales to whichever partition matched the first run while ads metrics
    kept refreshing — letting PPC Sales exceed Total Sales. Orders are deduped
    at ingestion (MERGE on order id + sku), so summing ``item_price`` over the
    purchase-date window is correct and does not double count.
    """
    from shared.config import MARKETPLACE_TIMEZONES

    mkt_tz = ZoneInfo(MARKETPLACE_TIMEZONES.get(marketplace, "America/Los_Angeles"))

    if full_day:
        day = date_type.fromisoformat(report_date)
        day_start_local = datetime(day.year, day.month, day.day, tzinfo=mkt_tz)
        day_end_local = day_start_local + timedelta(days=1)
        day_start_utc = day_start_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        day_end_utc = day_end_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        query = f"""
            SELECT
                COALESCE(SUM(item_price), 0) AS total_sales,
                COALESCE(SUM(quantity), 0) AS units
            FROM `{project}.{dataset}.orders_latest`
            WHERE client_id = @client_id
              AND purchase_date >= @day_start
              AND purchase_date < @day_end
              AND order_status != 'Cancelled'
              AND marketplace = @marketplace
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
                bigquery.ScalarQueryParameter("day_start", "TIMESTAMP", day_start_utc),
                bigquery.ScalarQueryParameter("day_end", "TIMESTAMP", day_end_utc),
                bigquery.ScalarQueryParameter("marketplace", "STRING", marketplace),
            ]
        )
    else:
        mkt_now = now.astimezone(mkt_tz)
        mkt_midnight = mkt_now.replace(hour=0, minute=0, second=0, microsecond=0)
        mkt_next_midnight = mkt_midnight + timedelta(days=1)
        mkt_midnight_utc = mkt_midnight.astimezone(ZoneInfo("UTC"))
        mkt_next_midnight_utc = mkt_next_midnight.astimezone(ZoneInfo("UTC"))

        query = f"""
            SELECT
                COALESCE(SUM(item_price), 0) AS total_sales,
                COALESCE(SUM(quantity), 0) AS units
            FROM `{project}.{dataset}.orders_latest`
            WHERE client_id = @client_id
              AND purchase_date >= @mkt_midnight
              AND purchase_date < @mkt_next_midnight
              AND order_status != 'Cancelled'
              AND marketplace = @marketplace
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
                bigquery.ScalarQueryParameter(
                    "mkt_midnight", "TIMESTAMP",
                    mkt_midnight_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                ),
                bigquery.ScalarQueryParameter(
                    "mkt_next_midnight", "TIMESTAMP",
                    mkt_next_midnight_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                ),
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
            FROM `{project}.{dataset}.{table}_latest`
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
# Per-day thread anchor
# ---------------------------------------------------------------------------

def _build_day_anchor_blocks(
    event_name: str, day_index: int, event_date: date_type
) -> tuple[list[dict], str]:
    """Build the thin daily anchor message (e.g. "📊 Prime Day — Day 2, Jun 22").

    The anchor carries no metrics — it exists only as the thread parent and is
    never rewritten on subsequent updates.
    """
    date_label = event_date.strftime("%b %-d")
    day_label = f"Day {day_index}" if day_index and day_index > 0 else ""
    suffix = f"{day_label}, {date_label}" if day_label else date_label
    text = f":bar_chart: *{event_name} — {suffix}*"
    fallback = f"{event_name} — {suffix}"
    return [{"type": "section", "text": {"type": "mrkdwn", "text": text}}], fallback


def _ensure_day_anchor(
    *,
    channel_id: str,
    event_id: str,
    client_id: str,
    event_name: str,
    event_date: date_type,
    day_index: int,
) -> str | None:
    """Return the parent ts for ``event_date``, creating the anchor if absent.

    Looks up the stored parent ts first (so a mid-day restart reuses it). Only
    when none exists does it post a fresh top-level anchor and persist its ts.
    """
    date_iso = event_date.isoformat()
    existing = get_thread_anchor_ts(event_id, client_id, channel_id, date_iso)
    if existing:
        return existing

    blocks, fallback = _build_day_anchor_blocks(event_name, day_index, event_date)
    result = post_message(channel_id, blocks, fallback)
    parent_ts = result.get("ts")
    if not parent_ts:
        return None

    try:
        set_thread_anchor_ts(event_id, client_id, channel_id, date_iso, parent_ts)
    except Exception:
        # Lost a create race with a concurrent run; thread under the winner's
        # anchor instead of our now-orphaned one.
        winner = get_thread_anchor_ts(event_id, client_id, channel_id, date_iso)
        if winner:
            return winner
    return parent_ts


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
# Midnight day recap
# ---------------------------------------------------------------------------

def _is_midnight_recap_slot(now: datetime, client_tz: ZoneInfo) -> bool:
    """True on the first hourly run after midnight in the client's timezone."""
    return now.astimezone(client_tz).hour == 0


def _report_date_for_event_day(event_start: str, day_number: int, tz: ZoneInfo) -> str:
    """ISO date for a 1-based event day using the client's local calendar."""
    start = _parse_event_start(event_start)
    return (start + timedelta(days=day_number - 1)).isoformat()


def _query_cumulative_metrics(
    client_id: str,
    marketplaces: list[str],
    event_start: str,
    through_day: int,
    client_tz: ZoneInfo,
) -> list[MarketplaceMetrics]:
    """Sum full-day metrics for event days 1 through ``through_day``."""
    accumulated: dict[str, MarketplaceMetrics] = {}
    for day in range(1, through_day + 1):
        day_date = _report_date_for_event_day(event_start, day, client_tz)
        for m in _query_metrics(
            client_id, marketplaces, datetime.now(timezone.utc),
            report_date=day_date, full_day=True,
        ):
            if m.marketplace not in accumulated:
                accumulated[m.marketplace] = MarketplaceMetrics(
                    marketplace=m.marketplace,
                    currency=m.currency,
                    total_sales=0.0,
                    units=0,
                    spend=0.0,
                    ppc_sales=0.0,
                )
            acc = accumulated[m.marketplace]
            acc.total_sales += m.total_sales
            acc.units += m.units
            acc.spend += m.spend
            acc.ppc_sales += m.ppc_sales
    return [accumulated[m] for m in marketplaces if m in accumulated]


def _fetch_prior_yoy_metrics(
    prior_event_id: str | None,
    client_id: str,
    marketplaces: list[str],
    recap_day: int,
    client_tz: ZoneInfo,
) -> tuple[list[MarketplaceMetrics] | None, list[MarketplaceMetrics] | None]:
    """Prior-year single-day and cumulative metrics, or (None, None) if unlinked."""
    if not prior_event_id:
        return None, None
    prior_event = get_event(prior_event_id)
    if not prior_event:
        return None, None
    prior_start = prior_event.get("start_date", "")
    if not prior_start:
        return None, None

    now = datetime.now(timezone.utc)
    prior_date = _report_date_for_event_day(prior_start, recap_day, client_tz)
    prior_single = _query_metrics(
        client_id, marketplaces, now, report_date=prior_date, full_day=True,
    )
    prior_cumulative = (
        _query_cumulative_metrics(
            client_id, marketplaces, prior_start, recap_day, client_tz,
        )
        if recap_day >= 2
        else None
    )
    return prior_single, prior_cumulative


def _metrics_by_marketplace(metrics: list[MarketplaceMetrics] | None) -> dict[str, MarketplaceMetrics]:
    if not metrics:
        return {}
    return {m.marketplace: m for m in metrics}


def _format_yoy_suffix(
    current: float,
    prior: float | None,
    currency: str,
    *,
    is_pct: bool = False,
) -> str:
    if prior is None:
        return " _(YoY: —)_"
    if is_pct:
        return f" _(YoY: {format_percentage(prior)} {format_delta_bps(current, prior)})_"
    return f" _(YoY: {format_currency(prior, currency)} {format_delta(current, prior)})_"


def _format_marketplace_recap_lines(
    m: MarketplaceMetrics,
    prior: MarketplaceMetrics | None,
) -> list[str]:
    p = prior
    return [
        f"Spend: {format_currency(m.spend, m.currency)}{_format_yoy_suffix(m.spend, p.spend if p else None, m.currency)}",
        f"PPC Sales: {format_currency(m.ppc_sales, m.currency)}{_format_yoy_suffix(m.ppc_sales, p.ppc_sales if p else None, m.currency)}",
        f"ACoS: {format_percentage(m.acos)}{_format_yoy_suffix(m.acos, p.acos if p else None, m.currency, is_pct=True)}",
        f"Total Sales: {format_currency(m.total_sales, m.currency)}{_format_yoy_suffix(m.total_sales, p.total_sales if p else None, m.currency)}",
        f"TACoS: {format_percentage(m.tacos)}{_format_yoy_suffix(m.tacos, p.tacos if p else None, m.currency, is_pct=True)}",
    ]


def _build_recap_blocks(
    *,
    client_name: str,
    event_name: str,
    recap_day: int,
    now: datetime,
    client_tz: ZoneInfo,
    metrics: list[MarketplaceMetrics],
    prior_single: list[MarketplaceMetrics] | None,
    prior_cumulative: list[MarketplaceMetrics] | None,
    current_cumulative: list[MarketplaceMetrics] | None,
    base_currency: str,
) -> list[dict]:
    """Build Slack blocks for a completed-day recap with YoY comparisons."""
    time_str = _format_local_time(now, client_tz)
    subtitle = f"{time_str} | {event_name} — Day {recap_day} Recap"

    blocks: list[dict] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":bar_chart: *Day {recap_day} Recap — {client_name}*\n{subtitle}",
            },
        },
    ]

    prior_map = _metrics_by_marketplace(prior_single)
    for m in metrics:
        mkt_header = f"*{m.marketplace}*"
        mkt_tz_str = _MARKETPLACE_TIMEZONES.get(m.marketplace)
        if mkt_tz_str and mkt_tz_str != str(client_tz):
            mkt_tz = ZoneInfo(mkt_tz_str)
            mkt_header += f" ({_format_local_time(now, mkt_tz)})"

        lines = [mkt_header, *_format_marketplace_recap_lines(m, prior_map.get(m.marketplace))]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(lines)},
        })

    _maybe_add_recap_total_row(blocks, metrics, prior_single, base_currency)

    if recap_day >= 2 and current_cumulative:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Cumulative (Days 1–{recap_day})*",
            },
        })
        prior_cum_map = _metrics_by_marketplace(prior_cumulative)
        for m in current_cumulative:
            lines = [f"*{m.marketplace}*", *_format_marketplace_recap_lines(
                m, prior_cum_map.get(m.marketplace),
            )]
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": "\n".join(lines)},
            })
        _maybe_add_recap_total_row(blocks, current_cumulative, prior_cumulative, base_currency)

    return blocks


def _maybe_add_recap_total_row(
    blocks: list[dict],
    metrics: list[MarketplaceMetrics],
    prior_metrics: list[MarketplaceMetrics] | None,
    base_currency: str,
) -> None:
    """Total row with YoY for single-currency recaps."""
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

    prior: MarketplaceMetrics | None = None
    if prior_metrics and len({m.currency for m in prior_metrics}) == 1:
        prior = MarketplaceMetrics(
            marketplace="Total",
            currency=currency,
            total_sales=sum(m.total_sales for m in prior_metrics),
            units=sum(m.units for m in prior_metrics),
            spend=sum(m.spend for m in prior_metrics),
            ppc_sales=sum(m.ppc_sales for m in prior_metrics),
        )

    lines = [
        "*Total*",
        f"Spend: {format_currency(total_spend, currency)}{_format_yoy_suffix(total_spend, prior.spend if prior else None, currency)}",
        f"PPC Sales: {format_currency(total_ppc, currency)}{_format_yoy_suffix(total_ppc, prior.ppc_sales if prior else None, currency)}",
        f"ACoS: {format_percentage(acos)}{_format_yoy_suffix(acos, prior.acos if prior else None, currency, is_pct=True)}",
        f"Total Sales: {format_currency(total_sales, currency)}{_format_yoy_suffix(total_sales, prior.total_sales if prior else None, currency)}",
        f"TACoS: {format_percentage(tacos)}{_format_yoy_suffix(tacos, prior.tacos if prior else None, currency, is_pct=True)}",
    ]

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "\n".join(lines)},
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_event_start(event_start: str | date_type) -> date_type:
    if hasattr(event_start, "year"):
        return event_start  # type: ignore[return-value]
    return date_type.fromisoformat(event_start)


def _compute_day_index(event_start: str, now: datetime, tz: ZoneInfo) -> int:
    """Compute 1-based day index within the event using the client's local date."""
    try:
        start = _parse_event_start(event_start)
        local_today = now.astimezone(tz).date()
        return (local_today - start).days + 1
    except (ValueError, TypeError):
        return 0
