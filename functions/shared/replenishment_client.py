"""Amazon Replenishment API v2022-11-07 client (Subscribe & Save metrics).

Built on the generic ``sp_api_rest`` transport. Replaces the SP-API
GET_FBA_SNS_* reports that Amazon removed on 2025-12-11.

Three operations are exposed:
- ``list_offers``        -> program offer / enrollment details
- ``fetch_sp_metrics``   -> account-level S&S business metrics
- ``fetch_offer_metrics``-> per-ASIN S&S performance (or FORECAST) metrics

The ``listOfferMetrics`` / ``getSellingPartnerMetrics`` operations only accept a
``timeInterval`` no larger than a single unit of ``aggregationFrequency`` (e.g.
one week for ``WEEK``). Callers pass a full ``(start_date, end_date)`` range and
this module loops one aggregation window at a time, concatenating the rows.

Each returned row is a flat-ish dict (the Drive TSV converter flattens any
remaining nesting). Rows are annotated with ``window_start`` / ``window_end`` so
multi-window pulls stay attributable.

NOTE: The exact metric enum names and request-body field names below follow the
Amazon model docs. ``TOTAL_SUBSCRIPTIONS_REVENUE`` and ``SHIPPED_SUBSCRIPTION_UNITS``
are the only metrics guaranteed for both PERFORMANCE and FORECAST, so they are the
safe default. Additional performance metrics can be added to
``DEFAULT_PERFORMANCE_METRICS`` once the account has the required role and the set
is confirmed against the live API.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Iterator

from shared.config import get_marketplace_id
from shared.sp_api_rest import sp_api_request

logger = logging.getLogger(__name__)

_BASE = "/replenishment/2022-11-07"
_PROGRAM_TYPE = "SUBSCRIBE_AND_SAVE"
_PAGE_LIMIT = 100

# Confirmed for both PERFORMANCE and FORECAST time-period types.
FORECAST_METRICS = ["TOTAL_SUBSCRIPTIONS_REVENUE", "SHIPPED_SUBSCRIPTION_UNITS"]
DEFAULT_PERFORMANCE_METRICS = ["TOTAL_SUBSCRIPTIONS_REVENUE", "SHIPPED_SUBSCRIPTION_UNITS"]


def list_offers(client_id: str, marketplace: str) -> list[dict[str, Any]]:
    """Return Subscribe & Save program offers (enrolled ASINs + config)."""
    marketplace_id = get_marketplace_id(marketplace)
    rows: list[dict[str, Any]] = []
    for page in _paginate_offset(
        client_id,
        marketplace,
        f"{_BASE}/offers/search",
        body={
            "filters": {
                "marketplaceId": marketplace_id,
                "programTypes": [_PROGRAM_TYPE],
            },
        },
    ):
        rows.extend(page.get("offers", []))
    return rows


def fetch_sp_metrics(
    client_id: str,
    marketplace: str,
    start_date: date,
    end_date: date,
    *,
    aggregation: str = "WEEK",
    metrics: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return account-level S&S business metrics across the date range."""
    marketplace_id = get_marketplace_id(marketplace)
    use_metrics = metrics or DEFAULT_PERFORMANCE_METRICS
    rows: list[dict[str, Any]] = []

    for win_start, win_end in _iter_windows(start_date, end_date, aggregation):
        body = {
            "aggregationFrequency": aggregation,
            "timeInterval": _time_interval(win_start, win_end),
            "metrics": use_metrics,
            "timePeriodType": "PERFORMANCE",
            "marketplaceId": marketplace_id,
            "programTypes": [_PROGRAM_TYPE],
        }
        resp = sp_api_request(
            client_id, marketplace, "POST",
            f"{_BASE}/sellingPartners/metrics/search", body=body,
        )
        rows.extend(_annotate(resp.get("metrics", []), win_start, win_end))

    return rows


def fetch_offer_metrics(
    client_id: str,
    marketplace: str,
    start_date: date,
    end_date: date,
    *,
    aggregation: str = "WEEK",
    time_period_type: str = "PERFORMANCE",
    metrics: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return per-ASIN S&S metrics across the date range.

    ``time_period_type`` is ``PERFORMANCE`` (past) or ``FORECAST`` (forward
    30/60/90 days, sellers only — limited to ``FORECAST_METRICS``).
    """
    marketplace_id = get_marketplace_id(marketplace)
    if metrics is None:
        metrics = FORECAST_METRICS if time_period_type == "FORECAST" else DEFAULT_PERFORMANCE_METRICS

    rows: list[dict[str, Any]] = []
    for win_start, win_end in _iter_windows(start_date, end_date, aggregation):
        body = {
            "metrics": metrics,
            "filters": {
                "aggregationFrequency": aggregation,
                "timeInterval": _time_interval(win_start, win_end),
                "timePeriodType": time_period_type,
                "marketplaceId": marketplace_id,
                "programTypes": [_PROGRAM_TYPE],
            },
        }
        for page in _paginate_offset(client_id, marketplace, f"{_BASE}/offers/metrics/search", body=body):
            rows.extend(_annotate(page.get("offers", []), win_start, win_end))

    return rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _time_interval(start: date, end: date) -> dict[str, str]:
    """Build an inclusive ISO-8601 UTC time interval for one aggregation window."""
    return {
        "startDate": f"{start.isoformat()}T00:00:00Z",
        "endDate": f"{end.isoformat()}T23:59:59Z",
    }


def _annotate(rows: list[dict[str, Any]], win_start: date, win_end: date) -> list[dict[str, Any]]:
    """Stamp each row with the window it came from for multi-window pulls."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append({"window_start": win_start.isoformat(), "window_end": win_end.isoformat(), **row})
    return out


def _paginate_offset(
    client_id: str,
    marketplace: str,
    path: str,
    *,
    body: dict[str, Any],
    limit: int = _PAGE_LIMIT,
    max_pages: int = 100,
) -> Iterator[dict[str, Any]]:
    """Yield pages for Replenishment endpoints that use offset pagination."""
    offset = 0
    for _ in range(max_pages):
        page_body = {
            **body,
            "pagination": {"limit": limit, "offset": offset},
        }
        resp = sp_api_request(client_id, marketplace, "POST", path, body=page_body)
        yield resp

        row_count = len(resp.get("offers", []) or [])
        pagination = resp.get("pagination") or {}
        next_offset = pagination.get("nextOffset")
        if next_offset is not None:
            offset = int(next_offset)
        elif row_count >= limit:
            offset += limit
        else:
            return


def _iter_windows(start: date, end: date, aggregation: str) -> Iterator[tuple[date, date]]:
    """Yield (window_start, window_end) chunks no larger than one aggregation unit.

    The Replenishment metrics operations reject a ``timeInterval`` longer than a
    single aggregation unit, so a wider requested range is split into per-unit
    windows: 1 day for ``DAY``, 7 days for ``WEEK``, calendar months for ``MONTH``.
    """
    if end < start:
        return

    if aggregation == "MONTH":
        cur = start.replace(day=1)
        while cur <= end:
            month_end = _end_of_month(cur)
            yield cur, month_end
            cur = month_end + timedelta(days=1)
        return

    if aggregation == "WEEK":
        # Amazon's Replenishment WEEK aggregation is Sunday-Saturday. It rejects
        # partial or Monday-Sunday ranges ("startDate and endDate must be of same
        # Week"). Fetch every Amazon week that overlaps the requested range.
        cur = start - timedelta(days=(start.weekday() + 1) % 7)
        while cur <= end:
            yield cur, cur + timedelta(days=6)
            cur += timedelta(days=7)
        return

    step = 1 if aggregation == "DAY" else 7
    cur = start
    while cur <= end:
        win_end = min(cur + timedelta(days=step - 1), end)
        yield cur, win_end
        cur = win_end + timedelta(days=1)


def _end_of_month(d: date) -> date:
    next_month = d.replace(day=28) + timedelta(days=4)
    return next_month - timedelta(days=next_month.day)
