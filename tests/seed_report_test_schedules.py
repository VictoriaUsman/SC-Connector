#!/usr/bin/env python3
"""Create (or update) test schedules in Firestore that cover every report type.

Reports are grouped by their timeframe requirements:
  - "daily"   → yesterday strategy (most SP API reports, all Ads reports)
  - "monthly" → last_calendar_month strategy (Brand Analytics that need WEEK/MONTH)
  - "skip"    → non-requestable reports (settlement reports)

Each group becomes one test schedule per API source (sp_api / ads_api).

Usage:
    python tests/seed_report_test_schedules.py [--project PROJECT] [--client CLIENT_ID] [--dry-run]

Re-running the script is idempotent — it deletes any existing "test-report-*"
schedules and recreates them from scratch based on the current report registry.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

# Allow imports from functions/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

from shared.ads_report_config import ADS_REPORT_TYPES

# ---------------------------------------------------------------------------
# Report classification
# ---------------------------------------------------------------------------

_NON_REQUESTABLE = {
    "GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2",
    "GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE",
}

_MONTHLY_ONLY_SP = {
    "GET_BRAND_ANALYTICS_MARKET_BASKET_REPORT",
    "GET_BRAND_ANALYTICS_REPEAT_PURCHASE_REPORT",
    "GET_BRAND_ANALYTICS_ALTERNATE_PURCHASE_REPORT",
    "GET_BRAND_ANALYTICS_SEARCH_QUERY_PERFORMANCE_REPORT",
    "GET_BRAND_ANALYTICS_SEARCH_CATALOG_PERFORMANCE_REPORT",
}

ALL_SP_REPORT_TYPES = [
    "GET_FLAT_FILE_OPEN_LISTINGS_DATA",
    "GET_MERCHANT_LISTINGS_ALL_DATA",
    "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL",
    "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL",
    "GET_SALES_AND_TRAFFIC_REPORT",
    "GET_FBA_MYI_UNSUPPRESSED_INVENTORY_DATA",
    "GET_FBA_ESTIMATED_FBA_FEES_TXT_DATA",
    "GET_AFN_INVENTORY_DATA",
    "GET_LEDGER_SUMMARY_VIEW_DATA",
    "GET_V2_SETTLEMENT_REPORT_DATA_FLAT_FILE_V2",
    "GET_AMAZON_FULFILLED_SHIPMENTS_DATA_GENERAL",
    "GET_FLAT_FILE_RETURNS_DATA_BY_RETURN_DATE",
    "GET_FBA_FULFILLMENT_CUSTOMER_RETURNS_DATA",
    "GET_FBA_FULFILLMENT_REMOVAL_SHIPMENT_DETAIL_DATA",
    "GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT",
    "GET_BRAND_ANALYTICS_MARKET_BASKET_REPORT",
    "GET_BRAND_ANALYTICS_REPEAT_PURCHASE_REPORT",
    "GET_BRAND_ANALYTICS_SEARCH_QUERY_PERFORMANCE_REPORT",
    "GET_BRAND_ANALYTICS_SEARCH_CATALOG_PERFORMANCE_REPORT",
    "GET_MERCHANT_LISTINGS_DATA",
]

ALL_ADS_REPORT_TYPES = list(ADS_REPORT_TYPES.keys())


def classify_sp_reports() -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {"daily": [], "monthly": [], "skip": []}
    for rt in ALL_SP_REPORT_TYPES:
        if rt in _NON_REQUESTABLE:
            groups["skip"].append(rt)
        elif rt in _MONTHLY_ONLY_SP:
            groups["monthly"].append(rt)
        else:
            groups["daily"].append(rt)
    return groups


# ---------------------------------------------------------------------------
# Schedule builders
# ---------------------------------------------------------------------------

def _base_schedule(
    name: str,
    client_ids: list[str],
    api_source: str,
    report_types: list[str],
    marketplace: str,
    timeframe: dict,
    folder_name: str,
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "name": name,
        "client_ids": client_ids,
        "api_source": api_source,
        "report_types": report_types,
        "marketplaces": [marketplace],
        "frequency": "daily",
        "schedule_config": {"type": "daily", "time": "06:00"},
        "timeframe": timeframe,
        "folder_name": folder_name,
        "subfolder_strategy": "date",
        "reconciliation_days": [],
        "report_params": {},
        "is_active": False,
        "created_at": now,
        "updated_at": now,
    }


def build_test_schedules(
    client_ids: list[str],
    marketplace: str = "US",
) -> list[dict]:
    sp = classify_sp_reports()
    schedules: list[dict] = []

    if sp["daily"]:
        schedules.append(_base_schedule(
            name="Test: SP Daily Reports",
            client_ids=client_ids,
            api_source="sp_api",
            report_types=sp["daily"],
            marketplace=marketplace,
            timeframe={"strategy": "yesterday"},
            folder_name="test-reports-daily",
        ))

    if sp["monthly"]:
        schedules.append(_base_schedule(
            name="Test: SP Monthly Reports (Brand Analytics)",
            client_ids=client_ids,
            api_source="sp_api",
            report_types=sp["monthly"],
            marketplace=marketplace,
            timeframe={"strategy": "last_calendar_month"},
            folder_name="test-reports-monthly",
        ))

    if ALL_ADS_REPORT_TYPES:
        schedules.append(_base_schedule(
            name="Test: Ads Daily Reports",
            client_ids=client_ids,
            api_source="ads_api",
            report_types=ALL_ADS_REPORT_TYPES,
            marketplace=marketplace,
            timeframe={"strategy": "yesterday"},
            folder_name="test-reports-ads",
        ))

    return schedules


# ---------------------------------------------------------------------------
# Firestore operations
# ---------------------------------------------------------------------------

TEST_SCHEDULE_PREFIX = "test-report-"


def sync_to_firestore(
    project: str,
    schedules: list[dict],
    dry_run: bool = False,
) -> None:
    from google.cloud import firestore

    db = firestore.Client(project=project)
    coll = db.collection("schedules")

    existing = [
        doc for doc in coll.stream()
        if (doc.to_dict().get("folder_name") or "").startswith("test-reports-")
    ]

    if existing:
        print(f"  Removing {len(existing)} existing test schedule(s)...")
        if not dry_run:
            for doc in existing:
                doc.reference.delete()

    for sched in schedules:
        report_count = len(sched["report_types"])
        print(f"  + {sched['name']}  ({report_count} report types, {sched['timeframe']['strategy']})")
        if not dry_run:
            coll.add(sched)

    skipped = classify_sp_reports()["skip"]
    if skipped:
        print(f"\n  Skipped (non-requestable): {', '.join(skipped)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed Firestore with test schedules covering all report types",
    )
    parser.add_argument(
        "--project",
        default=os.environ.get("GCP_PROJECT", "kalilos-connector-staging"),
    )
    parser.add_argument(
        "--client",
        default="Betallic",
        help="Client ID to use (must have SP + Ads credentials)",
    )
    parser.add_argument(
        "--marketplace",
        default="US",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")
    args = parser.parse_args()

    print(f"Project:     {args.project}")
    print(f"Client:      {args.client}")
    print(f"Marketplace: {args.marketplace}")
    print()

    schedules = build_test_schedules(
        client_ids=[args.client],
        marketplace=args.marketplace,
    )

    total_reports = sum(len(s["report_types"]) for s in schedules)
    print(f"Creating {len(schedules)} test schedule(s) covering {total_reports} report types:\n")

    sync_to_firestore(args.project, schedules, dry_run=args.dry_run)

    if args.dry_run:
        print("\n  (dry run — no changes written)")
    else:
        print(f"\nDone. Schedules are created as INACTIVE — trigger via 'Run Now' in the UI.")


if __name__ == "__main__":
    main()
