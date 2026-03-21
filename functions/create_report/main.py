"""Create report — request a report from Amazon SP API or Ads API.

Self-authenticates using SDK credentials from Secret Manager.
Creates or updates the tracking job in Firestore, then submits the
report request to the appropriate Amazon API.
"""

from __future__ import annotations

import logging

import flask

from shared import ads_api_client, sp_api_client
from shared.ads_report_config import ADS_REPORT_TYPES as _ADS_REPORT_TYPES, get_all_columns
from shared.credentials import get_ads_credentials, get_sp_credentials
from shared.firestore_utils import create_job, update_job_status
from shared.schedule_compute import marketplace_yesterday

logger = logging.getLogger(__name__)


def handler(request: flask.Request) -> tuple[dict, int]:
    data = request.get_json(silent=True) or {}

    api_source = data.get("api_source")
    client_id = data.get("client_id")
    marketplace = data.get("marketplace")
    report_type = data.get("report_type")

    if not all([api_source, client_id, marketplace, report_type]):
        return {
            "error": "Missing required fields: api_source, client_id, marketplace, report_type",
            "code": "INVALID_REQUEST",
        }, 400

    job_id = data.get("job_id")
    report_params = data.get("report_params") or {}
    schedule_id = data.get("schedule_id")
    frequency = data.get("frequency", "on_demand")

    try:
        if not job_id:
            job_id = create_job({
                "client_id": client_id,
                "api_source": api_source,
                "marketplace": marketplace,
                "report_type": report_type,
                "schedule_id": schedule_id,
                "frequency": frequency,
            })
        update_job_status(job_id, "requesting")

        if api_source == "sp_api":
            creds = get_sp_credentials(client_id)
            report_id = sp_api_client.create_report(
                credentials=creds,
                marketplace=marketplace,
                report_type=report_type,
                report_params=report_params or None,
            )
        elif api_source == "ads_api":
            creds = get_ads_credentials(client_id)
            if "startDate" not in report_params or "endDate" not in report_params:
                yesterday = marketplace_yesterday(marketplace).isoformat()
                report_params.setdefault("startDate", yesterday)
                report_params.setdefault("endDate", yesterday)
            report_config = _build_ads_report_config(report_type, report_params)
            report_id = ads_api_client.create_report(
                credentials=creds,
                marketplace=marketplace,
                report_config=report_config,
            )
        else:
            return {"error": f"Unknown api_source: {api_source}", "code": "INVALID_SOURCE"}, 400

        update_job_status(job_id, "polling", amazon_report_id=report_id)

        logger.info(
            "Report created",
            extra={
                "job_id": job_id,
                "report_id": report_id,
                "client_id": client_id,
                "api_source": api_source,
                "report_type": report_type,
                "marketplace": marketplace,
            },
        )

        return {"report_id": report_id, "job_id": job_id}, 200

    except ValueError as exc:
        logger.warning("Validation error in create_report", extra={"error": str(exc), "client_id": client_id})
        if job_id:
            update_job_status(job_id, "failed", error_details={"message": str(exc), "phase": "create_report"})
        return {"error": str(exc), "code": "VALIDATION_ERROR"}, 400

    except Exception as exc:
        logger.exception("create_report failed", extra={"client_id": client_id, "api_source": api_source})
        if job_id:
            update_job_status(job_id, "failed", error_details={"message": str(exc), "phase": "create_report"})
        return {"error": "Failed to create report", "code": "CREATE_FAILED"}, 500


def _build_ads_report_config(report_type: str, report_params: dict) -> dict:
    """Assemble the Ads API v3 async report creation body.

    Auto-populates adProduct, groupBy, and columns from the shared
    ADS_REPORT_TYPES registry when not explicitly provided in report_params.
    """
    defaults = _ADS_REPORT_TYPES.get(report_type, {})

    configuration: dict = {
        "reportTypeId": report_type,
        "format": "GZIP_JSON",
        "timeUnit": report_params.get("timeUnit", "DAILY"),
        "adProduct": report_params.get("adProduct", defaults.get("adProduct")),
        "groupBy": report_params.get("groupBy", defaults.get("groupBy")),
        "columns": report_params.get("columns") or get_all_columns(report_type),
    }

    if configuration["timeUnit"] == "SUMMARY":
        configuration["columns"] = [c for c in configuration["columns"] if c != "date"]

    if not all(configuration.get(k) for k in ("adProduct", "groupBy", "columns")):
        raise ValueError(
            f"Unsupported Ads report type '{report_type}': "
            "adProduct, groupBy, and columns are required but could not be resolved"
        )

    return {
        "name": report_params.get("name", f"{report_type} report"),
        "startDate": report_params["startDate"],
        "endDate": report_params["endDate"],
        "configuration": configuration,
    }
