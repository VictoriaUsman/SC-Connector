"""Tests for report_converter — SP API and Ads API JSON→TSV conversion."""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

from shared.report_converter import maybe_convert_to_tsv


class TestMaybeConvertToTsv:
    """Unified entry-point tests."""

    def test_sp_api_known_json_report_converts(self):
        data = {"salesAndTrafficByDate": [{"date": "2026-03-20", "units": 5}]}
        content, converted = maybe_convert_to_tsv(
            json.dumps(data).encode(), "sp_api", "GET_SALES_AND_TRAFFIC_REPORT",
        )
        assert converted is True
        assert b"date\tunits" in content

    def test_sp_api_flat_file_not_converted(self):
        raw = b"col1\tcol2\nval1\tval2"
        content, converted = maybe_convert_to_tsv(raw, "sp_api", "GET_FLAT_FILE_OPEN_LISTINGS_DATA")
        assert converted is False
        assert content is raw

    def test_ads_api_always_converts(self):
        data = [{"campaign": "A", "cost": 1.5}]
        content, converted = maybe_convert_to_tsv(
            json.dumps(data).encode(), "ads_api", "spCampaigns",
        )
        assert converted is True
        assert b"campaign\tcost" in content

    def test_ads_api_invalid_json_not_converted(self):
        raw = b"not json"
        content, converted = maybe_convert_to_tsv(raw, "ads_api", "spCampaigns")
        assert converted is False
        assert content is raw


class TestAdsApiConversion:
    def test_flat_array(self):
        data = [
            {"campaignName": "Camp A", "cost": 4.66, "clicks": 23},
            {"campaignName": "Camp B", "cost": 1.50, "clicks": 10},
        ]
        content, converted = maybe_convert_to_tsv(json.dumps(data).encode(), "ads_api", "spCampaigns")
        assert converted
        lines = content.decode().strip().split("\n")
        assert lines[0] == "campaignName\tcost\tclicks"
        assert lines[1] == "Camp A\t4.66\t23"
        assert lines[2] == "Camp B\t1.5\t10"

    def test_nested_fields_flattened(self):
        data = [{"campaign": "A", "metrics": {"cost": 1.0, "impressions": 100}}]
        content, _ = maybe_convert_to_tsv(json.dumps(data).encode(), "ads_api", "spCampaigns")
        header = content.decode().split("\n")[0]
        assert "metrics.cost" in header
        assert "metrics.impressions" in header

    def test_empty_array_returns_raw(self):
        raw = b"[]"
        content, converted = maybe_convert_to_tsv(raw, "ads_api", "spCampaigns")
        assert not converted
        assert content is raw

    def test_dict_wrapper_with_array(self):
        data = {"reports": [{"a": 1}, {"a": 2}]}
        content, converted = maybe_convert_to_tsv(json.dumps(data).encode(), "ads_api", "spCampaigns")
        assert converted
        lines = content.decode().strip().split("\n")
        assert lines[0] == "a"
        assert lines[1] == "1"

    def test_superset_columns(self):
        data = [{"a": 1, "b": 2}, {"a": 3, "c": 4}]
        content, _ = maybe_convert_to_tsv(json.dumps(data).encode(), "ads_api", "spCampaigns")
        lines = content.decode().strip().split("\n")
        header = lines[0].split("\t")
        assert set(header) == {"a", "b", "c"}
        assert lines[2].split("\t")[header.index("b")] == ""


class TestSpApiConversion:
    def test_multi_section_report(self):
        data = {
            "salesAndTrafficByDate": [
                {"date": "2026-03-20", "orderedProductSales": {"amount": 100, "currency": "USD"}},
            ],
            "salesAndTrafficByAsin": [
                {"asin": "B001", "orderedProductSales": {"amount": 50, "currency": "USD"}},
            ],
        }
        content, converted = maybe_convert_to_tsv(
            json.dumps(data).encode(), "sp_api", "GET_SALES_AND_TRAFFIC_REPORT",
        )
        assert converted
        text = content.decode()
        assert "orderedProductSales.amount" in text
        assert "salesAndTrafficByDate" in text
        assert "salesAndTrafficByAsin" in text

    def test_invalid_json_returns_raw(self):
        raw = b"broken {{"
        content, converted = maybe_convert_to_tsv(raw, "sp_api", "GET_SALES_AND_TRAFFIC_REPORT")
        assert not converted
        assert content is raw


class TestPercentageNormalization:
    """Verify percentage columns are divided by 100 when normalize_percentages=True."""

    def _sales_traffic_data(self):
        return {
            "salesAndTrafficByDate": [
                {
                    "date": "2026-03-20",
                    "salesByDate": {
                        "orderedProductSales": {"amount": 7317.8, "currencyCode": "USD"},
                        "refundRate": 2.5,
                    },
                    "trafficByDate": {
                        "sessions": 221,
                        "unitSessionPercentage": 1.9,
                        "orderItemSessionPercentage": 2.04,
                        "buyBoxPercentage": 87.5,
                    },
                },
            ],
        }

    def test_percentages_divided_by_100(self):
        data = self._sales_traffic_data()
        content, converted = maybe_convert_to_tsv(
            json.dumps(data).encode(),
            "sp_api",
            "GET_SALES_AND_TRAFFIC_REPORT",
            normalize_percentages=True,
        )
        assert converted
        lines = content.decode().strip().split("\n")
        header = lines[0].split("\t")
        values = lines[1].split("\t")
        row = dict(zip(header, values))

        assert float(row["trafficByDate.unitSessionPercentage"]) == pytest.approx(0.019)
        assert float(row["trafficByDate.orderItemSessionPercentage"]) == pytest.approx(0.0204)
        assert float(row["trafficByDate.buyBoxPercentage"]) == pytest.approx(0.875)
        assert float(row["salesByDate.refundRate"]) == pytest.approx(0.025)

    def test_non_percentage_columns_unchanged(self):
        data = self._sales_traffic_data()
        content, _ = maybe_convert_to_tsv(
            json.dumps(data).encode(),
            "sp_api",
            "GET_SALES_AND_TRAFFIC_REPORT",
            normalize_percentages=True,
        )
        lines = content.decode().strip().split("\n")
        header = lines[0].split("\t")
        values = lines[1].split("\t")
        row = dict(zip(header, values))

        assert row["trafficByDate.sessions"] == "221"
        assert float(row["salesByDate.orderedProductSales.amount"]) == pytest.approx(7317.8)

    def test_no_normalization_by_default(self):
        data = self._sales_traffic_data()
        content, _ = maybe_convert_to_tsv(
            json.dumps(data).encode(),
            "sp_api",
            "GET_SALES_AND_TRAFFIC_REPORT",
        )
        lines = content.decode().strip().split("\n")
        header = lines[0].split("\t")
        values = lines[1].split("\t")
        row = dict(zip(header, values))

        assert float(row["trafficByDate.unitSessionPercentage"]) == pytest.approx(1.9)


class TestColumnFiltering:
    """Verify output_columns parameter filters and orders columns."""

    def _sales_traffic_data(self):
        return {
            "salesAndTrafficByDate": [
                {
                    "date": "2026-03-20",
                    "salesByDate": {
                        "orderedProductSales": {"amount": 100, "currencyCode": "USD"},
                        "unitsOrdered": 5,
                    },
                    "trafficByDate": {"sessions": 221, "unitSessionPercentage": 1.9},
                },
            ],
            "salesAndTrafficByAsin": [
                {
                    "parentAsin": "B001",
                    "salesByAsin": {"orderedProductSales": {"amount": 50, "currencyCode": "USD"}},
                    "trafficByAsin": {"sessions": 100},
                },
            ],
        }

    def test_filter_to_selected_columns(self):
        data = self._sales_traffic_data()
        content, converted = maybe_convert_to_tsv(
            json.dumps(data).encode(),
            "sp_api",
            "GET_SALES_AND_TRAFFIC_REPORT",
            output_columns=[
                "date",
                "salesByDate.orderedProductSales.amount",
                "salesByDate.unitsOrdered",
            ],
        )
        assert converted
        lines = content.decode().strip().split("\n")
        header = lines[0].split("\t")
        assert header == ["date", "salesByDate.orderedProductSales.amount", "salesByDate.unitsOrdered"]
        assert "currencyCode" not in content.decode()

    def test_filter_preserves_specified_order(self):
        data = self._sales_traffic_data()
        content, _ = maybe_convert_to_tsv(
            json.dumps(data).encode(),
            "sp_api",
            "GET_SALES_AND_TRAFFIC_REPORT",
            output_columns=[
                "salesByDate.unitsOrdered",
                "date",
            ],
        )
        lines = content.decode().strip().split("\n")
        header = lines[0].split("\t")
        assert header == ["salesByDate.unitsOrdered", "date"]

    def test_filter_skips_section_with_no_matching_columns(self):
        data = self._sales_traffic_data()
        content, _ = maybe_convert_to_tsv(
            json.dumps(data).encode(),
            "sp_api",
            "GET_SALES_AND_TRAFFIC_REPORT",
            output_columns=["date", "salesByDate.orderedProductSales.amount"],
        )
        text = content.decode()
        assert "salesAndTrafficByAsin" not in text
        assert "parentAsin" not in text
        assert "salesAndTrafficByDate" not in text

    def test_filter_silently_ignores_unknown_columns(self):
        data = self._sales_traffic_data()
        content, _ = maybe_convert_to_tsv(
            json.dumps(data).encode(),
            "sp_api",
            "GET_SALES_AND_TRAFFIC_REPORT",
            output_columns=["date", "nonexistent.column"],
        )
        lines = content.decode().strip().split("\n")
        header = lines[0].split("\t")
        assert header == ["date"]

    def test_no_filter_returns_all_columns(self):
        data = self._sales_traffic_data()
        content, _ = maybe_convert_to_tsv(
            json.dumps(data).encode(),
            "sp_api",
            "GET_SALES_AND_TRAFFIC_REPORT",
        )
        text = content.decode()
        assert "currencyCode" in text
        assert "salesAndTrafficByDate" in text
        assert "salesAndTrafficByAsin" in text

    def test_filter_combined_with_percentage_normalization(self):
        data = self._sales_traffic_data()
        content, _ = maybe_convert_to_tsv(
            json.dumps(data).encode(),
            "sp_api",
            "GET_SALES_AND_TRAFFIC_REPORT",
            output_columns=["date", "trafficByDate.unitSessionPercentage"],
            normalize_percentages=True,
        )
        lines = content.decode().strip().split("\n")
        header = lines[0].split("\t")
        values = lines[1].split("\t")
        assert header == ["date", "trafficByDate.unitSessionPercentage"]
        assert float(values[1]) == pytest.approx(0.019)

    def test_ads_filter(self):
        data = [
            {"campaign": "A", "cost": 1.5, "clicks": 23},
            {"campaign": "B", "cost": 2.0, "clicks": 10},
        ]
        content, converted = maybe_convert_to_tsv(
            json.dumps(data).encode(),
            "ads_api",
            "spCampaigns",
            output_columns=["campaign", "cost"],
        )
        assert converted
        lines = content.decode().strip().split("\n")
        assert lines[0] == "campaign\tcost"
        assert lines[1] == "A\t1.5"
