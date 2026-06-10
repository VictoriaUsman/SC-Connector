"""Fetch API — synchronous Amazon API data pull (non-report path).

Handles ``mode="api_call"`` workflow executions for operations registered in
``shared.api_operations`` (e.g. the Replenishment / Subscribe & Save API). Unlike
the report pipeline (create -> poll -> download), these are synchronous REST
calls: this single function authenticates, fetches + paginates, flattens to TSV,
uploads to Drive, and marks the job ``completed``. BigQuery ingestion runs as a
best-effort follow-up step in the workflow (identical to the report path).
"""

from __future__ import annotations

import logging
import os
from datetime import date

import flask

from shared.api_operations import get_api_operation
from shared.drive_client import find_or_create_folder, upload_report
from shared.firestore_utils import get_client, update_job_status
from shared.logging_setup import bind_log_context, clear_log_context, init_logging
from shared.report_converter import rows_to_tsv
from shared.sp_api_rest import SPAPIRequestError
from shared.throttle import is_throttled
from shared import replenishment_client

logger = logging.getLogger(__name__)
init_logging("fetch-api")


def handler(request: flask.Request) -> tuple[dict, int]:
    data = request.get_json(silent=True) or {}

    api_source = data.get("api_source")
    client_id = data.get("client_id")
    marketplace = data.get("marketplace")
    operation = data.get("report_type")
    job_id = data.get("job_id")
    report_params = data.get("report_params") or {}
    frequency = data.get("frequency", "on_demand")
    folder_name = data.get("folder_name", "")
    subfolder_strategy = data.get("subfolder_strategy", "date")
    execution_date_str = data.get("execution_date", "")
    report_date_str = data.get("report_date", "")
    report_end_date_str = data.get("report_end_date", "")

    if not all([api_source, client_id, marketplace, operation]):
        return {
            "error": "Missing required fields: api_source, client_id, marketplace, report_type",
            "code": "INVALID_REQUEST",
        }, 400

    op = get_api_operation(operation)
    if op is None:
        return {"error": f"Unknown API operation: {operation}", "code": "UNKNOWN_OPERATION"}, 400

    clear_log_context()
    bind_log_context(
        job_id=job_id,
        client_id=client_id,
        api_source=api_source,
        marketplace=marketplace,
        report_type=operation,
        phase="fetch_api",
    )

    try:
        if job_id:
            update_job_status(job_id, "fetching")

        start_date = _parse_date(report_date_str) or date.today()
        end_date = _parse_date(report_end_date_str) or start_date

        rows = _dispatch(op, client_id, marketplace, start_date, end_date, report_params)

        content = rows_to_tsv(rows)
        if not content:
            content = b""
        logger.info(
            "Fetched API operation rows",
            extra={"operation": operation, "row_count": len(rows), "client_id": client_id},
        )

        if job_id:
            update_job_status(job_id, "uploading")

        root_folder_id = os.environ.get("GDRIVE_ROOT_FOLDER_ID", "")
        if not root_folder_id:
            root_folder_name = os.environ.get("GDRIVE_ROOT_FOLDER_NAME", "Kalilos Reports")
            root_folder_id = find_or_create_folder(root_folder_name, "root")

        client_doc = get_client(client_id)
        client_name = client_doc["name"] if client_doc else client_id

        execution_date_val = _parse_date(execution_date_str)

        result = upload_report(
            root_folder_id=root_folder_id,
            client_name=client_name,
            marketplace=marketplace,
            api_source="sp_api",
            report_type=operation,
            report_date=start_date,
            frequency=frequency,
            content=content,
            file_ext=".tsv",
            mime_type="text/tab-separated-values",
            folder_name=folder_name,
            subfolder_strategy=subfolder_strategy,
            report_end_date=end_date if end_date != start_date else None,
            execution_date=execution_date_val,
        )

        if job_id:
            update_job_status(
                job_id,
                "completed",
                gdrive_file_id=result["file_id"],
                gdrive_folder_id=result["folder_id"],
                gdrive_path=result["path"],
                row_count=len(rows),
            )

        logger.info(
            "API operation delivered to Drive",
            extra={"job_id": job_id, "drive_file_id": result["file_id"], "path": result["path"]},
        )

        return {
            "status": "completed",
            "drive_file_id": result["file_id"],
            "drive_path": result["path"],
            "report_date": start_date.isoformat(),
            "row_count": len(rows),
        }, 200

    except PermissionError as exc:
        logger.error("Drive access denied", extra={"client_id": client_id, "error": str(exc)})
        if job_id:
            update_job_status(job_id, "failed", error_details={"message": str(exc), "phase": "fetch_api"})
        return {"error": str(exc), "code": "DRIVE_ACCESS_DENIED"}, 403

    except SPAPIRequestError as exc:
        if is_throttled(exc):
            logger.warning("Throttled by Amazon at fetch_api", extra={
                "client_id": client_id, "operation": operation, "error": str(exc)[:200],
            })
            return {"error": str(exc)[:200], "code": "THROTTLED"}, 429

        if exc.status_code != 403:
            logger.exception("SP-API REST request failed at fetch_api", extra={
                "client_id": client_id,
                "operation": operation,
                "status_code": exc.status_code,
            })
            if job_id:
                update_job_status(job_id, "failed", error_details={"message": str(exc), "phase": "fetch_api"})
            return {"error": str(exc), "code": "FETCH_FAILED"}, 500

        logger.error(
            "SP-API access forbidden at fetch_api",
            extra={"client_id": client_id, "operation": operation, "status_code": exc.status_code},
        )
        if job_id:
            update_job_status(
                job_id, "failed",
                error_details={"message": str(exc), "phase": "fetch_api", "code": "FORBIDDEN"},
            )
        return {"error": str(exc), "code": "FORBIDDEN"}, 403

    except Exception as exc:
        if is_throttled(exc):
            logger.warning("Throttled by Amazon at fetch_api", extra={
                "client_id": client_id, "operation": operation, "error": str(exc)[:200],
            })
            return {"error": str(exc)[:200], "code": "THROTTLED"}, 429

        logger.exception("fetch_api failed", extra={"client_id": client_id, "operation": operation})
        if job_id:
            update_job_status(job_id, "failed", error_details={"message": str(exc), "phase": "fetch_api"})
        return {"error": str(exc), "code": "FETCH_FAILED"}, 500


def _dispatch(
    op: dict,
    client_id: str,
    marketplace: str,
    start_date: date,
    end_date: date,
    report_params: dict,
) -> list[dict]:
    """Route to the right client method based on the operation's handler key."""
    handler_key = op.get("handler")
    aggregation = op.get("aggregation") or "WEEK"

    if handler_key == "replenishment_offers":
        return replenishment_client.list_offers(client_id, marketplace)

    if handler_key == "replenishment_sp_metrics":
        return replenishment_client.fetch_sp_metrics(
            client_id, marketplace, start_date, end_date, aggregation=aggregation,
        )

    if handler_key == "replenishment_offer_metrics":
        time_period_type = report_params.get("timePeriodType", "PERFORMANCE")
        return replenishment_client.fetch_offer_metrics(
            client_id, marketplace, start_date, end_date,
            aggregation=aggregation, time_period_type=time_period_type,
        )

    raise ValueError(f"No fetch handler implemented for '{handler_key}'")


def _parse_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
