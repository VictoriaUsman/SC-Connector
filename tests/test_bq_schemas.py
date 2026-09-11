"""Tests for shared.bq_schemas — the Active Listings registry entry."""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

# infra/resources/bigquery.py imports pulumi at module scope, which is not
# installed in the test environment (see tests/test_functions_infra.py for
# the same constraint) — parse TABLE_DEFS out of the source with ast instead
# of importing the module.
_BIGQUERY_PY = (
    Path(__file__).resolve().parent.parent / "infra" / "resources" / "bigquery.py"
)


def _load_table_defs() -> list[dict]:
    """Extract TABLE_DEFS's literal-only keys, without importing pulumi.

    Some values (e.g. ``"columns": _ORDERS_COLUMNS``) are module-level name
    references rather than literals, so a plain ``ast.literal_eval`` on the
    whole list fails — evaluate each dict key independently and skip the
    ones that aren't literals.
    """
    tree = ast.parse(_BIGQUERY_PY.read_text())
    list_node = None
    for node in tree.body:
        # TABLE_DEFS carries a "list[dict]" annotation, so it parses as an
        # AnnAssign rather than a plain Assign.
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "TABLE_DEFS" and node.value is not None:
                list_node = node.value
                break
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "TABLE_DEFS":
                    list_node = node.value
                    break
    if list_node is None:
        raise AssertionError("TABLE_DEFS not found in infra/resources/bigquery.py")

    table_defs = []
    for dict_node in list_node.elts:
        entry = {}
        for key_node, value_node in zip(dict_node.keys, dict_node.values):
            key = ast.literal_eval(key_node)
            try:
                entry[key] = ast.literal_eval(value_node)
            except ValueError:
                pass  # non-literal value (e.g. a _*_COLUMNS name reference)
        table_defs.append(entry)
    return table_defs


class TestActiveListingsLatestView:
    def test_latest_order_by_is_a_real_column(self):
        """active_listings has no per-row update timestamp (unlike orders'
        last_updated_date, the _latest_view_query default), so its
        TABLE_DEFS entry must set latest_order_by explicitly — otherwise the
        generated view SQL orders by a column that doesn't exist on this
        table at all.
        """
        table_defs = {t["name"]: t for t in _load_table_defs()}
        active_listings = table_defs["active_listings"]

        assert active_listings.get("latest_order_by") == "report_date"

        # report_date is a real column (meta); last_updated_date (the
        # _latest_view_query default) is not a column on this table.
        real_columns = {"sku", "asin", "item_name", "status", "fulfillment_channel",
                         "price", "quantity", "client_id", "marketplace",
                         "report_date", "ingested_at", "job_id"}
        assert active_listings["latest_order_by"] in real_columns
        assert "last_updated_date" not in real_columns


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
