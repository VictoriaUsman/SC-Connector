"""Tests for Vendor (1P) account support — report registry, option injection,
JSON conversion, Drive format heuristic, and api_source inference."""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")

from shared.drive_client import infer_report_format
from shared.report_converter import maybe_convert_to_tsv
from shared.vendor_reports import (
    VENDOR_INVENTORY_REPORT,
    VENDOR_REPORT_ARRAY_KEYS,
    VENDOR_SALES_REPORT,
    VENDOR_SP_REPORT_TYPES,
    VENDOR_TRAFFIC_REPORT,
    is_vendor_report_type,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestVendorRegistry:
    def test_three_domains_present(self):
        assert VENDOR_SP_REPORT_TYPES == {
            VENDOR_SALES_REPORT,
            VENDOR_TRAFFIC_REPORT,
            VENDOR_INVENTORY_REPORT,
        }

    def test_is_vendor_report_type(self):
        assert is_vendor_report_type(VENDOR_SALES_REPORT)
        assert not is_vendor_report_type("GET_FLAT_FILE_OPEN_LISTINGS_DATA")
        assert not is_vendor_report_type("spCampaigns")

    def test_every_report_has_array_keys(self):
        for rt in VENDOR_SP_REPORT_TYPES:
            assert VENDOR_REPORT_ARRAY_KEYS.get(rt), rt


# ---------------------------------------------------------------------------
# JSON → TSV conversion
# ---------------------------------------------------------------------------

class TestVendorConversion:
    def test_vendor_sales_report_converts_multi_section(self):
        data = {
            "reportSpecification": {"reportType": VENDOR_SALES_REPORT},
            "salesAggregate": [{"orderedUnits": 10, "shippedUnits": 8}],
            "salesByAsin": [{"asin": "B001", "orderedUnits": 4, "shippedUnits": 3}],
        }
        content, converted = maybe_convert_to_tsv(
            json.dumps(data).encode(), "sp_api", VENDOR_SALES_REPORT,
        )
        assert converted is True
        text = content.decode()
        # Both sections are flattened, with section headers for multi-section output.
        assert "# salesAggregate" in text
        assert "# salesByAsin" in text
        assert "asin" in text
        assert "B001" in text

    def test_vendor_traffic_report_converts(self):
        data = {
            "reportSpecification": {"reportType": VENDOR_TRAFFIC_REPORT},
            "trafficByAsin": [{"asin": "B009", "glanceViews": 123}],
        }
        content, converted = maybe_convert_to_tsv(
            json.dumps(data).encode(), "sp_api", VENDOR_TRAFFIC_REPORT,
        )
        assert converted is True
        assert b"glanceViews" in content
        assert b"123" in content

    def test_vendor_inventory_report_converts(self):
        data = {
            "inventoryAggregate": [{"sellableOnHandInventoryUnits": 50}],
            "inventoryByAsin": [{"asin": "B777", "sellableOnHandInventoryUnits": 12}],
        }
        content, converted = maybe_convert_to_tsv(
            json.dumps(data).encode(), "sp_api", VENDOR_INVENTORY_REPORT,
        )
        assert converted is True
        text = content.decode()
        assert "sellableOnHandInventoryUnits" in text
        assert "B777" in text


# ---------------------------------------------------------------------------
# Drive format heuristic
# ---------------------------------------------------------------------------

class TestVendorDriveFormat:
    def test_vendor_reports_inferred_as_json(self):
        for rt in VENDOR_SP_REPORT_TYPES:
            ext, mime = infer_report_format("sp_api", rt)
            assert ext == ".json"
            assert mime == "application/json"


# ---------------------------------------------------------------------------
# create_report reportOptions injection
# ---------------------------------------------------------------------------

class TestVendorReportOptions:
    def test_injects_defaults_for_sales(self):
        from create_report.main import _ensure_sp_report_options

        params = _ensure_sp_report_options(
            VENDOR_SALES_REPORT,
            {"dataStartTime": "2026-03-20", "dataEndTime": "2026-03-20"},
        )
        opts = params["reportOptions"]
        assert opts["distributorView"] == "SOURCING"
        assert opts["sellingProgram"] == "RETAIL"
        # Single-day range infers DAY period.
        assert opts["reportPeriod"] == "DAY"

    def test_injects_defaults_for_inventory(self):
        from create_report.main import _ensure_sp_report_options

        params = _ensure_sp_report_options(VENDOR_INVENTORY_REPORT, {})
        opts = params["reportOptions"]
        assert opts["distributorView"] == "SOURCING"
        assert opts["sellingProgram"] == "RETAIL"
        assert opts["reportPeriod"] in {"DAY", "WEEK", "MONTH", "QUARTER", "YEAR"}

    def test_traffic_has_no_distributor_view(self):
        from create_report.main import _ensure_sp_report_options

        params = _ensure_sp_report_options(VENDOR_TRAFFIC_REPORT, {})
        opts = params["reportOptions"]
        assert "distributorView" not in opts
        assert "reportPeriod" in opts

    def test_caller_options_are_preserved(self):
        from create_report.main import _ensure_sp_report_options

        params = _ensure_sp_report_options(
            VENDOR_SALES_REPORT,
            {"reportOptions": {"distributorView": "MANUFACTURING", "reportPeriod": "WEEK"}},
        )
        opts = params["reportOptions"]
        assert opts["distributorView"] == "MANUFACTURING"
        assert opts["reportPeriod"] == "WEEK"
        # Missing default still filled in.
        assert opts["sellingProgram"] == "RETAIL"


# ---------------------------------------------------------------------------
# api_source inference — vendor reports are SP API
# ---------------------------------------------------------------------------

class TestVendorApiSource:
    def test_vendor_report_is_sp_api_under_both(self):
        from shared.workflow_launcher import infer_api_source

        assert infer_api_source(VENDOR_SALES_REPORT, "both") == "sp_api"

    def test_validate_report_types_accepts_vendor_for_sp_api(self):
        from shared.workflow_launcher import validate_report_types

        assert validate_report_types("sp_api", list(VENDOR_SP_REPORT_TYPES)) is None

    def test_validate_report_types_rejects_vendor_for_ads(self):
        from shared.workflow_launcher import validate_report_types

        err = validate_report_types("ads_api", [VENDOR_SALES_REPORT])
        assert err is not None
