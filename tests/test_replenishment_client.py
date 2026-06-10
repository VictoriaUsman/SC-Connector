"""Tests for shared.replenishment_client — window looping + request shapes."""

from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")


class TestIterWindows:
    def test_weekly_windows_align_to_amazon_sunday_saturday_weeks(self):
        from shared.replenishment_client import _iter_windows

        wins = list(_iter_windows(date(2026, 1, 1), date(2026, 1, 20), "WEEK"))
        assert wins == [
            (date(2025, 12, 28), date(2026, 1, 3)),
            (date(2026, 1, 4), date(2026, 1, 10)),
            (date(2026, 1, 11), date(2026, 1, 17)),
            (date(2026, 1, 18), date(2026, 1, 24)),
        ]

    def test_daily_windows(self):
        from shared.replenishment_client import _iter_windows

        wins = list(_iter_windows(date(2026, 1, 1), date(2026, 1, 3), "DAY"))
        assert wins == [
            (date(2026, 1, 1), date(2026, 1, 1)),
            (date(2026, 1, 2), date(2026, 1, 2)),
            (date(2026, 1, 3), date(2026, 1, 3)),
        ]

    def test_monthly_windows_align_to_calendar_months(self):
        from shared.replenishment_client import _iter_windows

        wins = list(_iter_windows(date(2026, 1, 15), date(2026, 3, 10), "MONTH"))
        assert wins == [
            (date(2026, 1, 1), date(2026, 1, 31)),
            (date(2026, 2, 1), date(2026, 2, 28)),
            (date(2026, 3, 1), date(2026, 3, 31)),
        ]

    def test_empty_when_end_before_start(self):
        from shared.replenishment_client import _iter_windows

        assert list(_iter_windows(date(2026, 1, 10), date(2026, 1, 1), "WEEK")) == []


class TestFetchOfferMetrics:
    def test_one_request_per_window_and_rows_annotated(self):
        from shared import replenishment_client

        # Two Amazon weekly windows -> two POSTs; each returns one offer.
        pages = [
            {"offers": [{"asin": "A1"}]},
            {"offers": [{"asin": "A2"}]},
        ]
        with patch.object(replenishment_client, "_paginate_offset", side_effect=[[pages[0]], [pages[1]]]) as pg:
            rows = replenishment_client.fetch_offer_metrics(
                "moxe", "US", date(2026, 1, 4), date(2026, 1, 17), aggregation="WEEK",
            )

        assert pg.call_count == 2
        assert [r["asin"] for r in rows] == ["A1", "A2"]
        # Window stamps applied.
        assert rows[0]["window_start"] == "2026-01-04"
        assert rows[1]["window_start"] == "2026-01-11"

    def test_forecast_uses_forecast_metrics_and_time_period(self):
        from shared import replenishment_client
        from shared.replenishment_client import FORECAST_METRICS

        captured = {}

        def _fake_paginate(client_id, marketplace, path, *, body=None, **kwargs):
            captured["body"] = body
            return [{"offers": []}]

        with patch.object(replenishment_client, "_paginate_offset", side_effect=_fake_paginate):
            replenishment_client.fetch_offer_metrics(
                "moxe", "US", date(2026, 1, 4), date(2026, 1, 10),
                aggregation="WEEK", time_period_type="FORECAST",
            )

        assert captured["body"]["filters"]["timePeriodType"] == "FORECAST"
        assert captured["body"]["metrics"] == FORECAST_METRICS

    def test_request_shape_matches_live_replenishment_contract(self):
        from shared import replenishment_client

        captured: list[dict] = []

        def _fake_paginate(client_id, marketplace, path, *, body=None, **kwargs):
            captured.append({"path": path, "body": body})
            return [{"offers": []}]

        with patch.object(replenishment_client, "_paginate_offset", side_effect=_fake_paginate):
            replenishment_client.fetch_offer_metrics(
                "moxe", "US", date(2026, 5, 25), date(2026, 5, 31), aggregation="WEEK",
            )

        assert len(captured) == 2
        assert captured[0]["path"].endswith("/offers/metrics/search")
        assert captured[0]["body"]["filters"]["timeInterval"] == {
            "startDate": "2026-05-24T00:00:00Z",
            "endDate": "2026-05-30T23:59:59Z",
        }
        assert captured[1]["body"]["filters"]["timeInterval"] == {
            "startDate": "2026-05-31T00:00:00Z",
            "endDate": "2026-06-06T23:59:59Z",
        }
        assert captured[0]["body"]["filters"]["marketplaceId"] == "ATVPDKIKX0DER"
        assert captured[0]["body"]["filters"]["programTypes"] == ["SUBSCRIBE_AND_SAVE"]


class TestListOffers:
    def test_aggregates_paginated_offers(self):
        from shared import replenishment_client

        pages = [{"offers": [{"asin": "A1"}]}, {"offers": [{"asin": "A2"}]}]
        with patch.object(replenishment_client, "_paginate_offset", return_value=iter(pages)) as pg:
            rows = replenishment_client.list_offers("moxe", "US")

        assert [r["asin"] for r in rows] == ["A1", "A2"]
        assert pg.call_args.args[2].endswith("/offers/search")
        assert pg.call_args.kwargs["body"]["filters"]["marketplaceId"] == "ATVPDKIKX0DER"
