"""Shared workflow-launch helpers used by scheduler, API on-demand, and manual triggers.

Centralizes:
- Workflow parent path construction
- Payload building
- Retry-with-backoff for ``ExecutionsClient.create_execution``
- Per-marketplace fan-out with reconciliation
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, date, timedelta, timezone
from typing import Any

from google.cloud.workflows.executions_v1 import ExecutionsClient
from google.cloud.workflows.executions_v1.types import Execution

from shared.firestore_utils import create_job, update_job_status
from shared.schedule_compute import compute_date_range, compute_report_dates, marketplace_yesterday

logger = logging.getLogger(__name__)

_exec_client: ExecutionsClient | None = None

MAX_LAUNCH_RETRIES = 3


def _get_exec_client() -> ExecutionsClient:
    global _exec_client
    if _exec_client is None:
        _exec_client = ExecutionsClient()
    return _exec_client


def get_workflow_parent() -> str:
    """Build the fully-qualified Cloud Workflows parent path from env vars."""
    project = os.environ["GCP_PROJECT"]
    workflow_name = os.environ["WORKFLOW_NAME"]
    location = os.environ.get("WORKFLOW_LOCATION", "us-central1")
    return f"projects/{project}/locations/{location}/workflows/{workflow_name}"


def build_payload(
    *,
    api_source: str,
    client_id: str,
    marketplace: str,
    report_type: str,
    report_params: dict,
    job_id: str,
    frequency: str,
    folder_name: str = "",
    subfolder_strategy: str = "date",
    schedule_id: str | None = None,
) -> dict[str, Any]:
    """Construct the canonical workflow execution payload."""
    payload: dict[str, Any] = {
        "api_source": api_source,
        "client_id": client_id,
        "marketplace": marketplace,
        "report_type": report_type,
        "report_params": report_params,
        "job_id": job_id,
        "frequency": frequency,
        "folder_name": folder_name,
        "subfolder_strategy": subfolder_strategy,
    }
    if schedule_id is not None:
        payload["schedule_id"] = schedule_id
    return payload


def launch_execution(
    parent: str,
    payload: dict[str, Any],
    job_id: str,
    *,
    retries: int = MAX_LAUNCH_RETRIES,
    error_phase: str = "trigger",
) -> Execution:
    """Start a Cloud Workflow execution with retry-and-backoff.

    Returns the ``Execution`` on success.  On exhausted retries, marks the job
    as failed in Firestore and raises the last exception.
    """
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            execution = Execution(argument=json.dumps(payload))
            result = _get_exec_client().create_execution(parent=parent, execution=execution)
            return result
        except Exception as exc:
            last_err = exc
            logger.warning("Workflow start attempt %d failed: %s", attempt + 1, exc)
            if attempt < retries - 1:
                time.sleep(1 * (attempt + 1))

    update_job_status(job_id, "failed", error_details={"message": str(last_err), "phase": error_phase})
    raise last_err  # type: ignore[misc]


def launch_for_marketplace(
    parent: str,
    now: datetime,
    schedule: dict[str, Any],
    client_id: str,
    marketplace: str,
    *,
    extra_job_fields: dict[str, Any] | None = None,
) -> list[str]:
    """Launch primary + reconciliation jobs for one (client, marketplace) pair.

    Reconciliation only runs when the timeframe strategy is ``"yesterday"``
    (the default).  Multi-day strategies inherently cover wider windows,
    making single-day re-pulls redundant.

    Returns list of created job IDs.
    """
    frequency = schedule.get("frequency", "daily")
    timeframe: dict = schedule.get("timeframe", {"strategy": "yesterday"})
    strategy = timeframe.get("strategy", "yesterday")

    start_date, end_date = compute_date_range(marketplace, timeframe, now)

    report_params = {**schedule.get("report_params", {})}
    report_params.update(
        compute_report_dates(marketplace, schedule["api_source"], start_date, end_date)
    )

    dates_to_pull: list[tuple[date, date, dict]] = [(start_date, end_date, report_params)]

    if strategy == "yesterday":
        reconciliation_days: list[int] = schedule.get("reconciliation_days", [3, 7])
        for days_back in reconciliation_days:
            recon_date = start_date - timedelta(days=days_back - 1)
            recon_params = {**schedule.get("report_params", {})}
            recon_params.update(
                compute_report_dates(marketplace, schedule["api_source"], recon_date)
            )
            dates_to_pull.append((recon_date, recon_date, recon_params))

    job_ids: list[str] = []
    for pull_start, pull_end, pull_params in dates_to_pull:
        job_data: dict[str, Any] = {
            "client_id": client_id,
            "api_source": schedule["api_source"],
            "marketplace": marketplace,
            "report_type": schedule["report_type"],
            "schedule_id": schedule["id"],
            "frequency": frequency,
            "report_date": pull_start.isoformat(),
        }
        if pull_start != pull_end:
            job_data["report_end_date"] = pull_end.isoformat()
        if extra_job_fields:
            job_data.update(extra_job_fields)

        job_id = create_job(job_data)

        payload = build_payload(
            api_source=schedule["api_source"],
            client_id=client_id,
            marketplace=marketplace,
            report_type=schedule["report_type"],
            report_params=pull_params,
            job_id=job_id,
            frequency=frequency,
            folder_name=schedule.get("folder_name", ""),
            subfolder_strategy=schedule.get("subfolder_strategy", "date"),
            schedule_id=schedule["id"],
        )

        launch_execution(parent, payload, job_id, error_phase="scheduler")
        job_ids.append(job_id)

    return job_ids
