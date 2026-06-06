"""Amazon Vendor (1P) SP-API report registry.

Vendor Central accounts authorize through a distinct SP-API role than Seller
Central, but the report pipeline is identical: create_report → poll → download.
This module is the single source of truth for the vendor report types so the
create_report option-injection, the JSON→TSV converter, and the Drive format
heuristic stay in sync.

The three domains mirror what the team already reads in Vendor Central:
- Sales / orders (ordered = purchase-order demand, shipped) across the SOURCING
  and MANUFACTURING distributor views — ``GET_VENDOR_SALES_REPORT``.
- ARA traffic & conversion (glance views) — ``GET_VENDOR_TRAFFIC_REPORT``.
- Inventory health (sellable / unsellable / aged on-hand) across distributor
  views — ``GET_VENDOR_INVENTORY_REPORT``.
"""

from __future__ import annotations

VENDOR_SALES_REPORT = "GET_VENDOR_SALES_REPORT"
VENDOR_TRAFFIC_REPORT = "GET_VENDOR_TRAFFIC_REPORT"
VENDOR_INVENTORY_REPORT = "GET_VENDOR_INVENTORY_REPORT"

# All vendor SP-API report types this connector can request.
VENDOR_SP_REPORT_TYPES: frozenset[str] = frozenset({
    VENDOR_SALES_REPORT,
    VENDOR_TRAFFIC_REPORT,
    VENDOR_INVENTORY_REPORT,
})

# Vendor reports return nested JSON; these are the top-level array keys the
# converter flattens into TSV sections (aggregate + per-ASIN breakdown).
VENDOR_REPORT_ARRAY_KEYS: dict[str, list[str]] = {
    VENDOR_SALES_REPORT: ["salesAggregate", "salesByAsin"],
    VENDOR_TRAFFIC_REPORT: ["trafficByAsin"],
    VENDOR_INVENTORY_REPORT: ["inventoryAggregate", "inventoryByAsin"],
}

# Allowed reportPeriod values per vendor report (first = default fallback).
VENDOR_REPORT_PERIODS: dict[str, list[str]] = {
    VENDOR_SALES_REPORT: ["DAY", "WEEK", "MONTH", "QUARTER", "YEAR"],
    VENDOR_TRAFFIC_REPORT: ["DAY", "WEEK", "MONTH", "QUARTER", "YEAR"],
    VENDOR_INVENTORY_REPORT: ["DAY", "WEEK", "MONTH", "QUARTER", "YEAR"],
}

# reportOptions injected by create_report when the caller did not supply them.
# distributorView selects SOURCING (what Amazon sourced/ordered) vs MANUFACTURING
# (manufacturing/drop-ship view); a schedule can request both views at once by
# passing ``distributorView: ["SOURCING", "MANUFACTURING"]`` (fanned out by the
# workflow launcher's report-option variant expansion). sellingProgram defaults
# to RETAIL (the standard 1P program).
VENDOR_REPORT_DEFAULT_OPTIONS: dict[str, dict[str, str]] = {
    VENDOR_SALES_REPORT: {"distributorView": "SOURCING", "sellingProgram": "RETAIL"},
    VENDOR_TRAFFIC_REPORT: {},
    VENDOR_INVENTORY_REPORT: {"distributorView": "SOURCING", "sellingProgram": "RETAIL"},
}


def is_vendor_report_type(report_type: str) -> bool:
    """Return True if *report_type* is a vendor (1P) SP-API report."""
    return report_type in VENDOR_SP_REPORT_TYPES
