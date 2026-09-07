"""Maps Finances API (v2024-06-19) breakdown lines onto the category buckets
used by Amazon's own Seller Central "Transaction Report" CSV (Payments >
Reports > Transaction Report — the SP-API report type
GET_DATE_RANGE_FINANCIAL_TRANSACTION_DATA, which Amazon has removed from the
Reports API; see shared.removed_reports and shared.finances_client).

listTransactions returns raw, un-bucketed breakdown lines (one row per leaf —
see finances_client._flatten_transaction), while the UI report pre-sums dozens
of individual charge/credit types into named columns: product sales, product
sales tax, shipping credits(+tax), gift wrap credits(+tax), regulatory fee
(+tax), promotional rebates(+tax), marketplace withheld tax, selling fees,
fba fees, other transaction fees, other. Reconciling API-pulled totals
against that report means re-deriving the same buckets from breakdownType.

IMPORTANT — this is a best-effort heuristic, not a lookup table. Amazon does
not publish a closed enum for breakdownType: the Finances API OpenAPI model
and reference docs describe it only as "the type of charge", a free-form
string (confirmed 2026-09 against both). ``bucket_for`` therefore classifies
by keyword, and anything it doesn't recognize comes back as UNMAPPED rather
than being silently folded into "other" — check logs for UNMAPPED breakdown
types against real account data and add a rule below rather than assuming
the bucket totals are complete.

One documented ambiguity this module can only approximate: on the UI report,
an FBA fulfillment fee charged as part of an "Order" row lands in the
`fba fees` column, but a standalone FBA-only event (e.g. "FBA Customer
Return Fee") lands in `other transaction fees` under its "per-item fees"
definition — same underlying fee family, different bucket, driven by the
UI's `type` column rather than by the fee name itself. Finances API's closest
analog is ``transactionType`` (also not fully enumerated — Amazon's own
reference docs currently document only "Shipment" as a possible value even
though real accounts see others). Pass it as ``transaction_type`` to get the
same split; without it, FBA-shaped fees default to OTHER_TRANSACTION_FEES,
since that's the wider of the UI's two definitions.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Bucket names — one per column of Amazon's UI Transaction Report.
PRODUCT_SALES = "product_sales"
PRODUCT_SALES_TAX = "product_sales_tax"
SHIPPING_CREDITS = "shipping_credits"
SHIPPING_CREDITS_TAX = "shipping_credits_tax"
GIFT_WRAP_CREDITS = "gift_wrap_credits"
GIFT_WRAP_CREDITS_TAX = "gift_wrap_credits_tax"
REGULATORY_FEE = "regulatory_fee"
TAX_ON_REGULATORY_FEE = "tax_on_regulatory_fee"
PROMOTIONAL_REBATES = "promotional_rebates"
PROMOTIONAL_REBATES_TAX = "promotional_rebates_tax"
MARKETPLACE_WITHHELD_TAX = "marketplace_withheld_tax"
SELLING_FEES = "selling_fees"
FBA_FEES = "fba_fees"
OTHER_TRANSACTION_FEES = "other_transaction_fees"
OTHER = "other"

# Not a UI column — signals bucket_for saw a breakdownType none of the rules
# below recognize. Treat any row in this bucket as needing a new rule, not as
# correctly classified "other".
UNMAPPED = "unmapped"

ALL_BUCKETS = (
    PRODUCT_SALES, PRODUCT_SALES_TAX,
    SHIPPING_CREDITS, SHIPPING_CREDITS_TAX,
    GIFT_WRAP_CREDITS, GIFT_WRAP_CREDITS_TAX,
    REGULATORY_FEE, TAX_ON_REGULATORY_FEE,
    PROMOTIONAL_REBATES, PROMOTIONAL_REBATES_TAX,
    MARKETPLACE_WITHHELD_TAX,
    SELLING_FEES, FBA_FEES, OTHER_TRANSACTION_FEES, OTHER,
)

# Ordered (specific -> general) keyword rules. Matched against the lowercased,
# space/slash/underscore-stripped breakdownType leaf path (nested paths join
# with " / " — see finances_client._iter_breakdown_leaves — so e.g.
# "ProductCharges / Principal" is checked as "productchargesprincipal").
# First match wins.
_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    # (all_of, none_of, bucket)
    (("giftwrap",), ("tax", "chargeback"), GIFT_WRAP_CREDITS),
    (("giftwrap", "tax"), (), GIFT_WRAP_CREDITS_TAX),
    (("giftwrap",), ("tax",), OTHER_TRANSACTION_FEES),  # chargeback — mirrors shipping below
    (("shipping",), ("tax", "chargeback", "holdback", "shippinghb"), SHIPPING_CREDITS),
    (("shipping", "tax"), (), SHIPPING_CREDITS_TAX),
    (("shipping",), ("tax",), OTHER_TRANSACTION_FEES),  # chargeback/holdback, incl. "ShippingHB" abbreviation
    (("regulatoryfee",), ("tax",), REGULATORY_FEE),
    (("regulatoryfee", "tax"), (), TAX_ON_REGULATORY_FEE),
    (("regulatory", "tax"), (), TAX_ON_REGULATORY_FEE),
    (("regulatory",), ("tax",), REGULATORY_FEE),
    (("promotion",), ("tax",), PROMOTIONAL_REBATES),
    (("promotion", "tax"), (), PROMOTIONAL_REBATES_TAX),
    # "PromoRebates" is the real breakdownType Amazon sends (confirmed 2026-09
    # against a live ItsBodily/US pull) — doesn't contain "promotion", so it
    # needs its own rule rather than relying on the keyword above.
    (("promorebates",), ("tax",), PROMOTIONAL_REBATES),
    (("promorebates", "tax"), (), PROMOTIONAL_REBATES_TAX),
    (("marketplacefacilitatortax",), (), MARKETPLACE_WITHHELD_TAX),
    (("marketplacewithheldtax",), (), MARKETPLACE_WITHHELD_TAX),
    (("withheldtax",), (), MARKETPLACE_WITHHELD_TAX),
    (("commission",), (), SELLING_FEES),
    (("referralfee",), (), SELLING_FEES),
    (("variableclosingfee",), (), SELLING_FEES),
    (("closingfee",), (), SELLING_FEES),
    (("perunitfulfillmentfee",), (), FBA_FEES),
    (("weightbasedfee",), (), FBA_FEES),
    (("chargeback",), (), OTHER_TRANSACTION_FEES),
    (("holdback",), (), OTHER_TRANSACTION_FEES),
    (("peritemfee",), (), OTHER_TRANSACTION_FEES),
    (("taxcollectionfee",), (), OTHER_TRANSACTION_FEES),
    (("fba",), (), OTHER_TRANSACTION_FEES),  # standalone FBA-only events — see module docstring
    (("productcharges", "tax"), (), PRODUCT_SALES_TAX),
    (("principal",), (), PRODUCT_SALES),
    (("productcharges",), (), PRODUCT_SALES),
    (("tax",), (), PRODUCT_SALES_TAX),
)

# transactionType values (best-effort — see module docstring) under which an
# FBA-shaped fee is folded into `fba fees` instead of `other transaction
# fees`, matching the UI report's "part of an Order row" distinction.
_ORDER_TRANSACTION_TYPES = frozenset({"shipment", "order", "orderretrocharge"})


def bucket_for(breakdown_type: str | None, transaction_type: str | None = None) -> str:
    """Classify one flattened Finances API row into a UI-report bucket.

    ``breakdown_type`` is the (possibly " / "-joined) leaf path from
    ``finances_client._flatten_transaction``. ``transaction_type`` narrows the
    FBA-fee split described in the module docstring; omit it to get the wider
    (``other_transaction_fees``) default.

    Returns one of the bucket constants above, or ``UNMAPPED`` if no rule
    matched — callers should log/collect UNMAPPED rows rather than sum them
    into a real column, since an unmapped charge silently dropped from every
    bucket is worse than one left uncategorized.
    """
    if not breakdown_type:
        return UNMAPPED

    key = breakdown_type.lower().replace(" ", "").replace("/", "").replace("_", "")

    for all_of, none_of, bucket in _RULES:
        if all(term in key for term in all_of) and not any(term in key for term in none_of):
            if bucket == FBA_FEES and (transaction_type or "").lower().replace(" ", "") not in _ORDER_TRANSACTION_TYPES:
                # Only trust the FBA_FEES rules (perunitfulfillmentfee /
                # weightbasedfee) when we can confirm this rode along with an
                # order; otherwise treat it like the standalone-event rule.
                return OTHER_TRANSACTION_FEES
            return bucket

    logger.warning("finance_buckets: no bucket rule matched breakdownType=%r — add a rule", breakdown_type)
    return UNMAPPED


def bucket_rows(rows: list[dict]) -> dict[str, float]:
    """Sum a list of flattened SP_FINANCE_TRANSACTIONS rows into UI buckets.

    Each row must have ``breakdownType``, ``breakdownAmount``, and optionally
    ``transactionType`` (see finances_client._flatten_transaction). Rows with
    a ``breakdownAmount`` of ``None`` contribute nothing. Returns a dict with
    every bucket in ALL_BUCKETS plus UNMAPPED, all present even if zero, so
    downstream code can compare against the UI report's columns directly
    without a KeyError on an empty bucket.
    """
    totals = {bucket: 0.0 for bucket in (*ALL_BUCKETS, UNMAPPED)}
    for row in rows:
        amount = row.get("breakdownAmount")
        if amount is None:
            continue
        bucket = bucket_for(row.get("breakdownType"), row.get("transactionType"))
        totals[bucket] += amount
    return totals
