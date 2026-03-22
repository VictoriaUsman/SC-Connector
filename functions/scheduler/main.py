"""Scheduler — reads due schedules from Firestore and fans out Cloud Workflow executions.

Triggered by Cloud Scheduler on a cron (every 30 min staging, every 15 min prod).
For each active schedule whose next_run_at has passed:
  1. Validates the owning client(s) are still active
  2. Fans out one workflow per (client, marketplace) pair
  3. Computes marketplace-timezone-aware report dates
  4. Optionally launches reconciliation jobs for T-3 / T-7
  5. Updates the schedule's last_run_at / next_run_at
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import Any

import flask

from shared.firestore_utils import (
    get_client,
    list_due_schedules,
    update_schedule_run_times,
)
from shared.schedule_compute import compute_next_run
from shared.workflow_launcher import client_has_credentials, get_workflow_parent, launch_for_marketplace

logger = logging.getLogger(__name__)

MAX_EXECUTIONS_PER_CLIENT = 10


def handler(request: flask.Request) -> tuple[dict, int]:
    now = datetime.now(timezone.utc)
    due = list_due_schedules(now)

    if not due:
        logger.info("No due schedules")
        return {"status": "ok", "launched": 0}, 200

    parent = get_workflow_parent()

    random.shuffle(due)

    by_client: dict[str, list[dict[str, Any]]] = {}
    for sched in due:
        for cid in _get_client_ids(sched):
            by_client.setdefault(cid, []).append(sched)

    launched = 0
    skipped = 0
    errors: list[dict[str, str]] = []

    for client_id, schedules in by_client.items():
        client = get_client(client_id)
        if not client or not client.get("is_active", True):
            skipped += len(schedules)
            logger.info("Skipping inactive client", extra={"client_id": client_id})
            continue

        capped = schedules[:MAX_EXECUTIONS_PER_CLIENT]
        if len(schedules) > MAX_EXECUTIONS_PER_CLIENT:
            skipped += len(schedules) - MAX_EXECUTIONS_PER_CLIENT
            logger.warning(
                "Client hit execution cap",
                extra={"client_id": client_id, "cap": MAX_EXECUTIONS_PER_CLIENT, "total": len(schedules)},
            )

        for sched in capped:
            if not client_has_credentials(client, sched.get("api_source", "")):
                skipped += len(_get_marketplaces(sched))
                logger.info(
                    "Skipping schedule — client missing credentials for api_source",
                    extra={"client_id": client_id, "schedule_id": sched["id"], "api_source": sched.get("api_source")},
                )
                continue

            marketplaces = _get_marketplaces(sched)
            for marketplace in marketplaces:
                try:
                    ids = launch_for_marketplace(parent, now, sched, client_id, marketplace)
                    launched += len(ids)
                except Exception as exc:
                    logger.exception("Failed to launch workflow", extra={"schedule_id": sched["id"], "marketplace": marketplace})
                    errors.append({"schedule_id": sched["id"], "error": str(exc)})

    for sched in due:
        try:
            schedule_config = sched.get("schedule_config", {"type": sched.get("frequency", "daily")})
            next_run = compute_next_run(now, schedule_config)
            update_schedule_run_times(sched["id"], last_run_at=now, next_run_at=next_run)
        except Exception:
            logger.exception("Failed to update schedule run times", extra={"schedule_id": sched["id"]})

    logger.info(
        "Scheduler run complete",
        extra={"launched": launched, "skipped": skipped, "errors": len(errors)},
    )
    return {"status": "ok", "launched": launched, "skipped": skipped, "errors": len(errors)}, 200


def _get_client_ids(schedule: dict[str, Any]) -> list[str]:
    """Support both legacy single client_id and new client_ids array."""
    if "client_ids" in schedule and schedule["client_ids"]:
        return schedule["client_ids"]
    if "client_id" in schedule and schedule["client_id"]:
        return [schedule["client_id"]]
    return []


def _get_marketplaces(schedule: dict[str, Any]) -> list[str]:
    """Support both legacy single marketplace and new marketplaces array."""
    if "marketplaces" in schedule and schedule["marketplaces"]:
        return schedule["marketplaces"]
    if "marketplace" in schedule and schedule["marketplace"]:
        return [schedule["marketplace"]]
    return []


