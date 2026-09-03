"""Create report — request a report from Amazon SP API or Ads API.

Self-authenticates using SDK credentials from Secret Manager.
Creates or updates the tracking job in Firestore, then submits the
report request to the appropriate Amazon API.
"""

from __future__ import annotations

import calendar
import logging
from datetime import date, timedelta

import flask

from shared import ads_api_client, sp_api_client
from shared.ads_api_errors import AdsProfileUnauthorizedError
from shared.ads_report_config import ADS_REPORT_TYPES as _ADS_REPORT_TYPES, get_all_columns
from shared.credentials import get_ads_credentials, get_sp_credentials
from shared.db import create_job, update_job_status
from shared.logging_setup import bind_log_context, clear_log_context, init_logging
from shared.removed_reports import removed_report_reason
from shared.schedule_compute import marketplace_yesterday
from shared.sp_api_errors import SPAPIForbiddenError
from shared.throttle import is_throttled
from shared.vendor_reports import (
    VENDOR_REPORT_DEFAULT_OPTIONS,
    VENDOR_REPORT_PERIODS,
    VENDOR_SP_REPORT_TYPES,
)

logger = logging.getLogger(__name__)
init_logging("create-report")

_BRAND_ANALYTICS_REPORT_PERIOD: dict[str, list[str]] = {
    "GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT": ["DAY", "WEEK", "MONTH", "QUARTER"],
    "GET_BRAND_ANALYTICS_MARKET_BASKET_REPORT": ["WEEK", "MONTH", "QUARTER"],
    "GET_BRAND_ANALYTICS_REPEAT_PURCHASE_REPORT": ["WEEK", "MONTH", "QUARTER"],
    "GET_BRAND_ANALYTICS_ALTERNATE_PURCHASE_REPORT": ["WEEK", "MONTH", "QUARTER"],
    "GET_BRAND_ANALYTICS_SEARCH_QUERY_PERFORMANCE_REPORT": ["WEEK", "MONTH", "QUARTER"],
    "GET_BRAND_ANALYTICS_SEARCH_CATALOG_PERFORMANCE_REPORT": ["WEEK", "MONTH", "QUARTER"],
}

_SALES_TRAFFIC_DEFAULTS = {
    "dateGranularity": "DAY",
    "asinGranularity": "CHILD",
}


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

    clear_log_context()
    bind_log_context(
        job_id=job_id,
        client_id=client_id,
        api_source=api_source,
        report_type=report_type,
        marketplace=marketplace,
        phase="create_report",
    )

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
            bind_log_context(job_id=job_id)
        update_job_status(job_id, "requesting")

        if api_source == "sp_api":
            removed_reason = removed_report_reason(report_type)
            if removed_reason:
                logger.warning(
                    "Rejected removed SP-API report type",
                    extra={"report_type": report_type, "client_id": client_id},
                )
                if job_id:
                    update_job_status(
                        job_id,
                        "failed",
                        error_details={
                            "message": removed_reason,
                            "phase": "create_report",
                            "code": "REPORT_REMOVED",
                        },
                    )
                return {"error": removed_reason, "code": "REPORT_REMOVED"}, 422

            creds = get_sp_credentials(client_id)
            report_params = _ensure_sp_report_options(report_type, report_params)
            report_id = sp_api_client.create_report(
                credentials=creds,
                marketplace=marketplace,
                report_type=report_type,
                report_params=report_params or None,
                client_id=client_id,
            )
        elif api_source == "ads_api":
            creds = get_ads_credentials(client_id, marketplace)
            if "startDate" not in report_params or "endDate" not in report_params:
                yesterday = marketplace_yesterday(marketplace).isoformat()
                report_params.setdefault("startDate", yesterday)
                report_params.setdefault("endDate", yesterday)
            report_config = _build_ads_report_config(report_type, report_params)
            report_id = ads_api_client.create_report(
                credentials=creds,
                marketplace=marketplace,
                report_config=report_config,
                client_id=client_id,
                report_type=report_type,
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

    except SPAPIForbiddenError as exc:
        logger.error(
            "SP-API access forbidden at create_report",
            extra=exc.log_context(),
        )
        if job_id:
            update_job_status(
                job_id,
                "failed",
                error_details={
                    "message": str(exc),
                    "phase": "create_report",
                    "code": "FORBIDDEN",
                },
            )
        return {"error": str(exc), "code": "FORBIDDEN"}, 403

    except AdsProfileUnauthorizedError as exc:
        # Expected, non-retryable per-client condition: the advertiser revoked
        # the Kalilos Ads app's access to this 3P profile. It needs an account
        # manager to coordinate re-authorization, not on-call paging — so this
        # is a WARNING (the job is still marked failed with an actionable message).
        logger.warning(
            "Ads API unauthorized 3P profile at create_report",
            extra=exc.log_context(),
        )
        if job_id:
            update_job_status(
                job_id,
                "failed",
                error_details={
                    "message": str(exc),
                    "phase": "create_report",
                    "code": "UNAUTHORIZED",
                },
            )
        return {"error": str(exc), "code": "UNAUTHORIZED"}, 401

    except Exception as exc:
        if is_throttled(exc):
            logger.warning("Throttled by Amazon at create_report", extra={
                "client_id": client_id, "api_source": api_source, "error": str(exc)[:200],
            })
            return {"error": str(exc)[:200], "code": "THROTTLED"}, 429

        logger.exception("create_report failed", extra={"client_id": client_id, "api_source": api_source})
        msg = _humanize_create_error(str(exc), report_type)
        if job_id:
            update_job_status(job_id, "failed", error_details={"message": msg, "phase": "create_report"})
        return {"error": msg, "code": "CREATE_FAILED"}, 500


_NON_REQUESTABLE_REPORTS = {
    "GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2",
    "GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE",
}


def _humanize_create_error(raw: str, report_type: str) -> str:
    """Turn raw Amazon error messages into actionable guidance."""
    lower = raw.lower()

    if report_type in _NON_REQUESTABLE_REPORTS:
        return (
            f"Settlement reports are auto-generated by Amazon and cannot be "
            f"requested on demand. Remove '{report_type}' from this schedule "
            f"and download settlements from Seller Central instead."
        )

    if "not allowed at this time" in lower:
        return (
            f"Amazon rejected the request for '{report_type}' as not allowed "
            f"right now — usually a missing Role on the app (check the app's "
            f"registered Roles cover this report), or a role/reconnect change "
            f"that hasn't finished propagating on Amazon's side yet."
        )

    if "brand" in lower and "not eligible" in lower:
        return (
            f"This report requires Amazon Brand Registry enrollment. "
            f"Ensure the seller account is registered in Brand Registry."
        )

    if "invalid" in lower and "reportperiod" in lower:
        return (
            f"The date range does not match the required reportPeriod. "
            f"Use 'Last Calendar Month' or 'Last Calendar Week' timeframe "
            f"for Brand Analytics reports."
        )

    if "cancelled" in lower:
        return (
            f"Amazon cancelled this report. This usually means there is no "
            f"data for the requested date range, or the report type is not "
            f"available for this account/marketplace."
        )

    if "forbidden" in lower or "access to the resource is forbidden" in lower:
        return (
            "SP-API access forbidden: this seller account has not authorized the "
            "required roles (for Sales & Traffic, grant Selling Partner Insights "
            "in Seller Central) or the refresh token was revoked. Re-authorize "
            "the Kalilos app for this client and confirm role access, then retry."
        )

    return raw


def _ensure_sp_report_options(report_type: str, report_params: dict) -> dict:
    """Inject required reportOptions for SP API report types that need them.

    Brand Analytics reports require reportPeriod; Sales & Traffic requires
    dateGranularity + asinGranularity.  If the caller already supplied
    these via the schedule's report_params, they are preserved.
    """
    report_params = dict(report_params or {})
    opts = dict(report_params.get("reportOptions") or {})

    if report_type in _BRAND_ANALYTICS_REPORT_PERIOD and "reportPeriod" not in opts:
        period = _infer_report_period(report_type, report_params)
        opts["reportPeriod"] = period
        logger.info("Auto-set reportPeriod=%s for %s", period, report_type)

    if report_type in VENDOR_SP_REPORT_TYPES:
        for k, v in VENDOR_REPORT_DEFAULT_OPTIONS.get(report_type, {}).items():
            opts.setdefault(k, v)
        if "reportPeriod" not in opts:
            allowed = VENDOR_REPORT_PERIODS.get(report_type, ["DAY"])
            period = _infer_period_from_allowed(allowed, report_params)
            opts["reportPeriod"] = period
            logger.info("Auto-set reportPeriod=%s for %s", period, report_type)

    if report_type == "GET_SALES_AND_TRAFFIC_REPORT":
        for k, v in _SALES_TRAFFIC_DEFAULTS.items():
            opts.setdefault(k, v)
        gran = opts.get("asinGranularity")
        if isinstance(gran, list):
            opts["asinGranularity"] = gran[0] if gran else "CHILD"

    if opts:
        report_params["reportOptions"] = opts
    return report_params


def _infer_report_period(report_type: str, report_params: dict) -> str:
    """Pick the best reportPeriod based on the date range and allowed periods."""
    allowed = _BRAND_ANALYTICS_REPORT_PERIOD.get(report_type, ["MONTH"])
    return _infer_period_from_allowed(allowed, report_params)


def _infer_period_from_allowed(allowed: list[str], report_params: dict) -> str:
    """Pick the best reportPeriod from *allowed* based on the date range span."""
    start_str = report_params.get("dataStartTime", "")[:10]
    end_str = report_params.get("dataEndTime", "")[:10]

    if not start_str or not end_str:
        return allowed[0]

    try:
        start = date.fromisoformat(start_str)
        end = date.fromisoformat(end_str)
    except ValueError:
        return allowed[0]

    span = (end - start).days

    if span <= 1 and "DAY" in allowed:
        return "DAY"

    if span <= 7 and "WEEK" in allowed:
        return "WEEK"

    if "MONTH" in allowed:
        if start.day == 1:
            _, last_day = calendar.monthrange(start.year, start.month)
            expected_end = start.replace(day=last_day)
            if end == expected_end or end == expected_end + timedelta(days=1):
                return "MONTH"
        return "MONTH"

    return allowed[0]


def _build_ads_report_config(report_type: str, report_params: dict) -> dict:
    """Assemble the Ads API v3 async report creation body.

    Auto-populates adProduct, groupBy, and columns from the shared
    ADS_REPORT_TYPES registry when not explicitly provided in report_params.
    """
    defaults = _ADS_REPORT_TYPES.get(report_type, {})

    configuration: dict = {
        "reportTypeId": defaults.get("reportTypeId", report_type),
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
