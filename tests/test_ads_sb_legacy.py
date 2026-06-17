"""Tests for the Sponsored Brands legacy (v2) reporting fallback.

Covers the v2 -> v3 metric/date mapping and the v2+v3 merge/aggregation that
re-includes legacy (non-multi-ad-group) SB campaigns omitted by v3 reporting.
Reproduces the Glove Station discrepancy from CU-868jy1cgf / CU-868jybu2b:
v3 export under-counted ($1,383.89) vs the Ads console ($3,424.62).
"""

from __future__ import annotations

import gzip
import json
import os
import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

import requests
from ad_api.base.exceptions import (
    AdvertisingApiForbiddenException,
    AdvertisingApiTemporarilyUnavailableException,
    AdvertisingApiTooManyRequestsException,
)

from shared import ads_sb_legacy
from shared.ads_sb_legacy import (
    _call_with_retry,
    _is_retryable,
    augment_sb_campaigns_content,
    date_range,
    map_v2_campaign_row,
    merge_sb_rows,
    select_legacy_campaign_ids,
    to_yyyymmdd,
)
from shared.report_converter import maybe_convert_to_tsv


# ----------------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------------

class TestDateHelpers:
    def test_date_range_inclusive(self):
        rng = date_range(date(2026, 5, 21), date(2026, 5, 23))
        assert rng == [date(2026, 5, 21), date(2026, 5, 22), date(2026, 5, 23)]

    def test_date_range_single_day(self):
        assert date_range(date(2026, 5, 21), date(2026, 5, 21)) == [date(2026, 5, 21)]

    def test_date_range_reversed_is_empty(self):
        assert date_range(date(2026, 5, 23), date(2026, 5, 21)) == []

    def test_to_yyyymmdd(self):
        assert to_yyyymmdd(date(2026, 5, 21)) == "20260521"


class TestSelectLegacyCampaignIds:
    def test_keeps_only_non_multi_ad_group(self):
        campaigns = [
            {"campaignId": 111, "name": "Legacy A", "state": "enabled", "isMultiAdGroupsEnabled": False},
            {"campaignId": 222, "name": "V4 B", "state": "enabled", "isMultiAdGroupsEnabled": True},
            {"campaignId": 333, "name": "Legacy C", "state": "paused"},  # missing flag -> legacy
        ]
        legacy = select_legacy_campaign_ids(campaigns)
        assert set(legacy) == {"111", "333"}
        assert legacy["111"]["campaignName"] == "Legacy A"
        assert legacy["111"]["campaignStatus"] == "ENABLED"
        assert legacy["333"]["campaignStatus"] == "PAUSED"

    def test_extracts_nested_budget(self):
        campaigns = [
            {"campaignId": 1, "name": "A", "isMultiAdGroupsEnabled": False, "budget": {"budget": 500.0}},
            {"campaignId": 2, "name": "B", "isMultiAdGroupsEnabled": False, "budget": 250},
        ]
        legacy = select_legacy_campaign_ids(campaigns)
        assert legacy["1"]["campaignBudgetAmount"] == 500.0
        assert legacy["2"]["campaignBudgetAmount"] == 250

    def test_ignores_malformed_entries(self):
        campaigns = ["not-a-dict", {"name": "no id", "isMultiAdGroupsEnabled": False}]
        assert select_legacy_campaign_ids(campaigns) == {}


class TestMapV2CampaignRow:
    def test_metric_and_date_mapping(self):
        v2_row = {
            "campaignId": 111,
            "impressions": 1000,
            "clicks": 25,
            "cost": 510.36,
            "attributedSales14d": 1200.0,
            "attributedConversions14d": 8,
            "attributedUnitsOrdered14d": 10,
            "attributedDetailPageViewsClicks14d": 40,
            "attributedOrdersNewToBrand14d": 3,
            "attributedSalesNewToBrand14d": 600.0,
        }
        meta = {
            "campaignId": "111",
            "campaignName": "Legacy A",
            "campaignStatus": "ENABLED",
            "campaignBudgetAmount": 500.0,
        }
        row = map_v2_campaign_row(v2_row, report_date=date(2026, 5, 21), metadata=meta)

        assert row["date"] == "2026-05-21"
        assert row["campaignId"] == "111"
        assert row["campaignName"] == "Legacy A"
        assert row["campaignStatus"] == "ENABLED"
        assert row["campaignBudgetAmount"] == 500.0
        # v2 metric names mapped onto v3 column names
        assert row["cost"] == 510.36
        assert row["sales"] == 1200.0
        assert row["purchases"] == 8
        assert row["unitsSoldClicks"] == 10
        assert row["detailPageViewsClicks"] == 40
        assert row["newToBrandPurchases"] == 3
        assert row["newToBrandSales"] == 600.0

    def test_core_metrics_default_to_zero(self):
        row = map_v2_campaign_row({"campaignId": 9}, report_date=date(2026, 5, 21))
        assert row["impressions"] == 0
        assert row["clicks"] == 0
        assert row["cost"] == 0

    def test_falls_back_to_v2_dimensions_without_metadata(self):
        v2_row = {"campaignId": 9, "campaignName": "From V2", "campaignStatus": "paused", "cost": 5.0}
        row = map_v2_campaign_row(v2_row, report_date=date(2026, 5, 21))
        assert row["campaignName"] == "From V2"
        assert row["campaignStatus"] == "PAUSED"


class TestMergeSbRows:
    def test_concatenates_disjoint_sets(self):
        v3 = [{"campaignId": "v4-1", "cost": 100.0}]
        legacy = [{"campaignId": "leg-1", "cost": 50.0}, {"campaignId": "leg-2", "cost": 25.0}]
        merged = merge_sb_rows(v3, legacy)
        assert len(merged) == 3
        assert sum(r["cost"] for r in merged) == pytest.approx(175.0)

    def test_dedup_guard_drops_overlapping_legacy_row(self):
        v3 = [{"campaignId": "shared", "cost": 100.0}]
        legacy = [{"campaignId": "shared", "cost": 999.0}, {"campaignId": "leg-1", "cost": 10.0}]
        merged = merge_sb_rows(v3, legacy)
        assert len(merged) == 2
        assert sum(r["cost"] for r in merged) == pytest.approx(110.0)

    def test_empty_legacy_returns_v3_only(self):
        v3 = [{"campaignId": "v4-1", "cost": 100.0}]
        assert merge_sb_rows(v3, []) == v3


# ----------------------------------------------------------------------------
# End-to-end augmentation with a mocked Ads SDK
# ----------------------------------------------------------------------------

def _api_response(payload):
    return SimpleNamespace(payload=payload)


def _make_campaigns_v4_mock(campaign_pages):
    """Build a CampaignsV4 class mock whose list_campaigns returns *pages*."""
    instance = MagicMock()
    instance.list_campaigns.side_effect = [_api_response(p) for p in campaign_pages]
    cls = MagicMock(return_value=instance)
    return cls, instance


def _make_v2_reports_mock(records_by_date):
    """Build an SbV2Reports class mock.

    *records_by_date* maps a ``YYYYMMDD`` string to the list of v2 records that
    a download for that report date should return.
    """
    instance = MagicMock()
    state = {"counter": 0, "report_dates": []}

    def post_report(recordType, body):  # noqa: N803 (matches SDK signature)
        state["report_dates"].append(body["reportDate"])
        rid = f"report-{body['reportDate']}"
        return _api_response({"reportId": rid, "status": "IN_PROGRESS"})

    def get_report(reportId):  # noqa: N803
        report_date = reportId.replace("report-", "")
        return _api_response({"status": "SUCCESS", "location": f"https://dl/{report_date}"})

    def download_report(url, format):  # noqa: A002
        report_date = url.rsplit("/", 1)[-1]
        records = records_by_date.get(report_date, [])
        return _api_response(gzip.compress(json.dumps(records).encode()))

    instance.post_report.side_effect = post_report
    instance.get_report.side_effect = get_report
    instance.download_report.side_effect = download_report
    cls = MagicMock(return_value=instance)
    return cls, instance, state


# Glove Station US, May 21–22 2026: v3 captured only the multi-ad-group
# campaign ($1,383.89 total); legacy campaigns added $2,040.73, for a console
# total of $3,424.62.
_V3_ROWS = [
    {"date": "2026-05-21", "campaignId": "v4-1", "campaignName": "GS - SBV (multi)",
     "campaignStatus": "ENABLED", "campaignBudgetAmount": 500, "impressions": 5000,
     "clicks": 120, "cost": 691.945, "purchases": 12, "sales": 2000.0,
     "unitsSoldClicks": 14, "detailPageViewsClicks": 60, "newToBrandPurchases": 4,
     "newToBrandSales": 900.0},
    {"date": "2026-05-22", "campaignId": "v4-1", "campaignName": "GS - SBV (multi)",
     "campaignStatus": "ENABLED", "campaignBudgetAmount": 500, "impressions": 5200,
     "clicks": 130, "cost": 691.945, "purchases": 13, "sales": 2100.0,
     "unitsSoldClicks": 15, "detailPageViewsClicks": 65, "newToBrandPurchases": 5,
     "newToBrandSales": 950.0},
]

_V4_CAMPAIGN_PAGES = [
    {
        "campaigns": [
            {"campaignId": "v4-1", "name": "GS - SBV (multi)", "state": "enabled",
             "isMultiAdGroupsEnabled": True},
            {"campaignId": "leg-1", "name": "GS - SB Legacy A", "state": "enabled",
             "isMultiAdGroupsEnabled": False, "budget": {"budget": 300.0}},
        ],
        "nextToken": "page2",
    },
    {
        "campaigns": [
            {"campaignId": "leg-2", "name": "GS - SB Legacy B", "state": "paused",
             "isMultiAdGroupsEnabled": False, "budget": {"budget": 200.0}},
        ],
    },
]

# v2 reports return ALL campaigns (including the v4 one); our filter keeps only
# legacy ids. Legacy per-day: 765.305 + 255.06 = 1020.365; x2 days = 2040.73.
_V2_RECORDS_BY_DATE = {
    "20260521": [
        {"campaignId": "v4-1", "cost": 691.945, "impressions": 5000, "clicks": 120,
         "attributedSales14d": 2000.0},  # ignored (not legacy)
        {"campaignId": "leg-1", "cost": 765.305, "impressions": 800, "clicks": 20,
         "attributedSales14d": 1500.0, "attributedConversions14d": 6,
         "attributedUnitsOrdered14d": 7, "attributedDetailPageViewsClicks14d": 30,
         "attributedOrdersNewToBrand14d": 2, "attributedSalesNewToBrand14d": 500.0},
        {"campaignId": "leg-2", "cost": 255.06, "impressions": 400, "clicks": 9,
         "attributedSales14d": 600.0},
    ],
    "20260522": [
        {"campaignId": "leg-1", "cost": 765.305, "impressions": 820, "clicks": 21,
         "attributedSales14d": 1550.0},
        {"campaignId": "leg-2", "cost": 255.06, "impressions": 410, "clicks": 10,
         "attributedSales14d": 620.0},
    ],
}

_CONSOLE_TOTAL = 3424.62
_V3_ONLY_TOTAL = 1383.89


class TestAugmentSbCampaignsContent:
    def _run(self, campaign_pages=_V4_CAMPAIGN_PAGES, records=_V2_RECORDS_BY_DATE):
        v4_cls, _ = _make_campaigns_v4_mock(campaign_pages)
        v2_cls, _, state = _make_v2_reports_mock(records)
        with patch.object(ads_sb_legacy, "CampaignsV4", v4_cls), \
             patch.object(ads_sb_legacy, "SbV2Reports", v2_cls):
            content = augment_sb_campaigns_content(
                json.dumps(_V3_ROWS).encode(),
                credentials={"profile_id": "1"},
                marketplace="US",
                start_date=date(2026, 5, 21),
                end_date=date(2026, 5, 22),
                sleep_fn=lambda _s: None,
            )
        return content, state

    def test_merged_total_matches_console(self):
        content, _ = self._run()
        rows = json.loads(content)
        total = sum(float(r.get("cost", 0)) for r in rows)
        # v3-only under-counted; merge brings it up to the console total.
        assert pytest.approx(_V3_ONLY_TOTAL, abs=0.005) == sum(
            float(r["cost"]) for r in _V3_ROWS
        )
        assert pytest.approx(_CONSOLE_TOTAL, abs=0.005) == total

    def test_legacy_campaigns_present_v4_excluded_from_legacy(self):
        content, _ = self._run()
        rows = json.loads(content)
        ids = {r["campaignId"] for r in rows}
        assert ids == {"v4-1", "leg-1", "leg-2"}
        # leg-1 / leg-2 appear once per day (2 days each); v4-1 only from v3.
        leg_rows = [r for r in rows if r["campaignId"].startswith("leg")]
        assert len(leg_rows) == 4

    def test_requests_one_v2_report_per_day(self):
        _, state = self._run()
        assert sorted(state["report_dates"]) == ["20260521", "20260522"]

    def test_merged_content_converts_to_tsv_with_full_total(self):
        content, _ = self._run()
        tsv, converted = maybe_convert_to_tsv(content, "ads_api", "sbCampaigns")
        assert converted
        lines = tsv.decode().strip().split("\n")
        header = lines[0].split("\t")
        cost_idx = header.index("cost")
        total = sum(float(ln.split("\t")[cost_idx]) for ln in lines[1:])
        assert pytest.approx(_CONSOLE_TOTAL, abs=0.005) == total

    def test_no_legacy_campaigns_returns_v3_unchanged(self):
        pages = [{"campaigns": [
            {"campaignId": "v4-1", "name": "multi", "state": "enabled",
             "isMultiAdGroupsEnabled": True},
        ]}]
        v4_cls, _ = _make_campaigns_v4_mock(pages)
        v2_cls, v2_instance, _ = _make_v2_reports_mock({})
        original = json.dumps(_V3_ROWS).encode()
        with patch.object(ads_sb_legacy, "CampaignsV4", v4_cls), \
             patch.object(ads_sb_legacy, "SbV2Reports", v2_cls):
            content = augment_sb_campaigns_content(
                original,
                credentials={"profile_id": "1"},
                marketplace="US",
                start_date=date(2026, 5, 21),
                end_date=date(2026, 5, 22),
                sleep_fn=lambda _s: None,
            )
        assert json.loads(content) == _V3_ROWS
        v2_instance.post_report.assert_not_called()

    def test_best_effort_on_sdk_failure_returns_v3_unchanged(self):
        original = json.dumps(_V3_ROWS).encode()
        failing_cls = MagicMock(side_effect=RuntimeError("Ads API down"))
        with patch.object(ads_sb_legacy, "CampaignsV4", failing_cls):
            content = augment_sb_campaigns_content(
                original,
                credentials={"profile_id": "1"},
                marketplace="US",
                start_date=date(2026, 5, 21),
                end_date=date(2026, 5, 22),
                sleep_fn=lambda _s: None,
            )
        assert json.loads(content) == _V3_ROWS

    def test_non_json_content_returned_unchanged(self):
        raw = b"not json at all"
        content = augment_sb_campaigns_content(
            raw,
            credentials={"profile_id": "1"},
            marketplace="US",
            start_date=date(2026, 5, 21),
            end_date=date(2026, 5, 22),
            sleep_fn=lambda _s: None,
        )
        assert content is raw


# ----------------------------------------------------------------------------
# Retry-on-transient-failure (the deployed regression: the v2 calls were reset
# during the scheduler's concurrent fan-out and the whole augmentation aborted,
# dropping legacy campaigns so the export under-counted the console).
# ----------------------------------------------------------------------------

class TestRetryClassification:
    def test_connection_reset_is_retryable(self):
        # The exact error seen in production (wrapped by requests).
        wrapped = requests.exceptions.ConnectionError(
            ConnectionResetError(104, "Connection reset by peer")
        )
        assert _is_retryable(wrapped)
        # ...and the bare builtin form.
        assert _is_retryable(ConnectionResetError(104, "Connection reset by peer"))

    def test_timeout_is_retryable(self):
        assert _is_retryable(requests.exceptions.Timeout("slow"))

    def test_429_and_5xx_are_retryable(self):
        assert _is_retryable(AdvertisingApiTooManyRequestsException(429, {}))
        assert _is_retryable(AdvertisingApiTemporarilyUnavailableException(503, {}))

    def test_403_is_not_retryable(self):
        assert not _is_retryable(AdvertisingApiForbiddenException(403, {}))

    def test_value_error_is_not_retryable(self):
        assert not _is_retryable(ValueError("bad config"))


class TestCallWithRetry:
    def test_retries_then_succeeds(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise requests.exceptions.ConnectionError("reset")
            return "ok"

        result = _call_with_retry(flaky, description="t", sleep_fn=lambda _s: None)
        assert result == "ok"
        assert calls["n"] == 3

    def test_non_retryable_raises_immediately(self):
        calls = {"n": 0}

        def boom():
            calls["n"] += 1
            raise AdvertisingApiForbiddenException(403, {})

        with pytest.raises(AdvertisingApiForbiddenException):
            _call_with_retry(boom, description="t", sleep_fn=lambda _s: None)
        assert calls["n"] == 1

    def test_exhausts_attempts_then_raises_last_error(self):
        with pytest.raises(requests.exceptions.ConnectionError):
            _call_with_retry(
                lambda: (_ for _ in ()).throw(requests.exceptions.ConnectionError("reset")),
                description="t",
                sleep_fn=lambda _s: None,
                max_attempts=3,
            )


def _make_v2_reports_mock_with_failures(records_by_date, *, get_report_failures=0):
    """Like _make_v2_reports_mock but the first *get_report_failures* status
    polls raise the production connection-reset error before succeeding."""
    instance = MagicMock()
    state = {"report_dates": [], "get_report_calls": 0}

    def post_report(recordType, body):  # noqa: N803
        state["report_dates"].append(body["reportDate"])
        return _api_response({"reportId": f"report-{body['reportDate']}", "status": "IN_PROGRESS"})

    def get_report(reportId):  # noqa: N803
        state["get_report_calls"] += 1
        if state["get_report_calls"] <= get_report_failures:
            raise requests.exceptions.ConnectionError(
                ConnectionResetError(104, "Connection reset by peer")
            )
        report_date = reportId.replace("report-", "")
        return _api_response({"status": "SUCCESS", "location": f"https://dl/{report_date}"})

    def download_report(url, format):  # noqa: A002
        report_date = url.rsplit("/", 1)[-1]
        return _api_response(gzip.compress(json.dumps(records_by_date.get(report_date, [])).encode()))

    instance.post_report.side_effect = post_report
    instance.get_report.side_effect = get_report
    instance.download_report.side_effect = download_report
    return MagicMock(return_value=instance), instance, state


class TestAugmentRetriesTransientErrors:
    def test_transient_get_report_reset_recovers_and_matches_console(self):
        """The bug: a single connection reset aborted the whole augmentation,
        leaving the v3-only under-count. With retries the legacy rows are still
        fetched and the merged total matches the console."""
        v4_cls, _ = _make_campaigns_v4_mock(_V4_CAMPAIGN_PAGES)
        v2_cls, _, state = _make_v2_reports_mock_with_failures(
            _V2_RECORDS_BY_DATE, get_report_failures=2
        )
        with patch.object(ads_sb_legacy, "CampaignsV4", v4_cls), \
             patch.object(ads_sb_legacy, "SbV2Reports", v2_cls):
            content = augment_sb_campaigns_content(
                json.dumps(_V3_ROWS).encode(),
                credentials={"profile_id": "1"},
                marketplace="US",
                start_date=date(2026, 5, 21),
                end_date=date(2026, 5, 22),
                sleep_fn=lambda _s: None,
            )
        rows = json.loads(content)
        total = sum(float(r.get("cost", 0)) for r in rows)
        assert pytest.approx(_CONSOLE_TOTAL, abs=0.005) == total
        assert state["get_report_calls"] > 2  # proves a retry occurred

    def test_transient_list_campaigns_reset_recovers(self):
        instance = MagicMock()
        instance.list_campaigns.side_effect = [
            requests.exceptions.ConnectionError("reset"),
            _api_response(_V4_CAMPAIGN_PAGES[0]),
            _api_response(_V4_CAMPAIGN_PAGES[1]),
        ]
        v4_cls = MagicMock(return_value=instance)
        v2_cls, _, _ = _make_v2_reports_mock_with_failures(_V2_RECORDS_BY_DATE)
        with patch.object(ads_sb_legacy, "CampaignsV4", v4_cls), \
             patch.object(ads_sb_legacy, "SbV2Reports", v2_cls):
            content = augment_sb_campaigns_content(
                json.dumps(_V3_ROWS).encode(),
                credentials={"profile_id": "1"},
                marketplace="US",
                start_date=date(2026, 5, 21),
                end_date=date(2026, 5, 22),
                sleep_fn=lambda _s: None,
            )
        total = sum(float(r.get("cost", 0)) for r in json.loads(content))
        assert pytest.approx(_CONSOLE_TOTAL, abs=0.005) == total

    def test_persistent_reset_falls_back_to_v3_only(self):
        """If the resets never clear, degrade to the v3-only export (best-effort)
        rather than failing the job."""
        v4_cls, _ = _make_campaigns_v4_mock(_V4_CAMPAIGN_PAGES)
        v2_cls, _, _ = _make_v2_reports_mock_with_failures(
            _V2_RECORDS_BY_DATE, get_report_failures=10_000
        )
        with patch.object(ads_sb_legacy, "CampaignsV4", v4_cls), \
             patch.object(ads_sb_legacy, "SbV2Reports", v2_cls):
            content = augment_sb_campaigns_content(
                json.dumps(_V3_ROWS).encode(),
                credentials={"profile_id": "1"},
                marketplace="US",
                start_date=date(2026, 5, 21),
                end_date=date(2026, 5, 22),
                sleep_fn=lambda _s: None,
            )
        assert json.loads(content) == _V3_ROWS
