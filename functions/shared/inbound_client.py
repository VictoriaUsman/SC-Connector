"""Amazon FBA Inbound API v2024-03-20 client — item expiration dates.

Send to Amazon asks for an expiration date on dated products, and Amazon keeps
it on each inbound plan item. Neither the Reports API nor the legacy v0
inbound shipment API exposes it, so this pulls it from the v2024-03-20
endpoints:

- ``GET /inbound/fba/2024-03-20/inboundPlans`` — list plans (newest first).
- ``GET /inbound/fba/2024-03-20/inboundPlans/{id}/items`` — a plan's items,
  each carrying ``msku`` / ``asin`` / ``fnsku`` / ``quantity`` /
  ``expiration`` / ``manufacturingLotCode``.

Field names are per Amazon's published model (recalled, not yet confirmed
against a live response — same caveat as ``replenishment_client`` and
``finances_client``). Confirm against a real pull before relying on them.

Both endpoints page with a ``paginationToken`` query param fed from
``pagination.nextToken``, which the generic ``sp_api_rest.paginate`` helper
doesn't support (it re-sends the token as ``nextToken``), so loops are
hand-rolled here, like the Finances client.

Requires the "Amazon Fulfillment" role on the seller's SP-API authorization;
without it Amazon returns 403, which propagates so the job fails visibly
rather than producing an empty file.

Date scoping: the pull covers plans *last updated on or after* ``start_date``
(UTC). Plans are listed newest-updated first, so paging stops at the first
plan older than that.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone
from typing import Any, Iterator

from shared.config import get_marketplace_id
from shared.sp_api_rest import SPAPIRequestError, sp_api_request

import requests

logger = logging.getLogger(__name__)

_BASE = "/inbound/fba/2024-03-20/inboundPlans"

# listInboundPlans / listInboundPlanItems: 2 req/s sustained (burst 6).
_REQUEST_PACING_SECONDS = 0.6
_PAGE_SIZE = 30  # plans: max 30
_ITEMS_PAGE_SIZE = 100  # items: max 100
_MAX_PAGES = 200


def fetch_inbound_expirations(
    client_id: str,
    marketplace: str,
    start_date: date,
) -> list[dict[str, Any]]:
    """Return one row per inbound plan item that has an expiration date."""
    marketplace_id = get_marketplace_id(marketplace)
    rows: list[dict[str, Any]] = []

    for plan in _iter_plans(client_id, marketplace, start_date):
        if plan.get("status") == "VOIDED":
            continue
        if marketplace_id not in (plan.get("marketplaceIds") or []):
            continue

        plan_id = plan.get("inboundPlanId")
        try:
            items = list(_iter_items(client_id, marketplace, plan_id))
        except (SPAPIRequestError, requests.RequestException) as exc:
            # One bad plan must not blank out the others.
            logger.warning(
                "Inbound plan items fetch failed; skipping plan",
                extra={"client_id": client_id, "inbound_plan_id": plan_id, "error": str(exc)[:200]},
            )
            continue

        for item in items:
            if not item.get("expiration"):
                continue
            rows.append({
                "inbound_plan_id": plan_id,
                "plan_name": plan.get("name"),
                "plan_status": plan.get("status"),
                "plan_last_updated": plan.get("lastUpdatedAt"),
                "msku": item.get("msku"),
                "asin": item.get("asin"),
                "fnsku": item.get("fnsku"),
                "quantity": item.get("quantity"),
                "expiration": item.get("expiration"),
                "lot_code": item.get("manufacturingLotCode"),
            })

    return rows


def _iter_plans(client_id: str, marketplace: str, start_date: date) -> Iterator[dict[str, Any]]:
    """Yield plans updated on/after *start_date*, newest-updated first.

    A failure here is not caught: no plan list means no data, and that must
    surface as a failed job.
    """
    params: dict[str, Any] = {
        "pageSize": _PAGE_SIZE,
        "sortBy": "LAST_UPDATED_TIME",
        "sortOrder": "DESC",
    }
    for page_no in range(_MAX_PAGES):
        if page_no:
            time.sleep(_REQUEST_PACING_SECONDS)
        resp = sp_api_request(client_id, marketplace, "GET", _BASE, params=params)

        for plan in resp.get("inboundPlans") or []:
            updated = _parse_ts(plan.get("lastUpdatedAt"))
            if updated is not None and updated.date() < start_date:
                return  # sorted newest-first: everything after is older too
            yield plan

        token = (resp.get("pagination") or {}).get("nextToken")
        if not token:
            return
        params = {"pageSize": _PAGE_SIZE, "paginationToken": token}


def _iter_items(client_id: str, marketplace: str, plan_id: str) -> Iterator[dict[str, Any]]:
    path = f"{_BASE}/{plan_id}/items"
    params: dict[str, Any] = {"pageSize": _ITEMS_PAGE_SIZE}
    for _ in range(_MAX_PAGES):
        time.sleep(_REQUEST_PACING_SECONDS)
        resp = sp_api_request(client_id, marketplace, "GET", path, params=params)
        yield from resp.get("items") or []

        token = (resp.get("pagination") or {}).get("nextToken")
        if not token:
            return
        params = {"pageSize": _ITEMS_PAGE_SIZE, "paginationToken": token}


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
