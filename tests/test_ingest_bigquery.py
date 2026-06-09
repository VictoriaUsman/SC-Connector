"""Tests for the BigQuery ingestion function — MERGE source dedup in particular.

Regression coverage for the orders MERGE failure that froze Total Sales in the
hourly Slack bot: Amazon's by-last-update All Orders flat file re-emits the same
``(amazon_order_id, sku)`` line, so the staging table held duplicate dedup keys
and BigQuery rejected the MERGE ("UPDATE/MERGE must match at most one source row
for each target row") on every run after the first.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

from shared.bq_schemas import get_table_schema  # noqa: E402

_ORDERS_SCHEMA = get_table_schema(
    "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL", "sp_api"
)
_ADS_SCHEMA = get_table_schema("spCampaigns", "ads_api")


def _order_row(
    order_id: str,
    sku: str,
    item_price: float,
    last_updated_date: str,
    *,
    client_id: str = "brook-whittle",
    marketplace: str = "US",
) -> dict:
    return {
        "client_id": client_id,
        "marketplace": marketplace,
        "report_date": "2026-06-09",
        "ingested_at": "2026-06-09T17:00:00+00:00",
        "amazon_order_id": order_id,
        "sku": sku,
        "item_price": item_price,
        "last_updated_date": last_updated_date,
        "order_status": "Shipped",
        "quantity": 1,
    }


class TestDedupeMergeRows:
    def test_collapses_duplicate_order_sku_to_single_row(self):
        from ingest_bigquery.main import _dedupe_merge_rows

        rows = [
            _order_row("111", "SKU-A", 10.0, "2026-06-09T10:00:00+00:00"),
            _order_row("111", "SKU-A", 12.0, "2026-06-09T11:00:00+00:00"),
        ]
        deduped = _dedupe_merge_rows(rows, _ORDERS_SCHEMA)

        assert len(deduped) == 1
        # The most recently updated row wins (matches WHEN MATCHED UPDATE).
        assert deduped[0]["item_price"] == 12.0

    def test_keeps_distinct_keys(self):
        from ingest_bigquery.main import _dedupe_merge_rows

        rows = [
            _order_row("111", "SKU-A", 10.0, "2026-06-09T10:00:00+00:00"),
            _order_row("111", "SKU-B", 20.0, "2026-06-09T10:00:00+00:00"),
            _order_row("222", "SKU-A", 30.0, "2026-06-09T10:00:00+00:00"),
        ]
        deduped = _dedupe_merge_rows(rows, _ORDERS_SCHEMA)

        keys = {(r["amazon_order_id"], r["sku"]) for r in deduped}
        assert keys == {("111", "SKU-A"), ("111", "SKU-B"), ("222", "SKU-A")}

    def test_same_key_different_marketplace_not_collapsed(self):
        from ingest_bigquery.main import _dedupe_merge_rows

        rows = [
            _order_row("111", "SKU-A", 10.0, "2026-06-09T10:00:00+00:00", marketplace="US"),
            _order_row("111", "SKU-A", 99.0, "2026-06-09T10:00:00+00:00", marketplace="CA"),
        ]
        deduped = _dedupe_merge_rows(rows, _ORDERS_SCHEMA)
        assert len(deduped) == 2

    def test_no_dedup_key_schema_returns_rows_unchanged(self):
        from ingest_bigquery.main import _dedupe_merge_rows

        # Ads schemas use the replace strategy (dedup_key=None); never deduped.
        rows = [{"client_id": "c1", "marketplace": "US", "campaign_id": "x"}] * 3
        assert _dedupe_merge_rows(rows, _ADS_SCHEMA) == rows

    def test_result_is_unique_per_merge_key(self):
        """Post-condition guarding the MERGE: no two source rows may share the
        full ON key, otherwise BigQuery raises the multi-match error."""
        from ingest_bigquery.main import _dedupe_merge_rows

        rows = [
            _order_row("111", "SKU-A", 10.0, "2026-06-09T10:00:00+00:00"),
            _order_row("111", "SKU-A", 12.0, "2026-06-09T11:00:00+00:00"),
            _order_row("111", "SKU-A", 11.0, "2026-06-09T09:00:00+00:00"),
            _order_row("222", "SKU-C", 5.0, "2026-06-09T10:00:00+00:00"),
        ]
        deduped = _dedupe_merge_rows(rows, _ORDERS_SCHEMA)

        keys = [
            (r["amazon_order_id"], r["sku"], r["client_id"], r["marketplace"])
            for r in deduped
        ]
        assert len(keys) == len(set(keys))


class TestLoadWithMergeDedup:
    def test_staging_load_receives_deduped_rows(self):
        """The duplicate-key batch that crashed the production MERGE must reach
        the staging table already deduplicated."""
        from ingest_bigquery.main import _load_with_merge

        rows = [
            _order_row("111", "SKU-A", 10.0, "2026-06-09T10:00:00+00:00"),
            _order_row("111", "SKU-A", 12.0, "2026-06-09T11:00:00+00:00"),
            _order_row("222", "SKU-B", 5.0, "2026-06-09T10:00:00+00:00"),
        ]

        fake_client = MagicMock()

        with patch("ingest_bigquery.main._get_bq_client", return_value=fake_client):
            _load_with_merge(
                "proj.ds.orders", _ORDERS_SCHEMA, rows,
                "brook-whittle", "US", "2026-06-09",
            )

        loaded_rows = fake_client.load_table_from_json.call_args[0][0]
        keys = [(r["amazon_order_id"], r["sku"]) for r in loaded_rows]
        assert len(keys) == len(set(keys))
        assert sorted(keys) == [("111", "SKU-A"), ("222", "SKU-B")]
