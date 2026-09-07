"""Tests for shared.finances_client — window chunking, breakdown flattening,
pagination, and request shape for the Finances API v2024-06-19 listTransactions
replacement for the deprecated GET_DATE_RANGE_FINANCIAL_TRANSACTION_DATA report."""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")


class TestIterWindows:
    def test_splits_ranges_over_179_days(self):
        from shared.finances_client import _iter_windows

        wins = list(_iter_windows(date(2026, 1, 1), date(2026, 8, 1)))
        # 2026-01-01 .. 2026-08-01 is 213 days -> two windows.
        assert wins == [
            (date(2026, 1, 1), date(2026, 6, 28)),
            (date(2026, 6, 29), date(2026, 8, 1)),
        ]

    def test_short_range_is_a_single_window(self):
        from shared.finances_client import _iter_windows

        wins = list(_iter_windows(date(2026, 8, 1), date(2026, 8, 31)))
        assert wins == [(date(2026, 8, 1), date(2026, 8, 31))]

    def test_empty_when_end_before_start(self):
        from shared.finances_client import _iter_windows

        assert list(_iter_windows(date(2026, 1, 10), date(2026, 1, 1))) == []


class TestPostedBefore:
    def test_clamped_to_more_than_two_minutes_before_now(self):
        from shared.finances_client import _posted_before

        # A window ending "today" must not produce a postedBefore Amazon would
        # reject for being too close to (or after) the request time.
        today = datetime.now(timezone.utc).date()
        result = _posted_before(today, "US")
        parsed = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        assert parsed <= datetime.now(timezone.utc)

    def test_past_window_uses_end_of_day_in_marketplace_local_time(self):
        from shared.finances_client import _posted_before

        # 2020-01-01 23:59:59 PST (America/Los_Angeles, no DST in January) is
        # 2020-01-02T07:59:59Z -- not the UTC calendar day's own end-of-day.
        assert _posted_before(date(2020, 1, 1), "US") == "2020-01-02T07:59:59Z"

    def test_unmapped_marketplace_falls_back_to_utc(self):
        from shared.finances_client import _posted_before

        assert _posted_before(date(2020, 1, 1), "ZZ") == "2020-01-01T23:59:59Z"


class TestPostedAfter:
    def test_start_of_day_in_marketplace_local_time(self):
        from shared.finances_client import _posted_after

        # 2026-01-01 00:00:00 PST is 2026-01-01T08:00:00Z, not midnight UTC.
        assert _posted_after(date(2026, 1, 1), "US") == "2026-01-01T08:00:00Z"

    def test_reflects_daylight_saving_offset(self):
        from shared.finances_client import _posted_after

        # 2026-06-29 is inside PDT (UTC-7), not PST (UTC-8).
        assert _posted_after(date(2026, 6, 29), "US") == "2026-06-29T07:00:00Z"


class TestFlattenTransaction:
    def test_multiple_breakdown_lines_become_multiple_rows(self):
        from shared.finances_client import _flatten_transaction

        txn = {
            "transactionId": "txn-1",
            "transactionType": "Shipment",
            "transactionStatus": "RELEASED",
            "postedDate": "2026-08-15T00:00:00Z",
            "description": "Order Payment",
            "totalAmount": {"currencyCode": "USD", "currencyAmount": 43.25},
            "marketplaceDetails": {"marketplaceId": "ATVPDKIKX0DER"},
            "relatedIdentifiers": [
                {"relatedIdentifierName": "ORDER_ID", "relatedIdentifierValue": "111-222"},
            ],
            "breakdowns": [
                {"breakdownType": "Principal", "breakdownAmount": {"currencyCode": "USD", "currencyAmount": 45.0}},
                {"breakdownType": "Shipping", "breakdownAmount": {"currencyCode": "USD", "currencyAmount": 5.0}},
                {"breakdownType": "MarketplaceFee", "breakdownAmount": {"currencyCode": "USD", "currencyAmount": -6.75}},
            ],
        }

        rows = _flatten_transaction(txn, "US")

        assert len(rows) == 3
        for row in rows:
            assert row["transactionId"] == "txn-1"
            assert row["relatedOrderId"] == "111-222"
            assert row["currencyCode"] == "USD"
        assert [r["breakdownType"] for r in rows] == ["Principal", "Shipping", "MarketplaceFee"]
        assert [r["breakdownAmount"] for r in rows] == [45.0, 5.0, -6.75]
        assert [r["lineIndex"] for r in rows] == [0, 1, 2]

    def test_no_breakdowns_produces_one_fallback_row(self):
        from shared.finances_client import _flatten_transaction

        txn = {
            "transactionId": "txn-2",
            "totalAmount": {"currencyCode": "USD", "currencyAmount": 10.0},
        }

        rows = _flatten_transaction(txn, "US")

        assert len(rows) == 1
        assert rows[0]["breakdownType"] is None
        assert rows[0]["breakdownAmount"] == 10.0
        assert rows[0]["lineIndex"] == 0

    def test_falls_back_to_item_breakdowns_when_no_transaction_level_breakdowns(self):
        from shared.finances_client import _flatten_transaction

        txn = {
            "transactionId": "txn-3",
            "totalAmount": {"currencyCode": "USD", "currencyAmount": 20.0},
            "items": [
                {
                    "breakdowns": [
                        {"breakdownType": "Principal", "breakdownAmount": {"currencyCode": "USD", "currencyAmount": 20.0}},
                    ],
                },
            ],
        }

        rows = _flatten_transaction(txn, "US")

        assert len(rows) == 1
        assert rows[0]["breakdownType"] == "Principal"
        assert rows[0]["breakdownAmount"] == 20.0

    def test_nested_breakdowns_flatten_to_leaf_rows_with_joined_path(self):
        from shared.finances_client import _flatten_transaction

        txn = {
            "transactionId": "txn-4",
            "totalAmount": {"currencyCode": "USD", "currencyAmount": 100.0},
            "breakdowns": [
                {
                    "breakdownType": "ProductCharges",
                    "breakdowns": [
                        {"breakdownType": "Principal", "breakdownAmount": {"currencyCode": "USD", "currencyAmount": 90.0}},
                        {"breakdownType": "Tax", "breakdownAmount": {"currencyCode": "USD", "currencyAmount": 10.0}},
                    ],
                },
            ],
        }

        rows = _flatten_transaction(txn, "US")

        # The grouping node ("ProductCharges") itself is not a row — only its
        # two leaves are, and their type carries the joined path.
        assert len(rows) == 2
        assert [r["breakdownType"] for r in rows] == [
            "ProductCharges / Principal", "ProductCharges / Tax",
        ]

    def test_missing_related_order_id_is_none(self):
        from shared.finances_client import _flatten_transaction

        rows = _flatten_transaction({"transactionId": "txn-5", "totalAmount": {}}, "US")
        assert rows[0]["relatedOrderId"] is None

    def test_settlement_id_account_type_and_marketplace_name(self):
        from shared.finances_client import _flatten_transaction

        txn = {
            "transactionId": "txn-6",
            "totalAmount": {"currencyAmount": 1.0},
            "marketplaceDetails": {"marketplaceId": "ATVPDKIKX0DER", "marketplaceName": "amazon.com"},
            "sellingPartnerMetadata": {"accountType": "Standard Orders"},
            "relatedIdentifiers": [
                {"relatedIdentifierName": "ORDER_ID", "relatedIdentifierValue": "111-222"},
                {"relatedIdentifierName": "SETTLEMENT_ID", "relatedIdentifierValue": "26830040841"},
            ],
        }

        rows = _flatten_transaction(txn, "US")

        assert rows[0]["marketplaceName"] == "amazon.com"
        assert rows[0]["accountType"] == "Standard Orders"
        assert rows[0]["settlementId"] == "26830040841"

    def test_release_date_from_deferred_context(self):
        from shared.finances_client import _flatten_transaction

        txn = {
            "transactionId": "txn-7",
            "totalAmount": {"currencyAmount": 1.0},
            "contexts": [
                {"contextType": "DeferredContext", "deferralReason": "Reserve", "maturityDate": "2026-07-09"},
            ],
        }

        rows = _flatten_transaction(txn, "US")

        assert rows[0]["releaseDate"] == "2026-07-09"

    def test_release_date_none_without_deferred_context(self):
        from shared.finances_client import _flatten_transaction

        rows = _flatten_transaction({"transactionId": "txn-8", "totalAmount": {}}, "US")
        assert rows[0]["releaseDate"] is None

    def test_sku_quantity_fulfillment_from_item_product_context(self):
        from shared.finances_client import _flatten_transaction

        txn = {
            "transactionId": "txn-9",
            "totalAmount": {"currencyAmount": 49.90},
            "items": [
                {
                    "contexts": [
                        {"contextType": "ProductContext", "asin": "B0X", "sku": "BW01004EL", "quantityShipped": 1, "fulfillmentNetwork": "AFN"},
                    ],
                    "breakdowns": [
                        {"breakdownType": "Principal", "breakdownAmount": {"currencyCode": "USD", "currencyAmount": 49.90}},
                    ],
                },
            ],
        }

        rows = _flatten_transaction(txn, "US")

        assert rows[0]["sku"] == "BW01004EL"
        assert rows[0]["quantityShipped"] == 1
        assert rows[0]["fulfillmentNetwork"] == "AFN"

    def test_posted_date_is_converted_to_marketplace_local_time(self):
        # 2026-08-01T06:55:00Z reads as "August 1" in UTC but is still
        # "July 31, 11:55 PM PDT" in the US marketplace's own calendar day --
        # exactly the boundary that confused a "pulled July 1-31" export
        # showing an Aug-1-looking timestamp.
        from shared.finances_client import _flatten_transaction

        txn = {"transactionId": "txn-11", "postedDate": "2026-08-01T06:55:00Z", "totalAmount": {}}

        rows = _flatten_transaction(txn, "US")

        assert rows[0]["postedDate"] == "2026-07-31T23:55:00-07:00"

    def test_posted_date_none_passes_through(self):
        from shared.finances_client import _flatten_transaction

        rows = _flatten_transaction({"transactionId": "txn-12", "totalAmount": {}}, "US")

        assert rows[0]["postedDate"] is None

    def test_sku_is_none_for_transaction_level_breakdowns(self):
        # A transaction-level breakdown isn't attributable to a single item,
        # so sku/quantity/fulfillment must not be guessed.
        from shared.finances_client import _flatten_transaction

        txn = {
            "transactionId": "txn-10",
            "totalAmount": {"currencyAmount": 1.0},
            "breakdowns": [
                {"breakdownType": "Principal", "breakdownAmount": {"currencyCode": "USD", "currencyAmount": 1.0}},
            ],
            "items": [
                {"contexts": [{"contextType": "ProductContext", "sku": "SHOULD-NOT-APPEAR"}]},
            ],
        }

        rows = _flatten_transaction(txn, "US")

        assert rows[0]["sku"] is None


class TestDedupeTransactions:
    def _txn(self, txn_id, order_id, status, amount=45.0):
        return {
            "transactionId": txn_id,
            "transactionType": "Shipment",
            "transactionStatus": status,
            "totalAmount": {"currencyAmount": amount},
            "relatedIdentifiers": [
                {"relatedIdentifierName": "ORDER_ID", "relatedIdentifierValue": order_id},
            ],
            "breakdowns": [
                {"breakdownType": "Principal", "breakdownAmount": {"currencyAmount": amount}},
            ],
        }

    def test_released_and_deferred_released_with_same_amounts_collapse_to_released(self):
        from shared.finances_client import _dedupe_transactions

        txns = [
            self._txn("t-deferred", "111-222", "DEFERRED_RELEASED"),
            self._txn("t-released", "111-222", "RELEASED"),
        ]

        result = _dedupe_transactions(txns)

        assert len(result) == 1
        assert result[0]["transactionId"] == "t-released"

    def test_deferred_released_without_matching_released_is_kept(self):
        from shared.finances_client import _dedupe_transactions

        txns = [self._txn("t-deferred", "111-222", "DEFERRED_RELEASED")]

        result = _dedupe_transactions(txns)

        assert len(result) == 1
        assert result[0]["transactionId"] == "t-deferred"

    def test_two_released_with_identical_amounts_are_not_merged(self):
        # Same order, same amount, same status -- plausibly two separate
        # shipments of equal value, not a lifecycle duplicate.
        from shared.finances_client import _dedupe_transactions

        txns = [
            self._txn("t-1", "111-222", "RELEASED"),
            self._txn("t-2", "111-222", "RELEASED"),
        ]

        result = _dedupe_transactions(txns)

        assert len(result) == 2

    def test_transactions_without_order_id_are_untouched(self):
        from shared.finances_client import _dedupe_transactions

        txns = [
            {"transactionId": "t-1", "transactionStatus": "RELEASED", "totalAmount": {"currencyAmount": 1.0}},
            {"transactionId": "t-2", "transactionStatus": "RELEASED", "totalAmount": {"currencyAmount": 1.0}},
        ]

        result = _dedupe_transactions(txns)

        assert len(result) == 2

    def test_different_amounts_same_order_are_not_merged(self):
        from shared.finances_client import _dedupe_transactions

        txns = [
            self._txn("t-1", "111-222", "DEFERRED_RELEASED", amount=45.0),
            self._txn("t-2", "111-222", "RELEASED", amount=99.0),
        ]

        result = _dedupe_transactions(txns)

        assert len(result) == 2


class TestFetchTransactions:
    def _page(self, transactions, next_token=None):
        payload = {"transactions": transactions}
        if next_token:
            payload["nextToken"] = next_token
        return {"payload": payload}

    def test_single_window_single_page(self):
        from shared import finances_client

        page = self._page([{"transactionId": "t1", "totalAmount": {"currencyAmount": 1.0}}])
        with (
            patch.object(finances_client, "sp_api_request", return_value=page) as req,
            patch.object(finances_client.time, "sleep") as sleep,
        ):
            rows = finances_client.fetch_transactions(
                "moxe", "US", date(2026, 8, 1), date(2026, 8, 31),
            )

        assert [r["transactionId"] for r in rows] == ["t1"]
        req.assert_called_once()
        sleep.assert_not_called()  # first (and only) request is never paced

    def test_follows_next_token_across_pages(self):
        from shared import finances_client

        page1 = self._page([{"transactionId": "t1", "totalAmount": {}}], next_token="tok-1")
        page2 = self._page([{"transactionId": "t2", "totalAmount": {}}])
        with (
            patch.object(finances_client, "sp_api_request", side_effect=[page1, page2]) as req,
            patch.object(finances_client.time, "sleep") as sleep,
        ):
            rows = finances_client.fetch_transactions(
                "moxe", "US", date(2026, 8, 1), date(2026, 8, 31),
            )

        assert [r["transactionId"] for r in rows] == ["t1", "t2"]
        assert req.call_count == 2
        # First request unpaced, second (continuation page) paced.
        sleep.assert_called_once()
        assert req.call_args_list[1].kwargs["params"] == {"nextToken": "tok-1"}

    def test_splits_wide_range_into_multiple_requests(self):
        from shared import finances_client

        empty = self._page([])
        with (
            patch.object(finances_client, "sp_api_request", return_value=empty) as req,
            patch.object(finances_client.time, "sleep"),
        ):
            finances_client.fetch_transactions(
                "moxe", "US", date(2026, 1, 1), date(2026, 8, 1),
            )

        assert req.call_count == 2
        first_params = req.call_args_list[0].kwargs["params"]
        second_params = req.call_args_list[1].kwargs["params"]
        # US -> America/Los_Angeles local-day boundaries, not raw UTC.
        assert first_params["postedAfter"] == "2026-01-01T08:00:00Z"
        assert first_params["postedBefore"] == "2026-06-29T06:59:59Z"
        assert second_params["postedAfter"] == "2026-06-29T07:00:00Z"

    def test_marketplace_id_and_transaction_status_included(self):
        from shared import finances_client

        empty = self._page([])
        with (
            patch.object(finances_client, "sp_api_request", return_value=empty) as req,
            patch.object(finances_client.time, "sleep"),
        ):
            finances_client.fetch_transactions(
                "moxe", "US", date(2026, 8, 1), date(2026, 8, 2),
                transaction_status="RELEASED",
            )

        params = req.call_args.kwargs["params"]
        assert params["marketplaceId"] == "ATVPDKIKX0DER"
        assert params["transactionStatus"] == "RELEASED"

    def test_transaction_status_omitted_when_not_given(self):
        from shared import finances_client

        empty = self._page([])
        with (
            patch.object(finances_client, "sp_api_request", return_value=empty) as req,
            patch.object(finances_client.time, "sleep"),
        ):
            finances_client.fetch_transactions("moxe", "US", date(2026, 8, 1), date(2026, 8, 2))

        assert "transactionStatus" not in req.call_args.kwargs["params"]

    def test_request_uses_get_and_correct_path(self):
        from shared import finances_client

        empty = self._page([])
        with (
            patch.object(finances_client, "sp_api_request", return_value=empty) as req,
            patch.object(finances_client.time, "sleep"),
        ):
            finances_client.fetch_transactions("moxe", "US", date(2026, 8, 1), date(2026, 8, 2))

        args = req.call_args
        assert args.args[2] == "GET"
        assert args.args[3] == "/finances/2024-06-19/transactions"
