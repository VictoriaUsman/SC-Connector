"""Poll report status — check if an Amazon report is ready for download.

Self-authenticates via SDK. Returns a normalized status (ready / pending / failed)
and API-specific download_info when the report is ready. Updates the job's
poll_count in Firestore and marks it failed if Amazon reports a terminal status.
"""

from __future__ import annotations

import logging

import flask
from google.cloud import firestore

from shared import ads_api_client, sp_api_client
from shared.credentials import get_ads_credentials, get_sp_credentials
from shared.firestore_utils import update_job, update_job_status

logger = logging.getLogger(__name__)


def handler(request: flask.Request) -> tuple[dict, int]:
    data = request.get_json(silent=True) or {}

    api_source = data.get("api_source")
    client_id = data.get("client_id")
    marketplace = data.get("marketplace")
    report_id = data.get("report_id")
    job_id = data.get("job_id")

    if not all([api_source, client_id, marketplace, report_id]):
        return {
            "error": "Missing required fields: api_source, client_id, marketplace, report_id",
            "code": "INVALID_REQUEST",
        }, 400

    try:
        if api_source == "sp_api":
            creds = get_sp_credentials(client_id)
            result = sp_api_client.get_report(creds, marketplace, report_id)
        elif api_source == "ads_api":
            creds = get_ads_credentials(client_id)
            result = ads_api_client.get_report(creds, marketplace, report_id)
        else:
            return {"error": f"Unknown api_source: {api_source}", "code": "INVALID_SOURCE"}, 400

        if job_id:
            update_job(job_id, {"poll_count": firestore.Increment(1)})

            if result["status"] == "failed":
                update_job_status(
                    job_id,
                    "failed",
                    error_details={
                        "message": f"Amazon returned status: {result['raw_status']}",
                        "phase": "poll_status",
                        "raw_status": result["raw_status"],
                    },
                )

        logger.info(
            "Poll result",
            extra={
                "report_id": report_id,
                "status": result["status"],
                "raw_status": result["raw_status"],
                "job_id": job_id,
            },
        )

        return {
            "report_status": result["status"],
            "raw_status": result["raw_status"],
            "download_info": _extract_download_info(api_source, result),
        }, 200

    except Exception as exc:
        logger.exception("poll_status failed", extra={"report_id": report_id, "api_source": api_source})
        return {"error": "Failed to poll report status", "code": "POLL_FAILED"}, 500


def _extract_download_info(api_source: str, result: dict) -> dict:
    """Extract API-specific info needed by the download_upload step."""
    if api_source == "sp_api":
        return {"report_document_id": result.get("report_document_id")}
    return {"download_url": result.get("download_url")}
