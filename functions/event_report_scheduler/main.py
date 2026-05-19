"""Event Report Scheduler — triggers report pulls during active events.

Invoked every 30 minutes by Cloud Scheduler. When no event is live, returns
immediately (no-op).  During an active event:
  - Every 30 min: launch All Orders report workflows for each enabled client-marketplace
  - Every 60 min (on the hour): launch Ads campaign report workflows
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Any

import flask

from shared.firestore_utils import (
    get_client,
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
from shared.firestore_utils import create_job

logger = logging.getLogger(__name__)

ORDERS_REPORT = "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL"
ADS_REPORTS = ["spCampaigns", "sbCampaigns", "sdCampaigns"]

_STAGGER_SECONDS = 0.5


def handler(request: flask.Request) -> tuple[dict, int]:
    now = datetime.now(timezone.utc)
    today = now.date()

    _auto_transition_events(today)

    live_event = get_live_event()
    if not live_event:
        logger.info("No active event — skipping")
        return {"status": "ok", "launched": 0}, 200

    is_on_the_hour = now.minute < 30
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

            if is_on_the_hour and client_has_credentials(client, "ads_api"):
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
) -> str:
    """Create a job and launch a workflow execution for a single report."""
    from datetime import date as date_type
    today = date_type.fromisoformat(report_date)
    report_params = compute_report_dates(marketplace, api_source, today)

    job_id = create_job({
        "client_id": client_id,
        "api_source": api_source,
        "marketplace": marketplace,
        "report_type": report_type,
        "frequency": "event",
        "report_date": report_date,
        "execution_date": execution_date,
        "trigger": "event_scheduler",
        "event_id": event_id,
    })

    payload = build_payload(
        api_source=api_source,
        client_id=client_id,
        marketplace=marketplace,
        report_type=report_type,
        report_params=report_params,
        job_id=job_id,
        frequency="event",
        folder_name="",
        subfolder_strategy="date",
        execution_date=execution_date,
        report_date=report_date,
    )

    launch_execution(parent, payload, job_id, error_phase="event_scheduler")
    return job_id


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
