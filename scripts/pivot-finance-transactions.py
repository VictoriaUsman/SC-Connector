#!/usr/bin/env python3
"""Pivot a raw SP_FINANCE_TRANSACTIONS TSV (one row per breakdown line, as
produced by fetch_api's Drive upload -- see shared.finances_client and
shared.report_converter.rows_to_tsv) into the wide 31-column CSV shape of
the legacy GET_DATE_RANGE_FINANCIAL_TRANSACTION_DATA "Transaction Report",
for reconciling against it.

Grouping: one output row per (transactionId, sku) -- matches the report's
grain of one row per order/item/adjustment event. Breakdown lines with no
attributable item (sku blank) group under sku="".

Any breakdownType that shared.finance_buckets.bucket_for can't classify
(UNMAPPED) is folded into the "other" column rather than dropped, but its
raw type string is collected and printed as a warning so it's visible for
review -- see finance_buckets' module docstring on why UNMAPPED is kept
separate from "other" internally.

order city / order state / order postal / tax collection model are left
blank: confirmed absent from the Finances API v2024-06-19 model (see
shared.finances_client module docstring) and from every other SP-API
surface this app calls.

"FundTransfer" (a disbursement/bank-transfer event) is bucketed into "other"
like any other unmapped type, but with its sign flipped first. Confirmed
2026-09 against a real July pull: Amazon's own UI report *does* include
Transfer-type rows in "other" (its own "type" column shows "Transfer" with
a real dollar amount there -- an earlier assumption that it was absent was
wrong, based on a sample window that happened not to include one) -- but
the Finances API records the transfer amount with the opposite sign from
what the UI report shows (e.g. +72,276.61 here vs. the UI's negative
equivalent), so it must be negated to land in the same place as ground
truth, not just included as-is.

AdvertisingFee is *not* special-cased here anymore -- shared.finance_buckets
classifies it directly into "other transaction fees" (confirmed exact-match
against a real July pull's ground truth for that column).

Usage:
    python scripts/pivot-finance-transactions.py --in raw.tsv --out wide.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

from shared.finance_buckets import UNMAPPED, bucket_for  # noqa: E402

_BUCKET_TO_COLUMN = {
    "product_sales": "product sales",
    "product_sales_tax": "product sales tax",
    "shipping_credits": "shipping credits",
    "shipping_credits_tax": "shipping credits tax",
    "gift_wrap_credits": "gift wrap credits",
    "gift_wrap_credits_tax": "giftwrap credits tax",
    "regulatory_fee": "Regulatory Fee",
    "tax_on_regulatory_fee": "Tax On Regulatory Fee",
    "promotional_rebates": "promotional rebates",
    "promotional_rebates_tax": "promotional rebates tax",
    "marketplace_withheld_tax": "marketplace withheld tax",
    "selling_fees": "selling fees",
    "fba_fees": "fba fees",
    "other_transaction_fees": "other transaction fees",
    "other": "other",
}

_NUMERIC_COLUMNS = list(_BUCKET_TO_COLUMN.values())


def _normalize(breakdown_type: str) -> str:
    return breakdown_type.lower().replace(" ", "").replace("/", "").replace("_", "")


def _is_fund_transfer(breakdown_type: str | None) -> bool:
    return bool(breakdown_type) and "fundtransfer" in _normalize(breakdown_type)


_OUTPUT_COLUMNS = [
    "Year-Month", "date/time", "settlement id", "type", "order id", "sku",
    "description", "quantity", "marketplace", "account type", "fulfillment",
    "order city", "order state", "order postal", "tax collection model",
    *_NUMERIC_COLUMNS,
    "total",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="in_path", required=True, help="Raw TSV pulled from Drive")
    parser.add_argument("--out", dest="out_path", required=True, help="Wide 31-column CSV to write")
    args = parser.parse_args()

    with open(args.in_path, newline="", encoding="utf-8") as f:
        first_line = f.readline()
        f.seek(0)
        # The Drive upload is a real TSV, but a copy re-exported from Google
        # Sheets (e.g. downloaded after pasting the TSV in) comes back
        # comma-delimited -- sniff rather than assume.
        delimiter = "\t" if "\t" in first_line else ","
        raw_rows = list(csv.DictReader(f, delimiter=delimiter))

    groups: "OrderedDict[tuple[str, str], dict]" = OrderedDict()
    unmapped_types: set[str] = set()
    transfer_count = 0
    transfer_amount = 0.0

    for row in raw_rows:
        breakdown_type = row.get("breakdownType") or None
        key = (row.get("transactionId", ""), row.get("sku") or "")
        group = groups.get(key)
        if group is None:
            group = {
                "postedDate": row.get("postedDate", ""),
                "settlementId": row.get("settlementId", ""),
                "transactionType": row.get("transactionType", ""),
                "relatedOrderId": row.get("relatedOrderId", ""),
                "sku": row.get("sku", ""),
                "description": row.get("description", ""),
                "quantityShipped": row.get("quantityShipped", ""),
                "marketplaceName": row.get("marketplaceName", ""),
                "accountType": row.get("accountType", ""),
                "fulfillmentNetwork": row.get("fulfillmentNetwork", ""),
                "buckets": {col: 0.0 for col in _NUMERIC_COLUMNS},
            }
            groups[key] = group

        amount_str = row.get("breakdownAmount") or ""
        if amount_str == "":
            continue
        amount = float(amount_str)

        if _is_fund_transfer(breakdown_type):
            transfer_count += 1
            transfer_amount += amount
            group["buckets"]["other"] += -amount  # sign-flip -- see module docstring
            continue

        bucket = bucket_for(breakdown_type, row.get("transactionType"))
        if bucket == UNMAPPED:
            if breakdown_type:
                unmapped_types.add(breakdown_type)
            column = "other"
        else:
            column = _BUCKET_TO_COLUMN[bucket]
        group["buckets"][column] += amount

    out_rows = []
    for group in groups.values():
        posted_date = group["postedDate"]
        year_month = posted_date[:7] if posted_date else ""
        buckets = {col: round(v, 2) for col, v in group["buckets"].items()}
        out_rows.append({
            "Year-Month": year_month,
            "date/time": posted_date,
            "settlement id": group["settlementId"],
            "type": group["transactionType"],
            "order id": group["relatedOrderId"],
            "sku": group["sku"],
            "description": group["description"],
            "quantity": group["quantityShipped"],
            "marketplace": group["marketplaceName"],
            "account type": group["accountType"],
            "fulfillment": group["fulfillmentNetwork"],
            "order city": "",
            "order state": "",
            "order postal": "",
            "tax collection model": "",
            **buckets,
            "total": round(sum(buckets.values()), 2),
        })

    with open(args.out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"Wrote {len(out_rows)} rows to {args.out_path}")
    if unmapped_types:
        print(
            f"WARNING: {len(unmapped_types)} unmapped breakdownType(s) folded into 'other': "
            + ", ".join(sorted(unmapped_types)),
            file=sys.stderr,
        )
    if transfer_count:
        print(
            f"NOTE: {transfer_count} FundTransfer row(s) totaling {round(transfer_amount, 2)} "
            f"folded into 'other' with sign flipped -- see module docstring",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
