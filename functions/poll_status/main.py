"""Poll report status — check if an Amazon report is ready for download.

Self-authenticates via SDK. Returns a normalized status (ready / pending / failed)
and API-specific download_info when the report is ready. Updates the job's
poll_count in Firestore and marks it failed if Amazon reports a terminal status.
"""

from __future__ import annotations

import logging

import flask

from shared import ads_api_client, sp_api_client
from shared.ads_api_errors import AdsProfileUnauthorizedError
from shared.credentials import get_ads_credentials, get_sp_credentials
from shared.db import increment_job_poll_count, update_job_status
from shared.logging_setup import bind_log_context, clear_log_context, init_logging
from shared.sp_api_errors import SPAPIForbiddenError
from shared.throttle import is_throttled

logger = logging.getLogger(__name__)
init_logging("poll-status")


def handler(request: flask.Request) -> tuple[dict, int]:
    data = request.get_json(silent=True) or {}

    api_source = data.get("api_source")
    client_id = data.get("client_id")
    marketplace = data.get("marketplace")
    report_id = data.get("report_id")
    job_id = data.get("job_id")
    report_type = data.get("report_type", "unknown")

    if not all([api_source, client_id, marketplace, report_id]):
        return {
            "error": "Missing required fields: api_source, client_id, marketplace, report_id",
            "code": "INVALID_REQUEST",
        }, 400

    clear_log_context()
    bind_log_context(
        job_id=job_id,
        client_id=client_id,
        api_source=api_source,
        marketplace=marketplace,
        report_type=report_type,
        phase="poll_status",
    )

    try:
        if api_source == "sp_api":
            creds = get_sp_credentials(client_id)
            result = sp_api_client.get_report(
                creds,
                marketplace,
                report_id,
                client_id=client_id,
                report_type=report_type,
            )
        elif api_source == "ads_api":
            creds = get_ads_credentials(client_id, marketplace)
            result = ads_api_client.get_report(
                creds,
                marketplace,
                report_id,
                client_id=client_id,
                report_type=report_type,
            )
        else:
            return {"error": f"Unknown api_source: {api_source}", "code": "INVALID_SOURCE"}, 400

        if job_id:
            increment_job_poll_count(job_id)

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

    except SPAPIForbiddenError as exc:
        logger.error(
            "SP-API access forbidden at poll_status",
            extra={**exc.log_context(), "report_id": report_id},
        )
        if job_id:
            update_job_status(
                job_id,
                "failed",
                error_details={
                    "message": str(exc),
                    "phase": "poll_status",
                    "code": "FORBIDDEN",
                },
            )
        return {"error": str(exc), "code": "FORBIDDEN"}, 403

    except AdsProfileUnauthorizedError as exc:
        logger.error(
            "Ads API unauthorized 3P profile at poll_status",
            extra={**exc.log_context(), "report_id": report_id},
        )
        if job_id:
            update_job_status(
                job_id,
                "failed",
                error_details={
                    "message": str(exc),
                    "phase": "poll_status",
                    "code": "UNAUTHORIZED",
                },
            )
        return {"error": str(exc), "code": "UNAUTHORIZED"}, 401

    except Exception as exc:
        if is_throttled(exc):
            logger.warning("Throttled by Amazon at poll_status", extra={
                "report_id": report_id, "api_source": api_source, "error": str(exc)[:200],
            })
            return {"error": str(exc)[:200], "code": "THROTTLED"}, 429

        logger.exception("poll_status failed", extra={"report_id": report_id, "api_source": api_source})
        return {"error": "Failed to poll report status", "code": "POLL_FAILED"}, 500


def _extract_download_info(api_source: str, result: dict) -> dict:
    """Extract API-specific info needed by the download_upload step."""
    if api_source == "sp_api":
        return {"report_document_id": result.get("report_document_id")}
    return {"download_url": result.get("download_url")}
