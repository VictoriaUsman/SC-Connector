"""SP-API error classification and actionable messages for permission failures."""

from __future__ import annotations

from sp_api.base.exceptions import SellingApiForbiddenException

# Reports gated behind a specific Seller Central role. A *persistent* 403 on one
# of these (while other reports for the same client succeed) usually means the
# seller has not granted that role to the Kalilos app.
_ROLE_GATED_REPORTS: dict[str, str] = {
    "GET_SALES_AND_TRAFFIC_REPORT": "Selling Partner Insights (Brand Analytics)",
    "GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT": "Brand Analytics",
    "GET_BRAND_ANALYTICS_MARKET_BASKET_REPORT": "Brand Analytics",
    "GET_BRAND_ANALYTICS_REPEAT_PURCHASE_REPORT": "Brand Analytics",
    "GET_BRAND_ANALYTICS_SEARCH_QUERY_PERFORMANCE_REPORT": "Brand Analytics",
}


def forbidden_message(report_type: str | None = None) -> str:
    """Build an actionable 403 message.

    SP-API 403 ("Access to requested resource is denied" / "forbidden") is most
    often *transient* — it spikes when many reports are requested at once and
    Amazon's LWA/gateway throttles auth. The job is retried automatically. A 403
    that *persists* for one client+report points at a missing Seller Central role
    or a revoked refresh token, which needs human action.
    """
    role = _ROLE_GATED_REPORTS.get(report_type or "")
    role_hint = (
        f" This report additionally requires the seller to grant the "
        f"{role} role to the Kalilos app." if role else ""
    )
    return (
        "SP-API returned 403 (access denied). This is usually transient under load "
        "and is retried automatically. If it persists for this client and report, "
        "the seller likely has not authorized the required role or the refresh token "
        f"was revoked — re-authorize the Kalilos app in Seller Central, then retry.{role_hint}"
    )


# Backwards-compatible default (no report-type context).
FORBIDDEN_ACTIONABLE_MESSAGE = forbidden_message()


class SPAPIForbiddenError(Exception):
    """Raised when Amazon SP-API returns HTTP 403 (permissions or revoked access)."""

    def __init__(
        self,
        message: str,
        *,
        client_id: str,
        marketplace: str,
        report_type: str,
        report_date: str | None = None,
    ) -> None:
        super().__init__(message)
        self.client_id = client_id
        self.marketplace = marketplace
        self.report_type = report_type
        self.report_date = report_date

    def log_context(self) -> dict[str, str]:
        ctx = {
            "client_id": self.client_id,
            "marketplace": self.marketplace,
            "report_type": self.report_type,
        }
        if self.report_date:
            ctx["report_date"] = self.report_date
        return ctx


def is_sp_api_forbidden(exc: Exception) -> bool:
    """Return True if the exception indicates an SP-API HTTP 403 / forbidden response."""
    if isinstance(exc, (SPAPIForbiddenError, SellingApiForbiddenException)):
        return True
    msg = str(exc).lower()
    return (
        "access to the resource is forbidden" in msg
        or "access to requested resource is denied" in msg
        or ("forbidden" in msg and "403" in msg)
    )


def extract_report_date(report_params: dict | None) -> str | None:
    """Best-effort report data date from SP-API report_params."""
    if not report_params:
        return None
    start = report_params.get("dataStartTime") or report_params.get("startDate")
    end = report_params.get("dataEndTime") or report_params.get("endDate")
    if start and end and str(start)[:10] != str(end)[:10]:
        return f"{str(start)[:10]} to {str(end)[:10]}"
    if start:
        return str(start)[:10]
    if end:
        return str(end)[:10]
    return None


def raise_if_sp_api_forbidden(
    exc: Exception,
    *,
    client_id: str,
    marketplace: str,
    report_type: str,
    report_params: dict | None = None,
) -> None:
    """Re-raise SP-API permission failures as SPAPIForbiddenError with context."""
    if not is_sp_api_forbidden(exc):
        raise exc

    report_date = extract_report_date(report_params)
    raise SPAPIForbiddenError(
        forbidden_message(report_type),
        client_id=client_id,
        marketplace=marketplace,
        report_type=report_type,
        report_date=report_date,
    ) from exc
