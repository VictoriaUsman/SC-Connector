"""Tests for the BigQuery ingestion function — append-only load + source dedup.

Regression coverage for two issues:

1. Orders Total Sales freeze: Amazon's by-last-update All Orders flat file
   re-emits the same ``(amazon_order_id, sku)`` line, so a pull can hold
   duplicate dedup keys. The batch is collapsed to one row per key (latest
   update wins) before loading.

2. Alert storm from BQ concurrent-update: per-report MERGE/DELETE DML serialized
   under high event fan-out, raising "Could not serialize access ... due to
   concurrent update". Ingestion is now append-only (load jobs take no DML
   lock); cross-pull dedup happens at read time via the ``*_latest`` views.
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


class TestLoadAppend:
    def test_append_load_uses_write_append_and_dedupes_orders(self):
        """Orders are collapsed to one row per key, then appended (WRITE_APPEND).

        No MERGE/DELETE DML — append load jobs take no table lock, which is what
        eliminates the "concurrent update" alert storm under event fan-out.
        """
        from google.cloud import bigquery

        from ingest_bigquery.main import _load_append

        rows = [
            _order_row("111", "SKU-A", 10.0, "2026-06-09T10:00:00+00:00"),
            _order_row("111", "SKU-A", 12.0, "2026-06-09T11:00:00+00:00"),
            _order_row("222", "SKU-B", 5.0, "2026-06-09T10:00:00+00:00"),
        ]

        fake_client = MagicMock()

        with patch("ingest_bigquery.main._get_bq_client", return_value=fake_client):
            _load_append("proj.ds.orders", _ORDERS_SCHEMA, rows)

        call = fake_client.load_table_from_json.call_args
        loaded_rows = call[0][0]
        keys = [(r["amazon_order_id"], r["sku"]) for r in loaded_rows]
        assert len(keys) == len(set(keys))
        assert sorted(keys) == [("111", "SKU-A"), ("222", "SKU-B")]
        assert (
            call.kwargs["job_config"].write_disposition
            == bigquery.WriteDisposition.WRITE_APPEND
        )

    def test_ads_append_load_keeps_all_rows(self):
        """Ads schemas have no dedup key — every row is appended verbatim."""
        from ingest_bigquery.main import _load_append

        rows = [
            {"client_id": "c1", "marketplace": "US", "campaign_id": "x", "cost": 1.0},
            {"client_id": "c1", "marketplace": "US", "campaign_id": "x", "cost": 2.0},
        ]
        fake_client = MagicMock()

        with patch("ingest_bigquery.main._get_bq_client", return_value=fake_client):
            _load_append("proj.ds.sp_campaigns", _ADS_SCHEMA, rows)

        loaded_rows = fake_client.load_table_from_json.call_args[0][0]
        assert len(loaded_rows) == 2


class TestTransientRetry:
    def test_concurrent_update_is_retried_then_succeeds(self):
        from ingest_bigquery import main as ingest

        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError(
                    "400 Could not serialize access to table orders due to "
                    "concurrent update"
                )
            return "ok"

        with patch.object(ingest.time, "sleep"):
            result = ingest._run_with_retry(flaky, attempts=5, base_delay=0.0)

        assert result == "ok"
        assert calls["n"] == 3

    def test_non_transient_error_is_not_retried(self):
        from ingest_bigquery import main as ingest

        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise ValueError("schema mismatch")

        with patch.object(ingest.time, "sleep"):
            try:
                ingest._run_with_retry(boom, attempts=5, base_delay=0.0)
                raised = False
            except ValueError:
                raised = True

        assert raised
        assert calls["n"] == 1
