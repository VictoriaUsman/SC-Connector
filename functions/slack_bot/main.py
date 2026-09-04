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

from shared.currency import convert, get_rates
from shared.db import (
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
    SlackApiError,
    format_currency,
    format_delta,
    format_delta_bps,
    format_percentage,
    get_channel_tag_block,
    post_message,
    resolve_target_channels,
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


@dataclass
class SkuMetrics:
    """Cumulative day-to-date units and total sales for a single SKU."""

    sku: str
    units: int
    total_sales: float
    ly_units: int | None = None
    ly_total_sales: float | None = None


# Half-cent tolerance so currency rounding noise (e.g. total == ppc within a
# fraction of a cent) does not trip the invariant; only real violations fail.
_INVARIANT_TOLERANCE = 0.005

# Accounts that get a per-SKU breakdown appended to their hourly drop. Gated by
# the same identifier the bot config already uses (client_id). Skylight and
# Ritual run several regional accounts (e.g. skylight-frame-uk), so a family
# match (exact id or "{family}-..." suffix) covers every marketplace account
# without listing each one. Overridable via the SKU_BREAKDOWN_CLIENT_IDS env var
# (comma-separated families) so the allowlist can change without a code deploy.
_DEFAULT_SKU_BREAKDOWN_CLIENTS = ("skylight-frame", "ritual")

# Cent-level tolerance for the SKU↔account reconciliation: per-SKU sales are
# summed in a different grouping than the account total, so floating-point
# accumulation can differ by a fraction of a cent. Units must match exactly.
_SKU_RECONCILE_TOLERANCE = 0.01

# Slack hard-caps a section block's text at 3000 chars; keep a margin so a long
# SKU list is split across several blocks instead of being rejected/truncated.
_SKU_BLOCK_CHAR_BUDGET = 2800


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


def _sku_breakdown_families() -> tuple[str, ...]:
    """Return the account families that get a per-SKU breakdown appended.

    Reads ``SKU_BREAKDOWN_CLIENT_IDS`` (comma-separated) when set, otherwise the
    built-in default (Skylight + Ritual).
    """
    raw = os.environ.get("SKU_BREAKDOWN_CLIENT_IDS", "")
    families = tuple(part.strip() for part in raw.split(",") if part.strip())
    return families or _DEFAULT_SKU_BREAKDOWN_CLIENTS


def _sku_breakdown_enabled(client_id: str) -> bool:
    """True if ``client_id`` belongs to an allowlisted account family.

    Matches the exact id or any regional variant (``"{family}-..."``), so e.g.
    ``skylight-frame-uk`` is covered by the ``skylight-frame`` family. This is the
    legacy default; prefer ``_sku_breakdown_enabled_for_config`` which also honors
    the per-customer UI toggle.
    """
    return any(
        client_id == family or client_id.startswith(f"{family}-")
        for family in _sku_breakdown_families()
    )


def _sku_breakdown_enabled_for_config(config: dict) -> bool:
    """Whether a bot config gets the per-SKU breakdown appended.

    The per-customer ``sku_breakdown_enabled`` toggle (set in the Slack Bots UI)
    turns the breakdown on for any customer that requires it. It is OR-ed with the
    legacy family allowlist (Skylight/Ritual, env-overridable via
    ``SKU_BREAKDOWN_CLIENT_IDS``) so allowlisted accounts keep working before the
    toggle is set — the allowlist is a transitional default and can be retired
    once their configs are toggled on.
    """
    return bool(config.get("sku_breakdown_enabled")) or _sku_breakdown_enabled(
        config.get("client_id", "")
    )


# Marketplaces that get their own per-SKU breakdown in a combined drop, in
# display order (US first, then CA, UK below it). Each marketplace's section
# reconciles to its own single-currency account line. Overridable via the
# SKU_BREAKDOWN_MARKETPLACES env var (comma-separated) so the set can change
# mid-event without a code deploy.
_DEFAULT_SKU_BREAKDOWN_MARKETPLACES = ("US", "CA", "UK")


def _sku_breakdown_marketplaces() -> tuple[str, ...]:
    """Return the ordered marketplaces that get a per-SKU breakdown.

    Reads ``SKU_BREAKDOWN_MARKETPLACES`` (comma-separated) when set, otherwise
    the built-in default (US, CA, UK).
    """
    raw = os.environ.get("SKU_BREAKDOWN_MARKETPLACES", "")
    mkts = tuple(part.strip().upper() for part in raw.split(",") if part.strip())
    return mkts or _DEFAULT_SKU_BREAKDOWN_MARKETPLACES


# ---------------------------------------------------------------------------
# Automatic multi-marketplace account grouping
# ---------------------------------------------------------------------------
# Several accounts split one brand across marketplaces using a trailing
# marketplace-code suffix on the client_id (e.g. ``moxe`` + ``moxe-ca``,
# ``skylight-frame`` + ``skylight-frame-uk``). We group those back into one
# "account family" so a single combined Slack message can span every
# marketplace. Only a *known marketplace* suffix is stripped — ``-vc`` (vendor
# central) and any other suffix (e.g. ``leonisa-pr``) stay their own account.

_MARKETPLACE_SUFFIXES = frozenset({
    "us", "ca", "mx", "uk", "de", "fr", "it", "es", "nl", "se", "pl", "tr", "au", "sg",
})


def account_family(client_id: str) -> str:
    """Derive the account family by stripping a trailing ``-{marketplace}``.

    The suffix is only stripped when it is a known marketplace code, so
    ``moxe-ca`` -> ``moxe`` but ``candy-kittens-vc`` and ``leonisa-pr`` are left
    intact (they are distinct accounts, not marketplace splits of a family).
    """
    head, sep, tail = client_id.rpartition("-")
    if sep and head and tail.lower() in _MARKETPLACE_SUFFIXES:
        return head
    return client_id


def _group_enabled_by_family(configs: list[dict]) -> dict[str, list[dict]]:
    """Group enabled bot configs by derived account family, preserving order."""
    families: dict[str, list[dict]] = {}
    for config in configs:
        families.setdefault(account_family(config["client_id"]), []).append(config)
    return families


def _family_marketplaces(members: list[dict]) -> set[str]:
    """Union of marketplaces across every member of a family."""
    mkts: set[str] = set()
    for member in members:
        mkts.update(member.get("marketplaces", []))
    return mkts


def _representative_member(members: list[dict]) -> dict:
    """Pick the family's representative config (drives base_currency + anchor).

    Prefers a member covering the US marketplace; ties (and US-less families)
    break on the smallest client_id so the choice is deterministic.
    """
    us_members = [m for m in members if "US" in m.get("marketplaces", [])]
    pool = us_members or members
    return min(pool, key=lambda m: m["client_id"])


def _load_currency_rates() -> dict[str, float]:
    """Fetch FX rates for combined Totals, never raising into the run loop."""
    try:
        return get_rates()
    except Exception:
        logger.warning(
            "FX rate load failed — combined Totals will be skipped this run",
            extra={"phase": "currency", "error_code": "FX_UNAVAILABLE"},
        )
        return {}


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

    # Multi-marketplace accounts (e.g. moxe + moxe-ca) are grouped by family so
    # one combined message can span every marketplace. A family is posted once;
    # ``handled_families`` guards against re-posting on later members.
    families = _group_enabled_by_family(enabled)
    handled_families: set[str] = set()

    # FX rates (USD-based, cached) are only needed to convert combined
    # multi-currency Totals — load them once, and only when a multi-marketplace
    # family actually exists this run. An empty mapping just skips those Totals.
    rates: dict[str, float] = {}
    if any(len(_family_marketplaces(members)) > 1 for members in families.values()):
        rates = _load_currency_rates()

    sent = 0
    errors: list[str] = []
    invariant_skips: list[str] = []
    # Channel-config failures (the Kalilos app isn't in the channel, or it was
    # deleted/archived). These are operator-fixable misconfigurations, not bot
    # defects, so they're tracked apart from real errors and don't page.
    channel_skips: list[str] = []

    for config in enabled:
        client_id = config["client_id"]
        client = get_client(client_id)
        if not client or not client.get("is_active", True):
            continue

        client_name = client.get("name", client_id)
        client_tz_str = config.get("client_timezone", "America/Los_Angeles")
        client_tz = ZoneInfo(client_tz_str)
        target_channels = resolve_target_channels(config)
        if not target_channels:
            logger.warning("No Slack channel for client", extra={"client_id": client_id})
            continue

        marketplaces = config.get("marketplaces", [])
        if not marketplaces:
            continue

        day_index = _compute_day_index(event_start, now, client_tz)
        recap_day = day_index - 1 if day_index >= 2 else 0
        is_midnight_recap = _is_midnight_recap_slot(now, client_tz) and recap_day >= 1

        # Combine a multi-marketplace family into one hourly message. The
        # midnight recap stays strictly per-config (one recap per account), so
        # combining only applies to the regular hourly slot. The family is
        # posted exactly once per run; later members short-circuit here.
        family_id = account_family(client_id)
        members = families.get(family_id, [config])
        combined = (not is_midnight_recap) and len(_family_marketplaces(members)) > 1
        if combined:
            if family_id in handled_families:
                continue
            handled_families.add(family_id)

        try:
            marketplaces_reported = marketplaces
            anchor_client_id = client_id
            if combined:
                rep = _representative_member(members)
                rep_client = get_client(rep["client_id"])
                rep_name = rep_client.get("name", rep["client_id"]) if rep_client else rep["client_id"]
                base_currency = rep.get("base_currency", config.get("base_currency", "USD"))
                metrics = _combined_metrics(members, now)
                _check_total_sales_invariant(metrics)
                anchor_date = now.astimezone(client_tz).date()
                anchor_day = day_index
                blocks = _build_message_blocks(
                    client_name=rep_name,
                    event_name=event_name,
                    day_index=day_index,
                    now=now,
                    client_tz=client_tz,
                    metrics=metrics,
                    base_currency=base_currency,
                    rates=rates,
                )
                # Skylight/Ritual only: append per-marketplace SKU breakdowns
                # (US, then CA, then UK), each reconciled to its own line.
                _maybe_append_combined_sku_breakdown(
                    blocks, members, now, metrics,
                    prior_event_id=live_event.get("prior_event_id"),
                    event_day=day_index,
                    client_tz=client_tz,
                )
                text_fallback = f"Hourly Update — {rep_name} | {event_name}"
                marketplaces_reported = sorted(_family_marketplaces(members))
                anchor_client_id = rep["client_id"]
            elif is_midnight_recap:
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
                # Per-SKU breakdown (per-customer toggle / legacy allowlist) for
                # the completed recap day. The recap is per-account (single
                # marketplace/currency), so the existing single-currency
                # reconciliation in _append_sku_breakdown applies directly —
                # full_day reconciles to this recap's full-day metrics.
                if _sku_breakdown_enabled_for_config(config):
                    _append_sku_breakdown(
                        blocks, client_id, marketplaces, now, metrics,
                        label=marketplaces[0] if len(marketplaces) == 1 else None,
                        report_date=recap_date, full_day=True,
                        prior_event_id=live_event.get("prior_event_id"),
                        event_day=recap_day,
                        client_tz=client_tz,
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
                # Per-SKU breakdown (per-customer toggle / legacy allowlist).
                # Purely additive — leaves the account-level blocks untouched.
                if _sku_breakdown_enabled_for_config(config):
                    _append_sku_breakdown(
                        blocks, client_id, marketplaces, now, metrics,
                        prior_event_id=live_event.get("prior_event_id"),
                        event_day=day_index,
                        client_tz=client_tz,
                    )
                text_fallback = f"Hourly Update — {client_name} | {event_name}"

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
                "channel_ids": target_channels,
                "error": f"Total Sales invariant violated: {str(exc)[:500]}",
            })
            continue

        except Exception as exc:
            # Message building failed (e.g. a BigQuery error) before any
            # channel was posted to.
            logger.exception("Failed to send hourly bot message", extra={"client_id": client_id})
            errors.append(f"{client_id}: {str(exc)[:100]}")
            log_bot_activity({
                "client_id": client_id,
                "event_id": live_event["id"],
                "status": "failed",
                "channel_ids": target_channels,
                "error": str(exc)[:500],
            })
            continue

        # Broadcast the same message to every configured channel. Each
        # channel gets its own thread anchor and is isolated from failures in
        # the others — one bad channel (e.g. the app was never invited) never
        # blocks delivery to the rest.
        for channel_id in target_channels:
            try:
                # One thin parent message per channel per event-day acts as
                # the thread anchor; every update for that day (hourly + the
                # next-morning recap), from every account/marketplace posting
                # to this channel, lands as a threaded reply under it. The
                # anchor is created lazily on the first update of the day and
                # reused thereafter, so neither a mid-day restart nor sibling
                # accounts in the same channel ever spawn a duplicate parent.
                parent_ts = _ensure_day_anchor(
                    channel_id=channel_id,
                    event_id=live_event["id"],
                    client_id=anchor_client_id,
                    event_name=event_name,
                    event_date=anchor_date,
                    day_index=anchor_day,
                )
                tag_block = get_channel_tag_block(config, channel_id)
                channel_blocks = [tag_block] + blocks if tag_block else blocks
                result = post_message(channel_id, channel_blocks, text_fallback, thread_ts=parent_ts)

                log_bot_activity({
                    "client_id": anchor_client_id,
                    "event_id": live_event["id"],
                    "status": "sent",
                    "channel_id": channel_id,
                    "message_ts": result.get("ts"),
                    "parent_ts": parent_ts,
                    "marketplaces_reported": marketplaces_reported,
                })
                sent += 1

            except SlackApiError as exc:
                if exc.is_channel_config_error:
                    # The Kalilos app isn't a member of this channel (or it
                    # was deleted/archived) — usually because someone
                    # switched the config's channel (e.g. toggled
                    # use_test_channel) to one the app was never invited to.
                    # Nothing the bot can self-heal; the operator must
                    # re-invite the app. Surface it as an actionable WARNING
                    # (not a paging ERROR) with the exact channel to fix.
                    logger.warning(
                        "Slack channel not postable — invite the Kalilos app to the channel",
                        extra={
                            "client_id": client_id,
                            "channel_id": channel_id,
                            "phase": "slack_post",
                            "error_code": exc.code.upper(),
                            "remedy": f"Invite the Kalilos app to channel {channel_id} "
                                      f"(/invite @Kalilos), or fix the channel in the bot config.",
                        },
                    )
                    channel_skips.append(f"{client_id}: {exc.code} ({channel_id})")
                    log_bot_activity({
                        "client_id": client_id,
                        "event_id": live_event["id"],
                        "status": "failed",
                        "channel_id": channel_id,
                        "error_code": exc.code,
                        "error": f"Slack channel not joined: {exc.code} ({channel_id}). "
                                 f"Invite the Kalilos app to the channel.",
                    })
                else:
                    logger.exception(
                        "Slack API error sending hourly bot message",
                        extra={"client_id": client_id, "channel_id": channel_id,
                               "phase": "slack_post", "error_code": exc.code.upper()},
                    )
                    errors.append(f"{client_id}: {exc.code}")
                    log_bot_activity({
                        "client_id": client_id,
                        "event_id": live_event["id"],
                        "status": "failed",
                        "channel_id": channel_id,
                        "error_code": exc.code,
                        "error": str(exc)[:500],
                    })

            except Exception as exc:
                logger.exception("Failed to send hourly bot message", extra={"client_id": client_id})
                errors.append(f"{client_id}: {str(exc)[:100]}")
                log_bot_activity({
                    "client_id": client_id,
                    "event_id": live_event["id"],
                    "status": "failed",
                    "channel_id": channel_id,
                    "error": str(exc)[:500],
                })

    if errors:
        logger.error(
            "Hourly bot run completed with errors",
            extra={
                "sent": sent,
                "errors": len(errors),
                "invariant_skips": len(invariant_skips),
                "channel_skips": len(channel_skips),
                "error_code": "PARTIAL_FAILURE",
                "failures": errors[:20],
                "channel_config_failures": channel_skips[:20],
            },
        )
    elif channel_skips:
        logger.warning(
            "Hourly bot run completed with channel-config skips (app not in channel)",
            extra={
                "sent": sent,
                "channel_skips": len(channel_skips),
                "invariant_skips": len(invariant_skips),
                "error_code": "CHANNEL_NOT_JOINED",
                "channel_config_failures": channel_skips[:20],
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
        "channel_skips": len(channel_skips),
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
    manual_ads: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> list[MarketplaceMetrics]:
    """Query BQ for orders and ads data, returning per-marketplace metrics.

    When ``report_date`` is set, queries that calendar day (used for day-end
    recaps). ``full_day=True`` uses the complete report_date partition without
    filtering orders to purchases since midnight (hourly updates use partial day).

    ``manual_ads`` lets the caller supply operator-provided ads figures keyed by
    ``{marketplace: {YYYY-MM-DD: {"spend": float, "ppc_sales": float}}}``. When a
    matching ``(marketplace, date)`` entry exists it overrides the BigQuery ads
    query. This is how prior-year YoY ads are populated for events older than
    Amazon Ads' ~95-day reporting window, which the API can no longer pull.
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
        ads = _manual_ads_lookup(manual_ads, mkt, mkt_date)
        if ads is None:
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


def _marketplace_day_window(
    marketplace: str,
    now: datetime,
    *,
    report_date: str | None = None,
    full_day: bool = False,
    through: datetime | None = None,
) -> tuple[str, str]:
    """UTC ``[start, end)`` timestamps for a marketplace-local day.

    ``full_day=True`` returns the complete calendar day given by ``report_date``
    (used by day-end recaps). Otherwise it returns the live day-to-date window —
    marketplace-local midnight today up to next midnight, derived from ``now``
    (used by hourly updates). ``through`` caps the hourly end at that instant
    (prior-year YoY: same event-day through the current hour-of-day, so last
    year's already-complete afternoon is not compared against this year's
    still-running morning). Both the account orders query and the per-SKU query
    call this, so their windows are provably identical and the SKU rows reconcile
    to the account total by construction.
    """
    from shared.config import MARKETPLACE_TIMEZONES

    mkt_tz = ZoneInfo(MARKETPLACE_TIMEZONES.get(marketplace, "America/Los_Angeles"))
    if full_day:
        day = date_type.fromisoformat(report_date)
        start_local = datetime(day.year, day.month, day.day, tzinfo=mkt_tz)
    else:
        start_local = now.astimezone(mkt_tz).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
    if through is not None and not full_day:
        end_local = through.astimezone(mkt_tz)
    else:
        end_local = start_local + timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (
        start_local.astimezone(timezone.utc).strftime(fmt),
        end_local.astimezone(timezone.utc).strftime(fmt),
    )


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

    Total Sales is additionally scoped to the marketplace's own storefront via
    the ``sales_channel`` column. The All Orders report is account-wide — Amazon
    returns every order for the seller's region regardless of the marketplaceId
    requested — and ingestion stamps all of those rows with the single
    marketplace it pulled under. Filtering only on that stamped ``marketplace``
    therefore leaked other marketplaces' orders into a per-marketplace Total
    Sales (e.g. a EU account's DE/FR/IT orders inflating the UK figure). The
    ``sales_channel`` is the only per-row signal of an order's true marketplace,
    so we keep the ``marketplace`` partition filter (it de-dupes the
    ``orders_latest`` view, which is keyed by marketplace) *and* restrict to the
    rows whose storefront matches this marketplace. Unknown marketplaces fall
    back to the unscoped behaviour so we never zero out a real total.
    """
    from shared.config import get_marketplace_sales_channel

    sales_channel = get_marketplace_sales_channel(marketplace)
    sales_channel_clause = (
        "\n              AND LOWER(sales_channel) = @sales_channel"
        if sales_channel
        else ""
    )

    day_start_utc, day_end_utc = _marketplace_day_window(
        marketplace, now, report_date=report_date, full_day=full_day,
    )

    query = f"""
        SELECT
            COALESCE(SUM(item_price), 0) AS total_sales,
            COALESCE(SUM(quantity), 0) AS units
        FROM `{project}.{dataset}.orders_latest`
        WHERE client_id = @client_id
          AND purchase_date >= @day_start
          AND purchase_date < @day_end
          AND order_status != 'Cancelled'
          AND marketplace = @marketplace{sales_channel_clause}
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
            bigquery.ScalarQueryParameter("day_start", "TIMESTAMP", day_start_utc),
            bigquery.ScalarQueryParameter("day_end", "TIMESTAMP", day_end_utc),
            bigquery.ScalarQueryParameter("marketplace", "STRING", marketplace),
            *(
                [bigquery.ScalarQueryParameter("sales_channel", "STRING", sales_channel.lower())]
                if sales_channel
                else []
            ),
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

    ``report_date`` is the marketplace-local calendar day (YYYY-MM-DD) we want
    metrics for.

    The metrics are keyed off each campaign row's actual performance ``date``,
    **not** the ingestion ``report_date`` partition. The ``report_date`` column
    is the *start* of a report's pulled range, so any pull that is not a
    single-day pull for exactly that day (a multi-day range, or the prior-year
    backfill that fetches a whole event window in one pull) stamps rows with a
    range-start that differs from the data date. Filtering on ``report_date``
    then matched zero rows — the reported "last year's data shows 0" bug, since
    the linked prior-year event is backfilled as one multi-day pull. This
    mirrors the proven-correct ``daily_recap`` bot.

    Because the same performance ``date`` can be re-pulled under several
    overlapping ranges (each ingested under a different ``report_date``), keep
    only the most-recently-ingested row per (marketplace, campaign) before
    summing, so overlapping re-pulls don't double count.
    """
    tables = {
        "sp_campaigns": "sales7d",
        "sb_campaigns": "sales",
        "sd_campaigns": "sales",
    }

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
                  AND date = @perf_date
                  AND marketplace = @marketplace
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
            bigquery.ScalarQueryParameter("perf_date", "DATE", report_date),
            bigquery.ScalarQueryParameter("marketplace", "STRING", marketplace),
        ]
    )
    for row in bq.query(query, job_config=job_config):
        return {
            "spend": float(row["spend"]),
            "ppc_sales": float(row["ppc_sales"]),
        }
    return {}


def _manual_ads_lookup(
    manual_ads: dict[str, dict[str, dict[str, Any]]] | None,
    marketplace: str,
    report_date: str,
) -> dict[str, Any] | None:
    """Return operator-supplied ads for a marketplace/date, or None if unset.

    ``manual_ads`` is keyed ``{marketplace: {YYYY-MM-DD: {spend, ppc_sales}}}``.
    A present entry takes precedence over BigQuery so prior-year ads beyond
    Amazon's ~95-day reporting window can be filled in by hand.
    """
    if not manual_ads:
        return None
    entry = manual_ads.get(marketplace, {}).get(report_date)
    if not entry:
        return None
    return {
        "spend": float(entry.get("spend", 0.0) or 0.0),
        "ppc_sales": float(entry.get("ppc_sales", 0.0) or 0.0),
    }


# ---------------------------------------------------------------------------
# Per-SKU breakdown (cumulative day-to-date)
# ---------------------------------------------------------------------------

def _query_sku_orders(
    bq: bigquery.Client,
    project: str,
    dataset: str,
    client_id: str,
    marketplace: str,
    now: datetime,
    *,
    report_date: str | None = None,
    full_day: bool = False,
    through: datetime | None = None,
) -> list[SkuMetrics]:
    """Per-SKU units and total sales for a marketplace day, decomposing the
    account orders sum.

    Reads the SAME table (``orders_latest``), the SAME purchase-date window (via
    the shared ``_marketplace_day_window`` — the live day-to-date window for
    hourly drops, or the full calendar day for ``full_day`` recaps), and the SAME
    filters as ``_query_orders`` — non-cancelled, this client + marketplace, AND
    the same ``sales_channel`` storefront scoping — only adding ``GROUP BY sku``.
    The ``sales_channel`` clause must mirror ``_query_orders`` exactly: the All
    Orders report is account-wide, so without it the SKU rollup would include
    other-storefront lines the account total excludes, breaking reconciliation
    (e.g. a non-amazon.com $0 line adding a phantom unit). Because the row set is
    identical, ``SUM`` over the SKU groups equals the ungrouped account total by
    construction, so the breakdown reconciles to the figure already shown without
    a second independent pull.
    """
    from shared.config import get_marketplace_sales_channel

    sales_channel = get_marketplace_sales_channel(marketplace)
    sales_channel_clause = (
        "\n          AND LOWER(sales_channel) = @sales_channel"
        if sales_channel
        else ""
    )
    day_start_utc, day_end_utc = _marketplace_day_window(
        marketplace, now, report_date=report_date, full_day=full_day,
        through=through,
    )

    query = f"""
        SELECT
            COALESCE(sku, '(unknown)') AS sku,
            COALESCE(SUM(item_price), 0) AS total_sales,
            COALESCE(SUM(quantity), 0) AS units
        FROM `{project}.{dataset}.orders_latest`
        WHERE client_id = @client_id
          AND purchase_date >= @day_start
          AND purchase_date < @day_end
          AND order_status != 'Cancelled'
          AND marketplace = @marketplace{sales_channel_clause}
        GROUP BY sku
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("client_id", "STRING", client_id),
            bigquery.ScalarQueryParameter("day_start", "TIMESTAMP", day_start_utc),
            bigquery.ScalarQueryParameter("day_end", "TIMESTAMP", day_end_utc),
            bigquery.ScalarQueryParameter("marketplace", "STRING", marketplace),
            *(
                [bigquery.ScalarQueryParameter("sales_channel", "STRING", sales_channel.lower())]
                if sales_channel
                else []
            ),
        ]
    )
    rows: list[SkuMetrics] = []
    for row in bq.query(query, job_config=job_config):
        rows.append(SkuMetrics(
            sku=str(row["sku"]),
            units=int(row["units"]),
            total_sales=float(row["total_sales"]),
        ))
    return rows


def _query_sku_breakdown(
    client_id: str,
    marketplaces: list[str],
    now: datetime,
    *,
    report_date: str | None = None,
    full_day: bool = False,
    through: datetime | None = None,
) -> list[SkuMetrics]:
    """Account-level per-SKU rollup, aggregated across ``marketplaces``.

    Mirrors how the drop's account total sums each marketplace, so the SKU rows
    sum to that same total. ``report_date``/``full_day`` are forwarded so a
    day-end recap can roll up a completed calendar day instead of the live
    day-to-date window. Sorted by sales descending; every SKU with activity is
    returned (no top-N cap).
    """
    dataset = os.environ.get("BQ_DATASET", "")
    project = os.environ.get("GCP_PROJECT", "")
    bq = _get_bq()

    by_sku: dict[str, SkuMetrics] = {}
    for mkt in marketplaces:
        for row in _query_sku_orders(
            bq, project, dataset, client_id, mkt, now,
            report_date=report_date, full_day=full_day, through=through,
        ):
            agg = by_sku.get(row.sku)
            if agg is None:
                by_sku[row.sku] = SkuMetrics(
                    sku=row.sku, units=row.units, total_sales=row.total_sales,
                )
            else:
                agg.units += row.units
                agg.total_sales += row.total_sales
    return sorted(
        by_sku.values(), key=lambda s: (s.total_sales, s.units), reverse=True,
    )


def _sku_breakdown_reconciles(
    sku_rows: list[SkuMetrics],
    metrics: list[MarketplaceMetrics],
) -> bool:
    """True when SKU rows sum exactly to the account total shown in the drop.

    Units must match exactly; sales may differ by at most one cent (float
    accumulation across a different grouping). A mismatch means the two derived
    from different data, so the breakdown is suppressed rather than posting
    numbers that contradict the account line.
    """
    sku_units = sum(s.units for s in sku_rows)
    sku_sales = sum(s.total_sales for s in sku_rows)
    acct_units = sum(m.units for m in metrics)
    acct_sales = sum(m.total_sales for m in metrics)
    return (
        sku_units == acct_units
        and abs(sku_sales - acct_sales) <= _SKU_RECONCILE_TOLERANCE
    )


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
    return [_section(text)], fallback


def _ensure_day_anchor(
    *,
    channel_id: str,
    event_id: str,
    client_id: str,
    event_name: str,
    event_date: date_type,
    day_index: int,
) -> str | None:
    """Return the parent ts for ``(channel, event_date)``, creating it if absent.

    The anchor is shared per channel per day (not per client), so every account/
    marketplace posting to the same channel threads under one daily parent.
    Looks up the stored parent ts first (so a mid-day restart, or a later
    account in the same channel, reuses it). Only when none exists does it post a
    fresh top-level anchor and persist its ts. ``client_id`` is used only for
    logging / recording which account first created the day's anchor.
    """
    date_iso = event_date.isoformat()
    # Stable correlation fields so a single grep proves "one parent per
    # event-day, reused on every later run/restart" from the logs alone.
    log_ctx = {
        "phase": "thread_anchor",
        "event_id": event_id,
        "client_id": client_id,
        "channel_id": channel_id,
        "event_date": date_iso,
        "day_index": day_index,
    }
    existing = get_thread_anchor_ts(event_id, channel_id, date_iso)
    if existing:
        logger.info(
            "Reusing existing day anchor (no new parent posted)",
            extra={**log_ctx, "anchor_action": "reuse", "parent_ts": existing},
        )
        return existing

    blocks, fallback = _build_day_anchor_blocks(event_name, day_index, event_date)
    result = post_message(channel_id, blocks, fallback)
    parent_ts = result.get("ts")
    if not parent_ts:
        logger.warning(
            "Day anchor post returned no ts — update will post top-level",
            extra={**log_ctx, "anchor_action": "create_no_ts"},
        )
        return None

    try:
        set_thread_anchor_ts(
            event_id, channel_id, date_iso, parent_ts,
            created_by_client_id=client_id,
        )
        logger.info(
            "Created day anchor (new parent message)",
            extra={**log_ctx, "anchor_action": "create", "parent_ts": parent_ts},
        )
    except Exception:
        # Lost a create race with a concurrent run (another account in the same
        # channel, or a concurrent function instance); thread under the winner's
        # anchor instead of our now-orphaned one.
        winner = get_thread_anchor_ts(event_id, channel_id, date_iso)
        logger.warning(
            "Lost day-anchor create race — threading under the winner's parent",
            extra={
                **log_ctx,
                "anchor_action": "create_race_lost",
                "orphaned_ts": parent_ts,
                "winner_ts": winner,
            },
        )
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


def _section(text: str) -> dict:
    """A mrkdwn section block that always renders fully.

    ``expand: True`` disables Slack's per-block truncation, so a tall block (e.g.
    a per-marketplace line group or a combined drop) is never collapsed behind a
    "Show more"/"Show less" toggle.
    """
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}, "expand": True}


def _build_message_blocks(
    *,
    client_name: str,
    event_name: str,
    day_index: int,
    now: datetime,
    client_tz: ZoneInfo,
    metrics: list[MarketplaceMetrics],
    base_currency: str,
    rates: dict[str, float] | None = None,
) -> list[dict]:
    """Build Slack Block Kit blocks for the hourly update."""
    time_str = _format_local_time(now, client_tz)
    day_str = f"Day {day_index}" if day_index > 0 else ""
    subtitle = f"{time_str} | {event_name}"
    if day_str:
        subtitle += f" — {day_str}"

    blocks: list[dict] = [
        _section(f":zap: *Hourly Update — {client_name}*\n{subtitle}"),
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

        blocks.append(_section("\n".join(lines)))

    _maybe_add_total_row(blocks, metrics, base_currency, rates)

    return blocks


def _maybe_add_total_row(
    blocks: list[dict],
    metrics: list[MarketplaceMetrics],
    base_currency: str,
    rates: dict[str, float] | None = None,
) -> None:
    """Append a Total row, converting to ``base_currency`` when needed.

    Single-currency families keep the native Total (today's behaviour). A
    multi-currency family converts each marketplace's spend/ppc/sales into the
    representative ``base_currency`` using live FX rates, sums, and recomputes
    ACoS/TACoS — showing only the converted number (no rate annotation). If any
    required FX pair is unavailable the Total is skipped (logged
    ``MISSING_FX_RATE``) rather than mixing currencies or assuming 1:1.

    A single-marketplace account has nothing to total — its Total would just
    repeat that one marketplace's own numbers — so this is a no-op below 2.
    """
    if len(metrics) <= 1:
        return

    currencies = {m.currency for m in metrics}
    if len(currencies) == 1:
        currency = currencies.pop()
        total_spend = sum(m.spend for m in metrics)
        total_ppc = sum(m.ppc_sales for m in metrics)
        total_sales = sum(m.total_sales for m in metrics)
    else:
        converted = _convert_totals(metrics, base_currency, rates or {})
        if converted is None:
            logger.warning(
                "Skipping combined Total — missing FX rate for one or more marketplaces",
                extra={
                    "phase": "currency",
                    "error_code": "MISSING_FX_RATE",
                    "base_currency": base_currency,
                    "currencies": sorted(currencies),
                },
            )
            return
        currency = base_currency
        total_spend, total_ppc, total_sales = converted

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
    blocks.append(_section("\n".join(lines)))


def _convert_totals(
    metrics: list[MarketplaceMetrics],
    base_currency: str,
    rates: dict[str, float],
) -> tuple[float, float, float] | None:
    """Sum spend/ppc/total_sales converted into ``base_currency``.

    Returns ``None`` if any marketplace's currency can't be converted (missing
    rate), so the caller skips the Total entirely instead of under-counting.
    """
    if not rates:
        return None
    total_spend = total_ppc = total_sales = 0.0
    for m in metrics:
        spend = convert(m.spend, m.currency, base_currency, rates)
        ppc = convert(m.ppc_sales, m.currency, base_currency, rates)
        sales = convert(m.total_sales, m.currency, base_currency, rates)
        if spend is None or ppc is None or sales is None:
            return None
        total_spend += spend
        total_ppc += ppc
        total_sales += sales
    return total_spend, total_ppc, total_sales


def _clock_on_date(now: datetime, report_date: str, tz: ZoneInfo) -> datetime:
    """Same clock time as ``now``, moved onto ``report_date`` in ``tz``."""
    local = now.astimezone(tz)
    day = date_type.fromisoformat(report_date)
    return datetime(
        day.year, day.month, day.day,
        local.hour, local.minute, local.second, local.microsecond,
        tzinfo=tz,
    )


def _resolve_prior_event_date(
    prior_event_id: str | None,
    event_day: int,
    client_tz: ZoneInfo,
) -> str | None:
    """ISO date of the linked prior event's equivalent event-day, or None."""
    if not prior_event_id or event_day < 1:
        return None
    prior = get_event(prior_event_id)
    if not prior:
        return None
    prior_start = prior.get("start_date", "")
    if not prior_start:
        return None
    return _report_date_for_event_day(prior_start, event_day, client_tz)


def _attach_sku_yoy(sku_rows: list[SkuMetrics], ly_rows: list[SkuMetrics]) -> None:
    by_sku = {row.sku: row for row in ly_rows}
    for row in sku_rows:
        prior = by_sku.get(row.sku)
        if prior is None:
            continue
        row.ly_units = prior.units
        row.ly_total_sales = prior.total_sales


def _format_sku_yoy(current: float, prior: float | None, formatted_prior: str) -> str:
    """Inline YoY: `` (LY 280, +14%)`` or `` (new)`` when there is no baseline."""
    if prior is None or prior == 0:
        return " (new)"
    pct = ((current - prior) / abs(prior)) * 100
    sign = "+" if pct >= 0 else ""
    return f" (LY {formatted_prior}, {sign}{pct:.0f}%)"


def _sku_breakdown_line(s: SkuMetrics, currency: str, *, yoy: bool) -> str:
    units = f"{s.units:,} units"
    sales = format_currency(s.total_sales, currency)
    if yoy:
        units += _format_sku_yoy(
            float(s.units),
            None if s.ly_units is None else float(s.ly_units),
            f"{s.ly_units:,}" if s.ly_units is not None else "",
        )
        sales += _format_sku_yoy(
            s.total_sales,
            s.ly_total_sales,
            format_currency(s.ly_total_sales, currency) if s.ly_total_sales is not None else "",
        )
    return f"`{s.sku}` — {units} · {sales}"


def _build_sku_breakdown_blocks(
    sku_rows: list[SkuMetrics],
    currency: str,
    label: str | None = None,
    *,
    yoy: bool = False,
) -> list[dict]:
    """Build the appended per-SKU breakdown blocks (header + chunked rows).

    Lists every SKU (no top-N cap). Rows are split across multiple section
    blocks so a long catalog never exceeds Slack's per-block character limit.
    When ``label`` is set (e.g. a marketplace code) it is shown in the header so
    several stacked breakdowns in one drop are distinguishable.
    """
    suffix = f" ({label})" if label else ""
    header = f"*Per-SKU Breakdown{suffix} — Day to Date (cumulative)*"
    line_strs = [_sku_breakdown_line(s, currency, yoy=yoy) for s in sku_rows]

    blocks: list[dict] = [
        {"type": "divider"},
        _section(header),
    ]

    chunk: list[str] = []
    chunk_len = 0
    for line in line_strs:
        # +1 accounts for the joining newline.
        if chunk and chunk_len + len(line) + 1 > _SKU_BLOCK_CHAR_BUDGET:
            blocks.append(_section("\n".join(chunk)))
            chunk = []
            chunk_len = 0
        chunk.append(line)
        chunk_len += len(line) + 1
    if chunk:
        blocks.append(_section("\n".join(chunk)))
    return blocks


def _append_sku_breakdown(
    blocks: list[dict],
    client_id: str,
    marketplaces: list[str],
    now: datetime,
    metrics: list[MarketplaceMetrics],
    label: str | None = None,
    *,
    report_date: str | None = None,
    full_day: bool = False,
    prior_event_id: str | None = None,
    event_day: int = 0,
    client_tz: ZoneInfo | None = None,
) -> None:
    """Append a per-SKU breakdown to a drop (in place).

    Strictly additive: the existing account-level blocks are never touched. The
    breakdown is appended only when (a) the drop reports a single currency — so
    summed per-SKU sales have one unit and reconcile to the Total row — and
    (b) the SKU rows reconcile to that account total. Any miss (no SKUs, mixed
    currency, reconciliation failure, or query error) leaves the drop unchanged.

    ``label`` is forwarded to the header so stacked per-marketplace breakdowns
    in one combined drop are distinguishable. ``report_date``/``full_day`` are
    forwarded to the query so a day-end recap reconciles to a completed calendar
    day instead of the live day-to-date window.

    When ``prior_event_id`` is set, each SKU line also gets last-year units and
    sales for the equivalent event-day (hourly: through the current hour-of-day;
    recap: the full day). A SKU with no prior-year row renders as new / no-comp.
    """
    currencies = {m.currency for m in metrics}
    if len(currencies) != 1:
        # Multi-currency drops show no single account total to reconcile to.
        return
    currency = currencies.pop()

    try:
        sku_rows = _query_sku_breakdown(
            client_id, marketplaces, now,
            report_date=report_date, full_day=full_day,
        )
    except Exception:
        logger.exception(
            "Per-SKU breakdown query failed — posting drop without it",
            extra={"client_id": client_id, "phase": "sku_breakdown"},
        )
        return

    if not sku_rows:
        return

    if not _sku_breakdown_reconciles(sku_rows, metrics):
        logger.warning(
            "Per-SKU breakdown does not reconcile to account total — suppressing",
            extra={
                "client_id": client_id,
                "phase": "sku_breakdown",
                "error_code": "SKU_RECONCILE_MISMATCH",
                "sku_units": sum(s.units for s in sku_rows),
                "account_units": sum(m.units for m in metrics),
            },
        )
        return

    yoy = _maybe_attach_sku_yoy(
        sku_rows, client_id, marketplaces, now,
        prior_event_id=prior_event_id, event_day=event_day,
        client_tz=client_tz, full_day=full_day,
    )
    blocks.extend(_build_sku_breakdown_blocks(sku_rows, currency, label=label, yoy=yoy))


def _maybe_attach_sku_yoy(
    sku_rows: list[SkuMetrics],
    client_id: str,
    marketplaces: list[str],
    now: datetime,
    *,
    prior_event_id: str | None,
    event_day: int,
    client_tz: ZoneInfo | None,
    full_day: bool,
) -> bool:
    """Attach LY units/sales onto ``sku_rows``. True when YoY should render."""
    if not prior_event_id or client_tz is None:
        return False
    try:
        ly_date = _resolve_prior_event_date(prior_event_id, event_day, client_tz)
        if not ly_date:
            return False
        ly_now = _clock_on_date(now, ly_date, client_tz)
        ly_rows = _query_sku_breakdown(
            client_id, marketplaces, ly_now,
            report_date=ly_date, full_day=full_day,
            through=None if full_day else ly_now,
        )
    except Exception:
        logger.exception(
            "Prior-year SKU query failed — posting breakdown without YoY",
            extra={"client_id": client_id, "phase": "sku_yoy"},
        )
        return False
    _attach_sku_yoy(sku_rows, ly_rows)
    return True


# ---------------------------------------------------------------------------
# Combined multi-marketplace message
# ---------------------------------------------------------------------------

def _combined_metrics(members: list[dict], now: datetime) -> list[MarketplaceMetrics]:
    """Concatenate each active family member's per-marketplace metrics.

    Each member owns its own client_id and BigQuery data, so metrics are queried
    per member (with that member's marketplaces) and concatenated into one list.
    Inactive clients and members without marketplaces contribute nothing.
    """
    metrics: list[MarketplaceMetrics] = []
    for member in members:
        client = get_client(member["client_id"])
        if not client or not client.get("is_active", True):
            continue
        member_marketplaces = member.get("marketplaces", [])
        if not member_marketplaces:
            continue
        metrics.extend(_query_metrics(member["client_id"], member_marketplaces, now))
    return metrics


def _maybe_append_combined_sku_breakdown(
    blocks: list[dict],
    members: list[dict],
    now: datetime,
    metrics: list[MarketplaceMetrics],
    *,
    prior_event_id: str | None = None,
    event_day: int = 0,
    client_tz: ZoneInfo | None = None,
) -> None:
    """Append per-marketplace SKU breakdowns for a toggled/allowlisted family.

    For each marketplace in ``_sku_breakdown_marketplaces()`` (US, CA, UK by
    default, in display order) that the family covers, append a breakdown
    reconciled to that marketplace's own single-currency account line (USD for
    US, CAD for CA, GBP for UK). Each marketplace is owned by its own regional
    member account (e.g. ``skylight-frame-ca`` for CA), so the SKU rows for that
    member reconcile to the matching row in the combined drop. The breakdown is
    gated per member by ``_sku_breakdown_enabled_for_config`` (per-customer toggle
    or legacy allowlist). Members without it enabled, marketplaces the family
    doesn't cover, and any reconciliation miss are silently skipped — every
    section is strictly additive and independent.
    """
    metrics_by_mkt = {m.marketplace: m for m in metrics}
    for mkt in _sku_breakdown_marketplaces():
        member = next((m for m in members if mkt in m.get("marketplaces", [])), None)
        if not member or not _sku_breakdown_enabled_for_config(member):
            continue
        row = metrics_by_mkt.get(mkt)
        if row is None:
            continue
        _append_sku_breakdown(
            blocks, member["client_id"], [mkt], now, [row], label=mkt,
            prior_event_id=prior_event_id, event_day=event_day, client_tz=client_tz,
        )


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
    manual_ads: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> list[MarketplaceMetrics]:
    """Sum full-day metrics for event days 1 through ``through_day``."""
    accumulated: dict[str, MarketplaceMetrics] = {}
    for day in range(1, through_day + 1):
        day_date = _report_date_for_event_day(event_start, day, client_tz)
        for m in _query_metrics(
            client_id, marketplaces, datetime.now(timezone.utc),
            report_date=day_date, full_day=True, manual_ads=manual_ads,
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

    # Operator-supplied ads for the prior-year event fill the YoY ads metrics
    # (Spend / PPC Sales / ACoS) when Amazon Ads can no longer serve that
    # history (its reporting API only retains ~95 days). Orders/Total Sales
    # still come from BigQuery (SP-API retains ~2 years).
    manual_ads = prior_event.get("manual_ads") or None

    now = datetime.now(timezone.utc)
    prior_date = _report_date_for_event_day(prior_start, recap_day, client_tz)
    prior_single = _query_metrics(
        client_id, marketplaces, now, report_date=prior_date, full_day=True,
        manual_ads=manual_ads,
    )
    prior_cumulative = (
        _query_cumulative_metrics(
            client_id, marketplaces, prior_start, recap_day, client_tz,
            manual_ads=manual_ads,
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
        _section(f":bar_chart: *Day {recap_day} Recap — {client_name}*\n{subtitle}"),
    ]

    prior_map = _metrics_by_marketplace(prior_single)
    for m in metrics:
        mkt_header = f"*{m.marketplace}*"
        mkt_tz_str = _MARKETPLACE_TIMEZONES.get(m.marketplace)
        if mkt_tz_str and mkt_tz_str != str(client_tz):
            mkt_tz = ZoneInfo(mkt_tz_str)
            mkt_header += f" ({_format_local_time(now, mkt_tz)})"

        lines = [mkt_header, *_format_marketplace_recap_lines(m, prior_map.get(m.marketplace))]
        blocks.append(_section("\n".join(lines)))

    _maybe_add_recap_total_row(blocks, metrics, prior_single, base_currency)

    if recap_day >= 2 and current_cumulative:
        blocks.append({"type": "divider"})
        blocks.append(_section(f"*Cumulative (Days 1–{recap_day})*"))
        prior_cum_map = _metrics_by_marketplace(prior_cumulative)
        for m in current_cumulative:
            lines = [f"*{m.marketplace}*", *_format_marketplace_recap_lines(
                m, prior_cum_map.get(m.marketplace),
            )]
            blocks.append(_section("\n".join(lines)))
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
    blocks.append(_section("\n".join(lines)))


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
