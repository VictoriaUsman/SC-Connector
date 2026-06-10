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

from shared.ads_report_config import ADS_REPORT_TYPES as _ADS_REPORT_TYPES
from shared.api_operations import get_api_operation, is_api_operation
from shared.firestore_utils import create_job, update_job_status
from shared.removed_reports import removed_report_reason
from shared.schedule_compute import (
    compute_date_range,
    compute_report_dates,
    marketplace_today,
    marketplace_yesterday,
)

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
    execution_date: str | None = None,
    report_date: str | None = None,
    report_end_date: str | None = None,
    mode: str = "report",
) -> dict[str, Any]:
    """Construct the canonical workflow execution payload.

    ``mode`` is ``"report"`` for the async create-report pipeline (default) or
    ``"api_call"`` for the synchronous fetch path (``functions/fetch_api``).
    """
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
        "mode": mode,
    }
    if schedule_id is not None:
        payload["schedule_id"] = schedule_id
    if execution_date is not None:
        payload["execution_date"] = execution_date
    if report_date is not None:
        payload["report_date"] = report_date
    if report_end_date is not None:
        payload["report_end_date"] = report_end_date
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


def get_report_types(schedule: dict[str, Any]) -> list[str]:
    """Extract the report_types list from a schedule (or request data) dict."""
    return schedule.get("report_types") or []


def is_ads_report_type(report_type: str) -> bool:
    """Return True if the report type belongs to the Ads API."""
    return report_type in _ADS_REPORT_TYPES


def infer_api_source(report_type: str, schedule_api_source: str) -> str:
    """Determine the effective API source for a report type.

    Synchronous API operations declare their own ``api_source`` in the registry,
    which always wins. Otherwise, when ``schedule_api_source`` is ``"both"`` the
    report type name decides SP vs Ads; else the schedule's source is used.
    """
    op = get_api_operation(report_type)
    if op:
        return op["api_source"]
    if schedule_api_source in ("sp_api", "ads_api"):
        return schedule_api_source
    return "ads_api" if is_ads_report_type(report_type) else "sp_api"


def validate_report_types(api_source: str, report_types: list[str]) -> str | None:
    """Return an error message if any report type conflicts with api_source, else None."""
    for rt in report_types:
        is_ads = is_ads_report_type(rt)
        if api_source == "sp_api" and is_ads:
            return f"Report type '{rt}' is an Ads API type but api_source is 'sp_api'"
        if api_source == "ads_api" and not is_ads:
            return f"Report type '{rt}' is an SP API type but api_source is 'ads_api'"
    return None


def client_has_credentials(client: dict[str, Any], api_source: str) -> bool:
    """Check whether the client has credentials for the given API source.

    For ``"both"``, returns True if the client has at least one set of
    credentials.  Per-report-type credential filtering happens inside
    ``launch_for_marketplace`` via ``infer_api_source``.
    """
    if api_source == "sp_api":
        return bool(client.get("sp_api_secret_name"))
    if api_source == "ads_api":
        return bool(client.get("ads_profile_id"))
    if api_source == "both":
        return bool(client.get("sp_api_secret_name")) or bool(client.get("ads_profile_id"))
    return False


def _get_report_params_map(
    schedule: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Return the per-report-type params map from a schedule."""
    return schedule.get("report_params", {})


def _expand_report_option_variants(type_params: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand multi-value reportOptions (e.g. asinGranularity: ["CHILD","PARENT"])
    into one params dict per value, so each gets its own workflow execution."""
    opts = type_params.get("reportOptions")
    if not isinstance(opts, dict):
        return [type_params]

    multi_key: str | None = None
    multi_vals: list[str] = []
    for k, v in opts.items():
        if isinstance(v, list) and len(v) > 1:
            multi_key = k
            multi_vals = v
            break

    if not multi_key:
        return [type_params]

    variants: list[dict[str, Any]] = []
    for val in multi_vals:
        variant = {**type_params, "reportOptions": {**opts, multi_key: val}}
        variants.append(variant)
    return variants


def launch_for_marketplace(
    parent: str,
    now: datetime,
    schedule: dict[str, Any],
    client_id: str,
    marketplace: str,
    *,
    extra_job_fields: dict[str, Any] | None = None,
    stagger_seconds: float = 0,
) -> list[str]:
    """Launch primary + reconciliation jobs for one (client, marketplace) pair.

    Iterates over all ``report_types`` in the schedule (backward-compatible
    with legacy ``report_type``).  For each report type, determines the
    effective ``api_source`` (important when the schedule source is ``"both"``),
    extracts the per-type ``report_params``, and launches a separate workflow.

    Reconciliation only runs when the timeframe strategy is ``"yesterday"``
    (the default).  Multi-day strategies inherently cover wider windows,
    making single-day re-pulls redundant.

    When ``stagger_seconds`` > 0, sleeps between launches to reduce burst
    pressure on Amazon API rate limits.

    Returns list of created job IDs.
    """
    frequency = schedule.get("frequency", "daily")
    timeframe: dict = schedule.get("timeframe", {"strategy": "yesterday"})
    strategy = timeframe.get("strategy", "yesterday")

    execution_date_val = marketplace_today(marketplace, now)
    start_date, end_date = compute_date_range(marketplace, timeframe, now)

    report_types = get_report_types(schedule)
    schedule_api_source = schedule.get("api_source", "sp_api")
    params_map = _get_report_params_map(schedule)

    job_ids: list[str] = []

    for report_type in report_types:
        effective_source = infer_api_source(report_type, schedule_api_source)
        type_params = params_map.get(report_type, {})

        # Report types Amazon has permanently removed are accepted by createReport
        # but immediately cancelled, wasting the limited createReport quota and
        # surfacing a generic "no data" failure.  Record a clearly-documented
        # failed job instead of launching a doomed workflow.
        removed_reason = removed_report_reason(report_type)
        if removed_reason and effective_source == "sp_api":
            job_data = {
                "client_id": client_id,
                "api_source": effective_source,
                "marketplace": marketplace,
                "report_type": report_type,
                "schedule_id": schedule["id"],
                "frequency": frequency,
                "execution_date": execution_date_val.isoformat(),
            }
            if extra_job_fields:
                job_data.update(extra_job_fields)
            job_id = create_job(job_data)
            update_job_status(
                job_id,
                "failed",
                error_details={
                    "message": removed_reason,
                    "phase": "create_report",
                    "code": "REPORT_REMOVED",
                },
            )
            logger.warning(
                "Skipped removed SP-API report type",
                extra={
                    "report_type": report_type,
                    "client_id": client_id,
                    "marketplace": marketplace,
                    "job_id": job_id,
                },
            )
            job_ids.append(job_id)
            continue

        # Synchronous API operations (e.g. Replenishment / S&S) take the
        # fetch_api path instead of create-report -> poll -> download. They
        # cover the full requested range in one call, so there is no report-
        # option variant expansion and no single-day reconciliation re-pulls.
        if is_api_operation(report_type):
            op_params = {**type_params}
            op_params.update(
                compute_report_dates(marketplace, effective_source, start_date, end_date)
            )
            job_data = {
                "client_id": client_id,
                "api_source": effective_source,
                "marketplace": marketplace,
                "report_type": report_type,
                "schedule_id": schedule["id"],
                "frequency": frequency,
                "report_date": start_date.isoformat(),
                "execution_date": execution_date_val.isoformat(),
                "mode": "api_call",
            }
            if end_date != start_date:
                job_data["report_end_date"] = end_date.isoformat()
            if extra_job_fields:
                job_data.update(extra_job_fields)

            job_id = create_job(job_data)
            payload = build_payload(
                api_source=effective_source,
                client_id=client_id,
                marketplace=marketplace,
                report_type=report_type,
                report_params=op_params,
                job_id=job_id,
                frequency=frequency,
                folder_name=schedule.get("folder_name", ""),
                subfolder_strategy=schedule.get("subfolder_strategy", "date"),
                schedule_id=schedule["id"],
                execution_date=execution_date_val.isoformat(),
                report_date=start_date.isoformat(),
                report_end_date=end_date.isoformat() if end_date != start_date else None,
                mode="api_call",
            )
            launch_execution(parent, payload, job_id, error_phase="scheduler")
            job_ids.append(job_id)
            if stagger_seconds > 0:
                time.sleep(stagger_seconds)
            continue

        for variant_params in _expand_report_option_variants(type_params):
            report_params = {**variant_params}
            report_params.update(
                compute_report_dates(marketplace, effective_source, start_date, end_date)
            )

            dates_to_pull: list[tuple[date, date, dict]] = [
                (start_date, end_date, report_params),
            ]

            if strategy == "yesterday":
                reconciliation_days: list[int] = schedule.get("reconciliation_days", [3, 7])
                for days_back in reconciliation_days:
                    recon_date = start_date - timedelta(days=days_back - 1)
                    recon_params = {**variant_params}
                    recon_params.update(
                        compute_report_dates(marketplace, effective_source, recon_date)
                    )
                    dates_to_pull.append((recon_date, recon_date, recon_params))

            for pull_start, pull_end, pull_params in dates_to_pull:
                job_data: dict[str, Any] = {
                    "client_id": client_id,
                    "api_source": effective_source,
                    "marketplace": marketplace,
                    "report_type": report_type,
                    "schedule_id": schedule["id"],
                    "frequency": frequency,
                    "report_date": pull_start.isoformat(),
                    "execution_date": execution_date_val.isoformat(),
                }
                if pull_start != pull_end:
                    job_data["report_end_date"] = pull_end.isoformat()
                if extra_job_fields:
                    job_data.update(extra_job_fields)

                job_id = create_job(job_data)

                payload = build_payload(
                    api_source=effective_source,
                    client_id=client_id,
                    marketplace=marketplace,
                    report_type=report_type,
                    report_params=pull_params,
                    job_id=job_id,
                    frequency=frequency,
                    folder_name=schedule.get("folder_name", ""),
                    subfolder_strategy=schedule.get("subfolder_strategy", "date"),
                    schedule_id=schedule["id"],
                    execution_date=execution_date_val.isoformat(),
                    report_date=pull_start.isoformat(),
                )

                launch_execution(parent, payload, job_id, error_phase="scheduler")
                job_ids.append(job_id)

                if stagger_seconds > 0:
                    time.sleep(stagger_seconds)

    return job_ids
