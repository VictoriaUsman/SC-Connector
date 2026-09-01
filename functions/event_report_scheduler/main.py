"""Event Report Scheduler — triggers report pulls during active events.

Invoked at :20 and :50 past the hour by Cloud Scheduler — timed so a fresh sync
lands before the hourly Slack bot posts at :05. When no event is live, returns
immediately (no-op).  During an active event:
  - Both slots (:20 and :50): launch All Orders report workflows for each enabled
    client-marketplace (orders refresh every 30 min)
  - The :20 slot only: also launch Ads campaign report workflows (ads refresh
    hourly with ~45 min lead, since the ads workflow is heavier/slower to ingest)
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

import flask

from shared.db import (
    get_client,
    get_event,
    get_live_event,
    list_bot_configs,
    list_events,
    update_event,
)
from shared.schedule_compute import compute_report_dates, marketplace_today
from shared.workflow_launcher import (
    build_payload,
    client_has_credentials,
    get_workflow_parent,
    launch_execution,
)
from shared.db import create_job
from shared.logging_setup import init_logging

logger = logging.getLogger(__name__)
init_logging("event-report-scheduler")

ORDERS_REPORT = "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL"
ADS_REPORTS = ["spCampaigns", "sbCampaigns", "sdCampaigns"]

_STAGGER_SECONDS = 0.5

# Prior-year (YoY) backfill: when the live event is linked to a prior-year event,
# the midnight recap compares against that event's equivalent days. But the only
# mechanism that pulls event data (this scheduler) runs solely for the *live*
# event's current day, so the linked prior-year event's data was never ingested
# into BigQuery and the recap's YoY queries returned 0 (the reported bug). When a
# prior event is linked, pull its full window once so the YoY data exists.
PRIOR_YEAR_SYNC_FIELD = "prior_year_synced_for"
# All Orders "by last update" stamps rows by last-update time; extend the pull's
# end so orders purchased late in the prior window (but updated a little after)
# are still captured.
ORDERS_BACKFILL_END_BUFFER_DAYS = 3
# Amazon Ads reporting only serves a limited history (~95 days). Skip the ads
# backfill when the prior event is older than that — those report requests would
# only fail — while still backfilling orders (SP-API retains ~2 years), so
# Total Sales / TACoS YoY still populate.
ADS_BACKFILL_MAX_AGE_DAYS = 90
# Defensive cap so an over-long linked event can't fan out an unbounded backfill.
PRIOR_YEAR_MAX_WINDOW_DAYS = 45


def handler(request: flask.Request) -> tuple[dict, int]:
    now = datetime.now(timezone.utc)
    today = now.date()

    _auto_transition_events(today)

    live_event = get_live_event()
    if not live_event:
        logger.info("No active event — skipping")
        return {"status": "ok", "launched": 0}, 200

    # Ads pull once per hour on the earlier (:20) slot, giving the heavier ads
    # workflow ~45 min to ingest before the :05 Slack send; orders pull on both
    # the :20 and :50 slots. The minute<30 split keeps ads on :20, not :50.
    is_ads_slot = now.minute < 30
    configs = list_bot_configs()
    enabled_configs = [c for c in configs if c.get("hourly_bot", {}).get("enabled")]

    if not enabled_configs:
        logger.info("No enabled bot configs — skipping")
        return {"status": "ok", "launched": 0}, 200

    parent = get_workflow_parent()
    launched = 0
    errors: list[dict[str, str]] = []

    for config in enabled_configs:
        client_id = config["client_id"]
        client = get_client(client_id)
        if not client or not client.get("is_active", True):
            continue

        marketplaces = config.get("marketplaces", [])
        for marketplace in marketplaces:
            execution_date = marketplace_today(marketplace, now).isoformat()
            today_str = execution_date

            if client_has_credentials(client, "sp_api"):
                try:
                    job_id = _launch_report(
                        parent, client_id, marketplace, ORDERS_REPORT,
                        "sp_api", today_str, execution_date, live_event["id"],
                    )
                    launched += 1
                except Exception as exc:
                    logger.exception("Failed to launch orders report", extra={
                        "client_id": client_id, "marketplace": marketplace,
                    })
                    errors.append({"client_id": client_id, "error": str(exc)[:200]})

            if is_ads_slot and client_has_credentials(client, "ads_api"):
                for report_type in ADS_REPORTS:
                    try:
                        job_id = _launch_report(
                            parent, client_id, marketplace, report_type,
                            "ads_api", today_str, execution_date, live_event["id"],
                        )
                        launched += 1
                    except Exception as exc:
                        logger.exception("Failed to launch ads report", extra={
                            "client_id": client_id, "report_type": report_type,
                        })
                        errors.append({"client_id": client_id, "error": str(exc)[:200]})

            time.sleep(_STAGGER_SECONDS)

    try:
        launched += _maybe_backfill_prior_year(parent, live_event, enabled_configs, now)
    except Exception:
        logger.exception("Prior-year backfill failed", extra={
            "event_id": live_event.get("id"),
        })

    logger.info("Event report scheduler complete", extra={
        "event": live_event.get("name"),
        "launched": launched,
        "errors": len(errors),
    })
    return {"status": "ok", "launched": launched, "errors": len(errors)}, 200


def _launch_report(
    parent: str,
    client_id: str,
    marketplace: str,
    report_type: str,
    api_source: str,
    report_date: str,
    execution_date: str,
    event_id: str,
    *,
    report_end_date: str | None = None,
    frequency: str = "event",
    trigger: str = "event_scheduler",
) -> str:
    """Create a job and launch a workflow execution for a single report.

    When ``report_end_date`` is set, the report spans the ``[report_date,
    report_end_date]`` range (used by the prior-year backfill); otherwise it is a
    single-day pull for ``report_date``.
    """
    from datetime import date as date_type
    start = date_type.fromisoformat(report_date)
    end = date_type.fromisoformat(report_end_date) if report_end_date else None
    report_params = compute_report_dates(marketplace, api_source, start, end)

    job_id = create_job({
        "client_id": client_id,
        "api_source": api_source,
        "marketplace": marketplace,
        "report_type": report_type,
        "frequency": frequency,
        "report_date": report_date,
        "report_end_date": report_end_date,
        "execution_date": execution_date,
        "trigger": trigger,
        "event_id": event_id,
    })

    payload = build_payload(
        api_source=api_source,
        client_id=client_id,
        marketplace=marketplace,
        report_type=report_type,
        report_params=report_params,
        job_id=job_id,
        frequency=frequency,
        folder_name="",
        subfolder_strategy="date",
        execution_date=execution_date,
        report_date=report_date,
        report_end_date=report_end_date,
    )

    launch_execution(parent, payload, job_id, error_phase=trigger)
    return job_id


def _maybe_backfill_prior_year(
    parent: str,
    live_event: dict[str, Any],
    enabled_configs: list[dict[str, Any]],
    now: datetime,
) -> int:
    """Backfill the linked prior-year event's data into BigQuery (once per link).

    The midnight recap compares the live event's day(s) against the linked
    prior-year event's equivalent day(s), reading both from BigQuery. Nothing
    ingests the prior-year event's data, though — this scheduler only pulls the
    *live* event's current day — so the YoY queries found no rows and rendered
    "0"/"—". When an event is linked to a prior-year event, pull that event's
    full window once (orders always; ads only when recent enough to still be
    served by Amazon) so the recap's YoY queries have data to read.

    Idempotent: records the synced prior-event id on the live event and skips on
    subsequent runs, re-running only if the link changes.
    """
    prior_event_id = live_event.get("prior_event_id")
    if not prior_event_id:
        return 0
    if live_event.get(PRIOR_YEAR_SYNC_FIELD) == prior_event_id:
        return 0

    prior_event = get_event(prior_event_id)
    if not prior_event:
        logger.warning("Linked prior-year event not found — skipping backfill", extra={
            "event_id": live_event.get("id"), "prior_event_id": prior_event_id,
        })
        return 0

    try:
        prior_start = date.fromisoformat(prior_event.get("start_date", ""))
        prior_end = date.fromisoformat(
            prior_event.get("end_date") or prior_event.get("start_date", "")
        )
    except (ValueError, TypeError):
        logger.warning("Prior-year event has invalid dates — skipping backfill", extra={
            "event_id": live_event.get("id"), "prior_event_id": prior_event_id,
        })
        return 0

    if prior_end < prior_start:
        prior_end = prior_start
    # Defensive cap on the backfill window.
    max_end = prior_start + timedelta(days=PRIOR_YEAR_MAX_WINDOW_DAYS - 1)
    if prior_end > max_end:
        prior_end = max_end

    orders_start = prior_start.isoformat()
    orders_end = (prior_end + timedelta(days=ORDERS_BACKFILL_END_BUFFER_DAYS)).isoformat()
    ads_start = prior_start.isoformat()
    ads_end = prior_end.isoformat()

    # Ads reporting only serves a limited history; skip ads when out of range so
    # we don't fan out doomed report requests, but still backfill orders.
    age_days = (now.date() - prior_end).days
    include_ads = 0 <= age_days <= ADS_BACKFILL_MAX_AGE_DAYS

    launched = 0
    for config in enabled_configs:
        client_id = config["client_id"]
        client = get_client(client_id)
        if not client or not client.get("is_active", True):
            continue

        for marketplace in config.get("marketplaces", []):
            execution_date = marketplace_today(marketplace, now).isoformat()

            if client_has_credentials(client, "sp_api"):
                try:
                    _launch_report(
                        parent, client_id, marketplace, ORDERS_REPORT, "sp_api",
                        orders_start, execution_date, prior_event_id,
                        report_end_date=orders_end,
                        frequency="event_prior_year",
                        trigger="prior_year_backfill",
                    )
                    launched += 1
                except Exception:
                    logger.exception("Failed to backfill prior-year orders", extra={
                        "client_id": client_id, "marketplace": marketplace,
                        "prior_event_id": prior_event_id,
                    })

            if include_ads and client_has_credentials(client, "ads_api"):
                for report_type in ADS_REPORTS:
                    try:
                        _launch_report(
                            parent, client_id, marketplace, report_type, "ads_api",
                            ads_start, execution_date, prior_event_id,
                            report_end_date=ads_end,
                            frequency="event_prior_year",
                            trigger="prior_year_backfill",
                        )
                        launched += 1
                    except Exception:
                        logger.exception("Failed to backfill prior-year ads", extra={
                            "client_id": client_id, "report_type": report_type,
                            "prior_event_id": prior_event_id,
                        })

            time.sleep(_STAGGER_SECONDS)

    # Record the link as synced even if individual launches failed, so we don't
    # re-fan-out the whole backfill every run; re-link to retry.
    update_event(live_event["id"], {
        PRIOR_YEAR_SYNC_FIELD: prior_event_id,
        "prior_year_synced_at": now,
    })
    logger.info("Prior-year backfill launched", extra={
        "event_id": live_event.get("id"),
        "prior_event_id": prior_event_id,
        "launched": launched,
        "include_ads": include_ads,
        "window": f"{ads_start}..{ads_end}",
    })
    return launched


def _auto_transition_events(today: date) -> None:
    """Transition events: upcoming -> live on start_date, live -> completed after end_date."""
    for event in list_events():
        status = event.get("status", "upcoming")
        start = event.get("start_date", "")
        end = event.get("end_date", "")

        try:
            start_date = date.fromisoformat(start) if isinstance(start, str) else start
            end_date = date.fromisoformat(end) if isinstance(end, str) else end
        except (ValueError, TypeError):
            continue

        if status == "upcoming" and start_date <= today:
            update_event(event["id"], {
                "status": "live",
                "activated_at": datetime.now(timezone.utc),
            })
            logger.info("Event auto-activated", extra={"event_id": event["id"]})

        elif status == "live" and end_date < today:
            update_event(event["id"], {"status": "completed"})
            logger.info("Event auto-completed", extra={"event_id": event["id"]})
