"""Tests for shared.bq_schemas — the Active Listings registry entry."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))


class TestActiveListingsSchema:
    def test_registered_for_ingestion(self):
        from shared.bq_schemas import get_table_schema

        schema = get_table_schema("GET_MERCHANT_LISTINGS_ALL_DATA", "sp_api")
        assert schema is not None
        assert schema.table_name == "active_listings"
        assert schema.dedup_key == ("sku",)

    def test_columns_map_expected_tsv_headers(self):
        from shared.bq_schemas import get_table_schema

        schema = get_table_schema("GET_MERCHANT_LISTINGS_ALL_DATA", "sp_api")
        by_name = {c.bq_name: c for c in schema.columns}
        assert by_name["sku"].tsv_header == "seller-sku"
        assert by_name["asin"].tsv_header == "asin1"
        assert by_name["status"].tsv_header == "status"
        assert by_name["price"].bq_type == "FLOAT"
        assert by_name["quantity"].bq_type == "INTEGER"
