"""Daily Recap Bot — posts each client's previous-day account totals to Slack.

A year-round morning recap, independent of the hourly event bot and the
comparison "daily pulse". For every client whose bot config has
``daily_recap_enabled`` set, it:

  1. Computes the previous full calendar day in the client's configured timezone
  2. Sums that day's metrics across the client's marketplaces from BigQuery
     (Spend, PPC Sales, Total Sales — the same shared tables the hourly bot reads)
  3. Derives ACoS (Spend / PPC Sales) and TACoS (Spend / Total Sales)
  4. Posts a flat, single-day recap to the client's configured Slack channel

The message matches the hourly bot's visual style: a title block
(":bar_chart: Daily Recap — {client}" + a "{time} | {date}" subtitle), then
one block per marketplace (plain, non-bulleted metric lines — no
DoD/WoW/MoM/event-day indexing), and a combined *Total* block once a client
has 2+ marketplaces. The one comparison it does carry is an inline YoY suffix
on Spend, PPC Sales, and ACoS, sourced from a manually-loaded prior-year
reference table (see scripts/load_prior_year_reference.py) when one exists for
the client/marketplace/date; it's silently omitted otherwise.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import flask
from google.cloud import bigquery

from shared.db import (
    get_client,
    has_bot_activity,
    list_bot_configs,
    log_bot_activity,
)
from shared import metrics_repository
from shared.logging_setup import init_logging
from shared.slack_client import (
    format_currency,
    format_delta,
    format_delta_bps,
    format_percentage,
    get_channel_tag_block,
    post_message,
    resolve_target_channels,
)

logger = logging.getLogger(__name__)
init_logging("daily-recap")

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

        # Gate on the trigger window before doing any work for this client.
        # This function is invoked every 15 minutes (see
        # infra/resources/scheduler.py); each client only gets acted on once
        # it's inside its own 1-3h-past-local-midnight window, and only if
        # that day's recap hasn't already been sent — two independent gates
        # so a more-frequent trigger can't double-post. Checking this first —
        # before get_client() (a DB read) or resolve_target_channels() — means
        # a not-due client costs nothing beyond reading
        # config.get("client_timezone", ...): no DB read, no "No Slack
        # channel for client" warning logged on every 15-minute poll.
        client_tz = ZoneInfo(config.get("client_timezone", "America/Los_Angeles"))
        if not _is_due(now, client_tz):
            continue

        client = get_client(client_id)
        if not client or not client.get("is_active", True):
            continue

        target_channels = resolve_target_channels(config)
        if not target_channels:
            logger.warning("No Slack channel for client", extra={"client_id": client_id})
            continue

        marketplaces = config.get("marketplaces", [])
        if not marketplaces:
            continue

        recap_date = _previous_calendar_day(now, client_tz)
        currency = config.get("base_currency", "USD")

        report_date = recap_date.isoformat()
        if has_bot_activity("daily_recap", client_id, report_date):
            continue

        try:
            # Always recap the previous full calendar day (the spec's "previous
            # full calendar day") and query that exact day. The recap is
            # scheduled to run after the day's report pulls have ingested (see
            # infra/resources/scheduler.py), so yesterday's ads *and* orders are
            # present by the time this runs. Earlier revisions resolved "the most
            # recent day with any data", but that was dominated by the ads tables
            # (which ingest before the orders report), so the chosen day's orders
            # were still missing and Total Sales read $0 while ads were correct.

            # Query per marketplace (a single-element marketplaces list is the
            # same query today's single-marketplace clients already ran, so
            # this is a no-op refactor for them). A combined Total block is
            # only meaningful — and only shown — once there are 2+ marketplaces
            # to add together.
            per_marketplace_totals = {
                mkt: _query_account_totals(client_id, [mkt], report_date, client_tz)
                for mkt in marketplaces
            }
            per_marketplace_yoy = {
                mkt: _query_prior_year_totals(client_id, mkt, recap_date)
                for mkt in marketplaces
            }

            blocks: list[dict] = [
                _build_title_block(client.get("name", client_id), recap_date, now, client_tz),
            ]
            for mkt in marketplaces:
                blocks.append(_build_marketplace_block(
                    mkt, per_marketplace_totals[mkt], currency, now, client_tz,
                    yoy=per_marketplace_yoy[mkt],
                ))
            _maybe_add_total_block(blocks, per_marketplace_totals, per_marketplace_yoy, currency)

            text_fallback = f"Daily Recap — {recap_date.strftime('%m/%d/%y')}"

            # Per-client totals are logged so the recap day and each metric are
            # verifiable in Cloud Logging (e.g. confirming Total Sales is no
            # longer spuriously zero once orders have ingested). Logged as the
            # combined total across marketplaces, matching the pre-breakdown
            # log shape.
            combined = _sum_totals(per_marketplace_totals.values())
            logger.info(
                "Daily recap computed",
                extra={
                    "client_id": client_id,
                    "recap_date": report_date,
                    "spend": round(combined.spend, 2),
                    "ppc_sales": round(combined.ppc_sales, 2),
                    "total_sales": round(combined.total_sales, 2),
                },
            )

        except Exception as exc:
            # Building the recap failed (e.g. a BigQuery error) before any
            # channel was posted to.
            logger.exception("Failed to send daily recap", extra={"client_id": client_id})
            errors.append(f"{client_id}: {str(exc)[:100]}")
            log_bot_activity({
                "client_id": client_id,
                "bot": "daily_recap",
                "status": "failed",
                "channel_ids": target_channels,
                "recap_date": recap_date.isoformat(),
                "error": str(exc)[:500],
            })
            continue

        # Broadcast the same recap to every configured channel; one
        # channel's failure doesn't block delivery to the others.
        for channel_id in target_channels:
            try:
                tag_block = get_channel_tag_block(config, channel_id)
                channel_blocks = [tag_block] + blocks if tag_block else blocks
                result = post_message(channel_id, channel_blocks, text_fallback)
                log_bot_activity({
                    "client_id": client_id,
                    "bot": "daily_recap",
                    "status": "sent",
                    "channel_id": channel_id,
                    "message_ts": result.get("ts"),
                    "recap_date": report_date,
                    "marketplaces_reported": marketplaces,
                })
                sent += 1
            except Exception as exc:
                logger.exception("Failed to send daily recap", extra={"client_id": client_id, "channel_id": channel_id})
                errors.append(f"{client_id}: {str(exc)[:100]}")
                log_bot_activity({
                    "client_id": client_id,
                    "bot": "daily_recap",
                    "status": "failed",
                    "channel_id": channel_id,
                    "recap_date": report_date,
                    "error": str(exc)[:500],
                })

    if errors:
        logger.error(
            "Daily recap run completed with errors",
            extra={"sent": sent, "errors": len(errors), "error_code": "PARTIAL_FAILURE", "failures": errors[:20]},
        )
    else:
        logger.info("Daily recap run complete", extra={"sent": sent, "errors": 0})
    return {"status": "ok", "messages_sent": sent, "errors": len(errors)}, 200


# ---------------------------------------------------------------------------
# Date computation
# ---------------------------------------------------------------------------

def _previous_calendar_day(now: datetime, client_tz: ZoneInfo):
    """The previous full calendar day in the client's configured timezone."""
    return now.astimezone(client_tz).date() - timedelta(days=1)


# Trigger window: how long after a client's own local midnight the recap is
# allowed to fire. 1h buffer (down from an earlier fixed 23:00 UTC slot that
# gave Pacific clients up to ~16h of lag) — evidence from 2026-09-08 showed a
# client's data fully settled within ~40 minutes of its own midnight. The 2h
# window width (not a single instant) absorbs poll timing slop and, more
# importantly, a DST "spring forward" transition: computed from *absolute*
# elapsed time (aware-datetime subtraction, not a wall-clock hour reading),
# the window still produces a due instant on a day where the wall clock skips
# an hour entirely (see TestIsDue's DST tests).
_TRIGGER_WINDOW_START_HOURS = 1.0
_TRIGGER_WINDOW_END_HOURS = 3.0


def _hours_since_local_midnight(now: datetime, client_tz: ZoneInfo) -> float:
    """Absolute hours elapsed since local midnight today, in client_tz."""
    local_now = now.astimezone(client_tz)
    # Built via .replace() on the already-real `local_now` instance rather than
    # the module-level `datetime` constructor, so this keeps working when a
    # caller (e.g. handler(), in tests) patches `daily_recap.main.datetime` to
    # freeze "now" — that patch doesn't touch datetime *instances* already in
    # hand, only the module-level class name used to construct new ones.
    midnight_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    # Convert both to UTC before subtracting — do not "simplify" this by
    # subtracting local_now - midnight_local directly, even though it looks
    # redundant. `.replace()` preserves the original tzinfo *object* by
    # default, so midnight_local and local_now share the identical tzinfo
    # instance. Python's datetime.__sub__ takes a fast path whenever both
    # operands share that identical tzinfo object: it does naive wall-clock
    # subtraction and skips reconciling any UTC-offset difference between the
    # two instants. That's silently wrong across a DST transition, where the
    # UTC offset changes between local midnight and "now" but the wall-clock
    # subtraction has no way to see it. Converting both operands to
    # timezone.utc first defeats that shortcut: once both sides are already
    # in UTC, absolute-time subtraction and wall-clock subtraction are the
    # same operation, so the result is correct regardless of any DST shift
    # in between.
    utc_midnight = midnight_local.astimezone(timezone.utc)
    utc_now = local_now.astimezone(timezone.utc)
    return (utc_now - utc_midnight).total_seconds() / 3600


def _is_due(now: datetime, client_tz: ZoneInfo) -> bool:
    """True when `now` falls in the post-local-midnight trigger window."""
    hours = _hours_since_local_midnight(now, client_tz)
    return _TRIGGER_WINDOW_START_HOURS <= hours < _TRIGGER_WINDOW_END_HOURS


# ---------------------------------------------------------------------------
# BigQuery — sum the prior full calendar day across marketplaces
# ---------------------------------------------------------------------------

def _query_account_totals(
    client_id: str,
    marketplaces: list[str],
    report_date: str,
    client_tz: ZoneInfo,
) -> AccountTotals:
    """Sum Spend, PPC Sales, and Total Sales for ``report_date`` across marketplaces.

    Metrics are keyed off each row's **actual data date** — the campaign
    performance ``date`` for ads and the order ``purchase_date`` for sales — not
    the ingestion ``report_date`` partition. The ingestion ``report_date`` is the
    *start* of the report's pulled range, so a client whose report schedule uses a
    multi-day timeframe (``last_n_days``, ``rolling_window``, …) stamps every row
    with the same range-start date. Filtering on ``report_date`` would then match
    zero rows for the recap day and yield an all-zero recap.

    Because the same data date can be re-pulled under several overlapping ranges
    (each ingested under a different ``report_date``), the ads query keeps only the
    most-recently-ingested row per campaign bucket before summing, giving
    post-restatement totals without double counting. Orders are read from the
    ``orders_latest`` view (one row per (order id, sku), latest update wins), so a
    plain sum over the purchase-date window is correct even though ingestion is
    append-only.

    The recap day is bounded by midnight-to-midnight in the client's configured
    timezone, converted to UTC for the ``purchase_date`` (TIMESTAMP) comparison.
    """
    if os.environ.get("METRICS_BACKEND", "bigquery") == "supabase":
        totals = metrics_repository.get_account_totals(client_id, marketplaces, report_date, client_tz)
        return AccountTotals(**totals)

    dataset = os.environ.get("BQ_DATASET", "")
    project = os.environ.get("GCP_PROJECT", "")
    bq = _get_bq()

    day_start_utc, day_end_utc = _day_bounds_utc(report_date, client_tz)

    orders = _query_orders_total(
        bq, project, dataset, client_id, marketplaces, day_start_utc, day_end_utc,
    )
    ads = _query_ads_total(bq, project, dataset, client_id, marketplaces, report_date)

    return AccountTotals(
        spend=ads.get("spend", 0.0),
        ppc_sales=ads.get("ppc_sales", 0.0),
        total_sales=orders.get("total_sales", 0.0),
    )


def _day_bounds_utc(report_date: str, client_tz: ZoneInfo) -> tuple[str, str]:
    """UTC [start, end) timestamps for ``report_date`` as a full day in ``client_tz``."""
    day = date.fromisoformat(report_date)
    start_local = datetime(day.year, day.month, day.day, tzinfo=client_tz)
    end_local = start_local + timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (
        start_local.astimezone(timezone.utc).strftime(fmt),
        end_local.astimezone(timezone.utc).strftime(fmt),
    )


def _query_orders_total(
    bq: bigquery.Client,
    project: str,
    dataset: str,
    client_id: str,
    marketplaces: list[str],
    day_start_utc: str,
    day_end_utc: str,
) -> dict[str, Any]:
    query = f"""
        SELECT COALESCE(SUM(item_price), 0) AS total_sales
        FROM `{project}.{dataset}.orders_latest`
        WHERE client_id = @client_id
          AND purchase_date >= @day_start
          AND purchase_date < @day_end
          AND order_status != 'Cancelled'
          AND marketplace IN UNNEST(@marketplaces)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
            bigquery.ScalarQueryParameter("day_start", "TIMESTAMP", day_start_utc),
            bigquery.ScalarQueryParameter("day_end", "TIMESTAMP", day_end_utc),
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
    # Keep only the most-recently-ingested row per (marketplace, campaign) for the
    # data date, so overlapping re-pulls (each a distinct report_date partition)
    # don't double count.
    unions = []
    for table, sales_col in tables.items():
        unions.append(f"""
            SELECT cost, ppc_sales FROM (
                SELECT
                    cost,
                    {sales_col} AS ppc_sales,
                    ROW_NUMBER() OVER (
                        PARTITION BY marketplace, campaign_id
                        ORDER BY ingested_at DESC
                    ) AS _rn
                FROM `{project}.{dataset}.{table}`
                WHERE client_id = @client_id
                  AND date = @report_date
                  AND marketplace IN UNNEST(@marketplaces)
            )
            WHERE _rn = 1
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
# YoY baseline — manually-loaded prior-year reference data
# ---------------------------------------------------------------------------

def _query_prior_year_totals(
    client_id: str, marketplace: str, recap_date: date,
) -> AccountTotals | None:
    """Same-calendar-date-last-year totals, or None when no baseline exists.

    Reads ``ads_prior_year_reference`` (see scripts/load_prior_year_reference.py)
    — a manually-loaded Amazon Ads export, not part of the normal ingestion
    pipeline. It only has Spend/PPC Sales/Purchases/Units sold, never an
    account-wide Total Sales, so ``total_sales`` is always 0 on the returned
    ``AccountTotals`` and the caller must never show a Total Sales/TACoS YoY
    from it. Returns None (rather than raising) on a missing row, a
    year-that-doesn't-exist edge case (e.g. Feb 29), or any query failure —
    a client with no reference data loaded yet is the normal case, not an
    error, and the recap must still send without YoY.
    """
    try:
        prior_date = recap_date.replace(year=recap_date.year - 1)
    except ValueError:
        return None

    project = os.environ.get("GCP_PROJECT", "")
    dataset = os.environ.get("BQ_DATASET", "")
    query = f"""
        SELECT spend, ppc_sales
        FROM `{project}.{dataset}.ads_prior_year_reference`
        WHERE client_id = @client_id AND marketplace = @marketplace AND date = @date
        LIMIT 1
    """
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
        bigquery.ScalarQueryParameter("marketplace", "STRING", marketplace),
        bigquery.ScalarQueryParameter("date", "DATE", prior_date.isoformat()),
    ])
    try:
        for row in _get_bq().query(query, job_config=job_config):
            return AccountTotals(spend=float(row["spend"]), ppc_sales=float(row["ppc_sales"]), total_sales=0.0)
    except Exception:
        logger.warning(
            "Prior-year reference query failed — sending recap without YoY",
            extra={"client_id": client_id, "marketplace": marketplace, "prior_date": prior_date.isoformat()},
        )
    return None


# ---------------------------------------------------------------------------
# Slack message formatting
# ---------------------------------------------------------------------------

def _section(text: str) -> dict:
    """A mrkdwn section block with ``expand: True`` so it's never collapsed
    behind Slack's "Show more" toggle, matching the hourly bot's convention."""
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}, "expand": True}


def _yoy_currency_suffix(prior: float | None, current: float, currency: str) -> str:
    """Inline YoY suffix for a currency metric, or "" when no baseline exists."""
    if prior is None:
        return ""
    return f" _(YoY: {format_currency(prior, currency)} {format_delta(current, prior)})_"


def _yoy_pct_suffix(prior: float | None, current: float) -> str:
    """Inline YoY suffix for a percentage metric (ACoS), or "" when no baseline exists."""
    if prior is None:
        return ""
    return f" _(YoY: {format_percentage(prior)} {format_delta_bps(current, prior)})_"


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
    """Format time like '6 PM PST' (or '6 PM PDT' while daylight saving is in
    effect). Duplicated from the hourly bot (functions/slack_bot/main.py) —
    each Cloud Function's deploy bundles only its own directory + shared/, so
    the two bots can't import each other's code, only shared/ (see design
    note on ``_maybe_add_total_block`` for the same reasoning). Strips the
    leading-zero hour manually rather than via the hourly bot's ``%-I``
    (a glibc-only strftime extension that raises ValueError on Windows,
    though never on the Linux Cloud Functions runtime this actually deploys
    to) so this copy also runs cleanly in local/Windows dev and tests.
    """
    local = now.astimezone(tz)
    hour = local.strftime("%I %p").lstrip("0")
    live_name = local.tzname() or ""
    tz_name = live_name if live_name and live_name[0] not in "+-" else _TZ_ABBREVIATIONS.get(str(tz), str(tz))
    return f"{hour} {tz_name}"


def _build_title_block(client_name: str, recap_date, now: datetime, client_tz: ZoneInfo) -> dict:
    """Title + subtitle, matching the hourly bot's header style.

    daily_recap has no event/day-index concept (it's year-round, not tied to
    an event), so the subtitle is just "{time posted} | {recap date}" rather
    than the hourly bot's "{time} | {event_name} — Day N".
    """
    time_str = _format_local_time(now, client_tz)
    subtitle = f"{time_str} | {recap_date.strftime('%m/%d/%y')}"
    return _section(f":bar_chart: *Daily Recap — {client_name}*\n{subtitle}")


def _metric_lines(totals: AccountTotals, currency: str, yoy: AccountTotals | None) -> list[str]:
    """The five plain (non-bulleted) metric lines, matching the hourly bot's style."""
    return [
        f"Spend: {format_currency(totals.spend, currency)}"
        f"{_yoy_currency_suffix(yoy.spend if yoy else None, totals.spend, currency)}",
        f"PPC Sales: {format_currency(totals.ppc_sales, currency)}"
        f"{_yoy_currency_suffix(yoy.ppc_sales if yoy else None, totals.ppc_sales, currency)}",
        f"ACoS: {format_percentage(totals.acos)}"
        f"{_yoy_pct_suffix(yoy.acos if yoy else None, totals.acos)}",
        f"Total Sales: {format_currency(totals.total_sales, currency)}",
        f"TACoS: {format_percentage(totals.tacos)}",
    ]


def _build_marketplace_block(
    marketplace: str,
    totals: AccountTotals,
    currency: str,
    now: datetime,
    client_tz: ZoneInfo,
    *,
    yoy: AccountTotals | None = None,
) -> dict:
    """One marketplace's block: a bold marketplace header (with its own local
    time when it differs from the client's) plus five plain metric lines.

    ``yoy`` — the same-calendar-date-last-year totals — adds an inline YoY
    comparison to Spend, PPC Sales, and ACoS only. Total Sales and TACoS never
    get one: the prior-year reference data is a manually-loaded Ads export
    (see scripts/load_prior_year_reference.py), which has no account-wide
    order total to compare against, only ad-attributed figures.
    """
    header = f"*{marketplace}*"
    mkt_tz_str = _MARKETPLACE_TIMEZONES.get(marketplace)
    if mkt_tz_str and mkt_tz_str != str(client_tz):
        header += f" ({_format_local_time(now, ZoneInfo(mkt_tz_str))})"

    lines = [header, *_metric_lines(totals, currency, yoy)]
    return _section("\n".join(lines))


def _sum_totals(totals: Iterable[AccountTotals]) -> AccountTotals:
    """Combine several marketplaces' totals into one (for logging/Total row)."""
    totals = list(totals)
    return AccountTotals(
        spend=sum(t.spend for t in totals),
        ppc_sales=sum(t.ppc_sales for t in totals),
        total_sales=sum(t.total_sales for t in totals),
    )


def _maybe_add_total_block(
    blocks: list[dict],
    per_marketplace_totals: dict[str, AccountTotals],
    per_marketplace_yoy: dict[str, AccountTotals | None],
    currency: str,
) -> None:
    """Append a combined *Total* block across marketplaces, when there's more than one.

    A single-marketplace account has nothing to total — its Total would just
    repeat that one marketplace's own numbers — so this is a no-op below 2,
    matching the hourly bot's existing ``_maybe_add_total_row`` convention.

    The Total's own YoY is only shown when *every* marketplace has a prior-year
    baseline — a partial sum (some marketplaces compared, others not) would
    misrepresent the comparison rather than just omit it.
    """
    if len(per_marketplace_totals) <= 1:
        return

    total = _sum_totals(per_marketplace_totals.values())

    yoy_by_mkt = per_marketplace_yoy.values()
    total_yoy = _sum_totals(yoy_by_mkt) if all(v is not None for v in yoy_by_mkt) else None

    lines = ["*Total*", *_metric_lines(total, currency, total_yoy)]
    blocks.append({"type": "divider"})
    blocks.append(_section("\n".join(lines)))
