"""Download report from Amazon and upload to Google Drive.

Self-authenticates via SDK. For SP API: fetches the report document URL
then downloads and decompresses. For Ads API: downloads directly from
the URL returned during polling. Uploads to Drive using either a custom
folder name or the default date-first layout.
Updates the job through each lifecycle stage.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta

import flask

from shared import ads_api_client, sp_api_client
from shared.ads_api_errors import AdsProfileUnauthorizedError
from shared.ads_sb_legacy import augment_sb_campaigns_content
from shared.credentials import get_ads_credentials, get_sp_credentials
from shared.drive_client import find_or_create_folder, upload_report
from shared.firestore_utils import get_client, update_job_status
from shared.logging_setup import bind_log_context, clear_log_context, init_logging
from shared.report_converter import maybe_convert_to_tsv
from shared.sp_api_errors import SPAPIForbiddenError
from shared.throttle import is_throttled

logger = logging.getLogger(__name__)
init_logging("download-upload")


def handler(request: flask.Request) -> tuple[dict, int]:
    data = request.get_json(silent=True) or {}

    api_source = data.get("api_source")
    client_id = data.get("client_id")
    marketplace = data.get("marketplace")
    report_type = data.get("report_type")
    job_id = data.get("job_id")
    download_info = data.get("download_info") or {}
    frequency = data.get("frequency", "on_demand")
    report_params = data.get("report_params") or {}
    folder_name = data.get("folder_name", "")
    subfolder_strategy = data.get("subfolder_strategy", "date")
    execution_date_str = data.get("execution_date", "")

    if not all([api_source, client_id, marketplace, report_type]):
        return {
            "error": "Missing required fields: api_source, client_id, marketplace, report_type",
            "code": "INVALID_REQUEST",
        }, 400

    clear_log_context()
    bind_log_context(
        job_id=job_id,
        client_id=client_id,
        api_source=api_source,
        marketplace=marketplace,
        report_type=report_type,
        phase="download_upload",
    )

    try:
        if job_id:
            update_job_status(job_id, "downloading")

        if api_source == "sp_api":
            content = _download_sp_report(client_id, marketplace, report_type, download_info)
        elif api_source == "ads_api":
            content = _download_ads_report(
                client_id, marketplace, report_type, download_info, report_params
            )
        else:
            return {"error": f"Unknown api_source: {api_source}", "code": "INVALID_SOURCE"}, 400

        if job_id:
            update_job_status(job_id, "uploading")

        root_folder_id = os.environ.get("GDRIVE_ROOT_FOLDER_ID", "")
        if not root_folder_id:
            root_folder_name = os.environ.get("GDRIVE_ROOT_FOLDER_NAME", "Kalilos Reports")
            root_folder_id = find_or_create_folder(root_folder_name, "root")

        client_doc = get_client(client_id)
        client_name = client_doc["name"] if client_doc else client_id

        report_start, report_end = _extract_report_dates(api_source, report_params)

        execution_date_val: date | None = None
        if execution_date_str:
            execution_date_val = date.fromisoformat(execution_date_str)

        output_columns = report_params.pop("outputColumns", None)
        if output_columns is None and api_source == "ads_api":
            output_columns = report_params.get("columns")

        content, converted = maybe_convert_to_tsv(
            content,
            api_source,
            report_type,
            output_columns=output_columns,
            normalize_percentages=(api_source == "sp_api"),
        )
        file_ext = ".tsv" if converted else None
        mime_type = "text/tab-separated-values" if converted else None

        result = upload_report(
            root_folder_id=root_folder_id,
            client_name=client_name,
            marketplace=marketplace,
            api_source=api_source,
            report_type=report_type,
            report_date=report_start,
            frequency=frequency,
            content=content,
            file_ext=file_ext,
            mime_type=mime_type,
            folder_name=folder_name,
            subfolder_strategy=subfolder_strategy,
            report_end_date=report_end if report_end != report_start else None,
            execution_date=execution_date_val,
        )

        if job_id:
            update_job_status(
                job_id,
                "completed",
                gdrive_file_id=result["file_id"],
                gdrive_folder_id=result["folder_id"],
                gdrive_path=result["path"],
            )

        logger.info(
            "Report uploaded to Drive",
            extra={
                "job_id": job_id,
                "drive_file_id": result["file_id"],
                "path": result["path"],
                "size_bytes": len(content),
            },
        )

        return {
            "drive_file_id": result["file_id"],
            "drive_path": result["path"],
            "filename": result["filename"],
            "size_bytes": len(content),
        }, 200

    except PermissionError as exc:
        logger.error(
            "Drive access denied",
            extra={"client_id": client_id, "error": str(exc)},
        )
        if job_id:
            update_job_status(
                job_id,
                "failed",
                error_details={"message": str(exc), "phase": "drive_access"},
            )
        return {"error": str(exc), "code": "DRIVE_ACCESS_DENIED"}, 403

    except SPAPIForbiddenError as exc:
        logger.error(
            "SP-API access forbidden at download_upload",
            extra={**exc.log_context(), "report_type": report_type},
        )
        if job_id:
            update_job_status(
                job_id,
                "failed",
                error_details={
                    "message": str(exc),
                    "phase": "download_upload",
                    "code": "FORBIDDEN",
                },
            )
        return {"error": str(exc), "code": "FORBIDDEN"}, 403

    except AdsProfileUnauthorizedError as exc:
        logger.error(
            "Ads API unauthorized 3P profile at download_upload",
            extra={**exc.log_context(), "report_type": report_type},
        )
        if job_id:
            update_job_status(
                job_id,
                "failed",
                error_details={
                    "message": str(exc),
                    "phase": "download_upload",
                    "code": "UNAUTHORIZED",
                },
            )
        return {"error": str(exc), "code": "UNAUTHORIZED"}, 401

    except Exception as exc:
        if is_throttled(exc):
            logger.warning("Throttled by Amazon at download_upload", extra={
                "client_id": client_id, "api_source": api_source, "error": str(exc)[:200],
            })
            return {"error": str(exc)[:200], "code": "THROTTLED"}, 429

        logger.exception(
            "download_upload failed",
            extra={"client_id": client_id, "api_source": api_source, "report_type": report_type},
        )
        if job_id:
            update_job_status(
                job_id,
                "failed",
                error_details={"message": str(exc), "phase": "download_upload"},
            )
        return {"error": "Failed to download/upload report", "code": "DOWNLOAD_UPLOAD_FAILED"}, 500


def _download_sp_report(
    client_id: str,
    marketplace: str,
    report_type: str,
    download_info: dict,
) -> bytes:
    document_id = download_info.get("report_document_id")
    if not document_id:
        raise ValueError("Missing report_document_id in download_info")

    creds = get_sp_credentials(client_id)
    doc = sp_api_client.get_report_document(
        creds,
        marketplace,
        document_id,
        client_id=client_id,
        report_type=report_type,
    )
    return sp_api_client.download_report(doc["url"], doc.get("compression"))


def _download_ads_report(
    client_id: str,
    marketplace: str,
    report_type: str,
    download_info: dict,
    report_params: dict | None = None,
) -> bytes:
    download_url = download_info.get("download_url")
    if not download_url:
        raise ValueError("Missing download_url in download_info")

    creds = get_ads_credentials(client_id, marketplace)
    content = ads_api_client.download_report(
        creds,
        marketplace,
        download_url,
        client_id=client_id,
        report_type=report_type,
    )

    # The v3 reporting endpoint silently omits legacy (non-multi-ad-group) SB
    # campaigns. For the SB Campaigns report, re-include them via the deprecated
    # v2 reporting endpoints so the export total matches the Ads console.
    if report_type == "sbCampaigns":
        start, end = _extract_report_dates("ads_api", report_params or {})
        content = augment_sb_campaigns_content(
            content,
            credentials=creds,
            marketplace=marketplace,
            start_date=start,
            end_date=end,
        )

    return content


def _extract_report_dates(api_source: str, report_params: dict) -> tuple[date, date]:
    """Best-effort extraction of the report's logical start and end dates for folder/filename."""
    start: date | None = None
    end: date | None = None

    if api_source == "sp_api":
        raw_start = report_params.get("dataStartTime", "")
        raw_end = report_params.get("dataEndTime", "")
        if raw_start:
            start = datetime.fromisoformat(raw_start.replace("Z", "+00:00")).date()
        if raw_end:
            end_dt = datetime.fromisoformat(raw_end.replace("Z", "+00:00"))
            end = (end_dt - timedelta(seconds=1)).date()
    elif api_source == "ads_api":
        raw_start = report_params.get("startDate", "")
        raw_end = report_params.get("endDate", "")
        if raw_start:
            start = date.fromisoformat(raw_start)
        if raw_end:
            end = date.fromisoformat(raw_end)

    if start is None:
        start = date.today()
    if end is None:
        end = start

    return start, end
