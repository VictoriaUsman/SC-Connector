"""Amazon Finances API v2024-06-19 client (listTransactions).

Replaces the SP-API report GET_DATE_RANGE_FINANCIAL_TRANSACTION_DATA, which
Amazon has deprecated from programmatic (Reports API) requests — it now
rejects every create-report call for it with "not allowed at this time",
regardless of account/role state (confirmed against multiple accounts; see
shared.removed_reports). listTransactions is the documented replacement: a
synchronous, paginated REST endpoint returning financial events for a date
range without waiting for a settlement period to close.

Built directly on ``sp_api_rest.sp_api_request`` rather than its generic
``paginate`` helper: the response envelope here is
``{"payload": {"transactions": [...], "nextToken": ...}}``, which doesn't
match ``paginate``'s ``pagination.nextToken`` / top-level ``nextToken``
assumption (confirmed against Amazon's own OpenAPI model). Replenishment hits
the same kind of mismatch and also rolls its own loop — no shared helper
change needed for a second call shape.

Two Amazon constraints drive the shape of this module:
- ``postedAfter``/``postedBefore`` more than 180 days apart returns an empty
  response (no error) — wide ranges are split into <=179-day windows.
- The rate limit is 0.5 req/s (burst 10), tighter than every other SP-API
  surface this app calls — a fixed pacing delay is applied between requests.

Each returned row is one *breakdown line* of one transaction (transaction
fields repeated per line — a transaction with 3 breakdowns becomes 3 rows),
so fees/refunds/sales sum directly without downstream JSON parsing. A
transaction with no breakdowns at all still produces exactly one row so
nothing is silently dropped.

Field sourcing beyond the obvious ones (confirmed against Amazon's public
finances_2024-06-19.json OpenAPI model, not against a live response, since
several of these are undocumented on the reference page):
- ``settlementId`` / ``relatedOrderId``: ``relatedIdentifiers`` entries keyed
  by ``relatedIdentifierName`` (SETTLEMENT_ID / ORDER_ID).
- ``accountType``: ``sellingPartnerMetadata.accountType``.
- ``marketplaceName``: ``marketplaceDetails.marketplaceName`` (alongside the
  existing ``marketplaceId``).
- ``sku`` / ``quantityShipped`` / ``fulfillmentNetwork``: each item's
  ``contexts`` array, the ``ProductContext`` entry — only populated on rows
  whose breakdown came from an item (the per-item fallback path below), since
  a transaction-level breakdown isn't attributable to one SKU.
- ``releaseDate``: the transaction's ``contexts`` array, the
  ``DeferredContext.maturityDate`` entry — the closest analog to the UI
  Transaction Report's "Transaction Release Date" column; only present on
  deferred transactions.

Still no API equivalent at all (confirmed absent from the model): the UI
report's order city/state/postal and "tax collection model" columns.

Deferred-lifecycle duplicate: when an order's payment transitions from
DEFERRED/DEFERRED_RELEASED to RELEASED inside a single pull's date range,
Amazon returns it as two separate transactionId entries with byte-identical
breakdown amounts -- confirmed against a real ItsBodily/US August 2026 pull
(1,843 orders exactly this shape) and against Amazon's own UI Transaction
Report, which shows each such order exactly once, under RELEASED. See
_dedupe_transactions.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from shared.config import MARKETPLACE_TIMEZONES, get_marketplace_id
from shared.sp_api_rest import sp_api_request

logger = logging.getLogger(__name__)

_BASE = "/finances/2024-06-19/transactions"

# Amazon: "If PostedAfter and PostedBefore are more than 180 days apart, the
# response is empty." Use 179 to stay unambiguously inside the limit.
_MAX_WINDOW_DAYS = 179

# 0.5 req/s sustained (burst 10). Paced conservatively rather than trying to
# ride the burst budget exactly, since a caller's page/window count varies.
_REQUEST_PACING_SECONDS = 2.1

# postedAfter/postedBefore must each be more than 2 minutes before the
# request is made. 3 minutes leaves margin for clock skew + request latency.
_MIN_LOOKBACK = timedelta(minutes=3)


def fetch_transactions(
    client_id: str,
    marketplace: str,
    start_date: date,
    end_date: date,
    *,
    transaction_status: str | None = None,
) -> list[dict[str, Any]]:
    """Return flattened transaction/breakdown rows for [start_date, end_date].

    ``transaction_status``, if given, must be one of DEFERRED / RELEASED /
    DEFERRED_RELEASED (passed straight through to Amazon; not validated here).
    """
    marketplace_id = get_marketplace_id(marketplace)
    txns: list[dict[str, Any]] = []
    first_request = True

    for win_start, win_end in _iter_windows(start_date, end_date):
        params: dict[str, Any] = {
            "postedAfter": _posted_after(win_start, marketplace),
            "postedBefore": _posted_before(win_end, marketplace),
            "marketplaceId": marketplace_id,
        }
        if transaction_status:
            params["transactionStatus"] = transaction_status

        for page in _paginate(client_id, marketplace, params, pace=not first_request):
            first_request = False
            payload = page.get("payload") or {}
            txns.extend(payload.get("transactions") or [])

    rows: list[dict[str, Any]] = []
    for txn in _dedupe_transactions(txns):
        rows.extend(_flatten_transaction(txn))

    return rows


def _paginate(
    client_id: str,
    marketplace: str,
    params: dict[str, Any],
    *,
    pace: bool,
    max_pages: int = 200,
) -> Iterator[dict[str, Any]]:
    """Yield response pages, following ``payload.nextToken``."""
    next_params = dict(params)
    for _ in range(max_pages):
        if pace:
            time.sleep(_REQUEST_PACING_SECONDS)
        pace = True

        resp = sp_api_request(client_id, marketplace, "GET", _BASE, params=next_params)
        yield resp

        token = (resp.get("payload") or {}).get("nextToken")
        if not token:
            return
        next_params = {"nextToken": token}


def _iter_windows(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Yield (window_start, window_end) chunks no larger than 179 days."""
    if end < start:
        return
    cur = start
    while cur <= end:
        win_end = min(cur + timedelta(days=_MAX_WINDOW_DAYS - 1), end)
        yield cur, win_end
        cur = win_end + timedelta(days=1)


def _marketplace_tz(marketplace: str) -> ZoneInfo:
    return ZoneInfo(MARKETPLACE_TIMEZONES.get(marketplace, "UTC"))


def _posted_after(win_start: date, marketplace: str) -> str:
    """Start-of-day for win_start in the marketplace's local calendar day.

    Amazon's own UI Transaction Report buckets by the marketplace's local
    calendar day (Pacific for US), not UTC. A raw UTC day boundary here
    creates a real gap/overlap at the start and end of a pull's range —
    confirmed against a real Aug 2026 ItsBodily/US pull and its Seller
    Central export: every ground-truth row missing from the API pull fell on
    Aug 31, exactly the PDT-vs-UTC boundary window (PDT's "Aug 31" runs
    until 2026-09-01T06:59:59Z, 7 hours past the old UTC-anchored cutoff).
    """
    start_local = datetime(win_start.year, win_start.month, win_start.day, tzinfo=_marketplace_tz(marketplace))
    return start_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _posted_before(win_end: date, marketplace: str) -> str:
    """End-of-day for win_end in the marketplace's local calendar day,
    clamped to more than 2 minutes before now.

    Only the final/most-recent window can land on "today"; every earlier
    window's end-of-day is already safely in the past.
    """
    end_local = datetime(win_end.year, win_end.month, win_end.day, 23, 59, 59, tzinfo=_marketplace_tz(marketplace))
    end_of_day = end_local.astimezone(timezone.utc)
    latest_allowed = datetime.now(timezone.utc) - _MIN_LOOKBACK
    return min(end_of_day, latest_allowed).strftime("%Y-%m-%dT%H:%M:%SZ")


_STATUS_PRIORITY = {"RELEASED": 0, "DEFERRED_RELEASED": 1, "DEFERRED": 2}


def _dedupe_transactions(txns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse deferred-lifecycle duplicate postings of the same order payment.

    See the module docstring. Groups transactions by (relatedOrderId,
    breakdown signature) and, only when a group's statuses *differ*, keeps
    the single most-final one (RELEASED > DEFERRED_RELEASED > DEFERRED).
    A group where every entry shares the same status is left untouched --
    e.g. two RELEASED transactions with identical amounts are plausibly two
    separate real shipments of equal value (an order split across packages),
    not a lifecycle duplicate, and merging those would silently drop real
    money with no confirming evidence either way.

    Transactions with no relatedOrderId (e.g. FundTransfer, standalone fees
    with no order to correlate against) pass through unchanged.
    """
    by_order: dict[str, list[dict[str, Any]]] = {}
    result: list[dict[str, Any]] = []

    for txn in txns:
        order_id = _related_identifier(txn.get("relatedIdentifiers"), "ORDER_ID")
        if not order_id:
            result.append(txn)
            continue
        by_order.setdefault(order_id, []).append(txn)

    for order_txns in by_order.values():
        groups: dict[tuple[tuple[str, float], ...], list[dict[str, Any]]] = {}
        for txn in order_txns:
            groups.setdefault(_breakdown_signature(txn), []).append(txn)

        for group in groups.values():
            statuses = {t.get("transactionStatus") for t in group}
            if len(group) == 1 or len(statuses) == 1:
                result.extend(group)
                continue
            result.append(min(group, key=lambda t: _STATUS_PRIORITY.get(t.get("transactionStatus"), 99)))

    return result


def _breakdown_signature(txn: dict[str, Any]) -> tuple[tuple[str, float], ...]:
    """A (leaf_path, amount) signature identifying what a transaction pays,
    independent of transactionId/postedDate/status -- used by
    _dedupe_transactions to recognize the same payment posted twice."""
    leaves = list(_iter_breakdown_leaves(txn.get("breakdowns")))
    if not leaves:
        for item in txn.get("items") or []:
            leaves.extend(_iter_breakdown_leaves(item.get("breakdowns")))
    return tuple(sorted(
        (leaf_type, round((leaf.get("breakdownAmount") or {}).get("currencyAmount") or 0.0, 2))
        for leaf_type, leaf in leaves
    ))


def _flatten_transaction(txn: dict[str, Any]) -> list[dict[str, Any]]:
    total = txn.get("totalAmount") or {}
    marketplace_details = txn.get("marketplaceDetails") or {}
    seller_metadata = txn.get("sellingPartnerMetadata") or {}
    base = {
        "transactionId": txn.get("transactionId"),
        "transactionType": txn.get("transactionType"),
        "transactionStatus": txn.get("transactionStatus"),
        "postedDate": txn.get("postedDate"),
        "description": txn.get("description"),
        "marketplaceId": marketplace_details.get("marketplaceId"),
        "marketplaceName": marketplace_details.get("marketplaceName"),
        "accountType": seller_metadata.get("accountType"),
        "relatedOrderId": _related_identifier(txn.get("relatedIdentifiers"), "ORDER_ID"),
        "settlementId": _related_identifier(txn.get("relatedIdentifiers"), "SETTLEMENT_ID"),
        "releaseDate": _deferred_maturity_date(txn.get("contexts")),
        "currencyCode": total.get("currencyCode"),
        "totalAmount": total.get("currencyAmount"),
    }

    # Each leaf is tagged with the item it came from (None for transaction-level
    # breakdowns, which aren't attributable to a single SKU) so sku/quantity/
    # fulfillment can ride along per row without a second pass over items.
    leaves = [(leaf_type, leaf, None) for leaf_type, leaf in _iter_breakdown_leaves(txn.get("breakdowns"))]
    if not leaves:
        # No transaction-level breakdowns — fall back to per-item breakdowns
        # (each item can carry its own breakdown tree) before giving up and
        # emitting a single row off totalAmount, so nothing is ever dropped.
        for item in txn.get("items") or []:
            leaves.extend((leaf_type, leaf, item) for leaf_type, leaf in _iter_breakdown_leaves(item.get("breakdowns")))

    if not leaves:
        return [{
            **base, **_product_fields(None),
            "breakdownType": None, "breakdownAmount": total.get("currencyAmount"), "lineIndex": 0,
        }]

    return [
        {
            **base,
            **_product_fields(item),
            "breakdownType": leaf_type,
            "breakdownAmount": (leaf.get("breakdownAmount") or {}).get("currencyAmount"),
            "lineIndex": i,
        }
        for i, (leaf_type, leaf, item) in enumerate(leaves)
    ]


def _iter_breakdown_leaves(
    breakdowns: list[dict[str, Any]] | None, path: tuple[str, ...] = ()
) -> Iterator[tuple[str, dict[str, Any]]]:
    """Recursively walk a breakdown tree, yielding (joined-path, leaf-node) pairs.

    A breakdown with its own nested ``breakdowns`` is a grouping node (e.g.
    "ProductCharges"), not a monetary line item — only leaves (no children)
    carry an amount worth a row. The path is joined with " / " so a nested
    line still reads as e.g. "ProductCharges / Principal".
    """
    for b in breakdowns or []:
        name = b.get("breakdownType") or "Unknown"
        full_path = path + (name,)
        children = b.get("breakdowns")
        if children:
            yield from _iter_breakdown_leaves(children, full_path)
        else:
            yield " / ".join(full_path), b


def _related_identifier(related_identifiers: list[dict[str, Any]] | None, name: str) -> str | None:
    """Look up one entry in ``relatedIdentifiers`` by ``relatedIdentifierName``.

    Amazon's model enumerates ORDER_ID, SETTLEMENT_ID, SHIPMENT_ID,
    FINANCIAL_EVENT_GROUP_ID, REFUND_ID, INVOICE_ID, DISBURSEMENT_ID,
    TRANSFER_ID, DEFERRED_TRANSACTION_ID, RELEASE_TRANSACTION_ID — only
    ORDER_ID and SETTLEMENT_ID are surfaced today.
    """
    for r in related_identifiers or []:
        if r.get("relatedIdentifierName") == name:
            return r.get("relatedIdentifierValue")
    return None


def _product_fields(item: dict[str, Any] | None) -> dict[str, Any]:
    """Pull sku/quantityShipped/fulfillmentNetwork off an item's ProductContext.

    ``Item.contexts`` is a polymorphic list (ProductContext, PaymentsContext,
    DeferredContext, BusinessContext, AmazonPayContext, TimeRangeContext),
    discriminated by ``contextType`` — matched by substring rather than exact
    equality since the literal enum string isn't confirmed against a live
    response, only against Amazon's public OpenAPI model.
    """
    ctx = _find_context(item.get("contexts") if item else None, "product")
    return {
        "sku": (ctx or {}).get("sku"),
        "quantityShipped": (ctx or {}).get("quantityShipped"),
        "fulfillmentNetwork": (ctx or {}).get("fulfillmentNetwork"),
    }


def _deferred_maturity_date(contexts: list[dict[str, Any]] | None) -> str | None:
    """Return DeferredContext.maturityDate — the closest analog to the UI
    Transaction Report's "Transaction Release Date" column. Only meaningful
    for DEFERRED/DEFERRED_RELEASED transactions; None otherwise."""
    ctx = _find_context(contexts, "deferred")
    return (ctx or {}).get("maturityDate")


def _find_context(contexts: list[dict[str, Any]] | None, type_hint: str) -> dict[str, Any] | None:
    for ctx in contexts or []:
        if type_hint in (ctx.get("contextType") or "").lower():
            return ctx
    return None
