"""Tests for the event report scheduler function."""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")
os.environ.setdefault("WORKFLOW_NAME", "test-workflow")
os.environ.setdefault("WORKFLOW_LOCATION", "us-central1")


def _make_request() -> MagicMock:
    req = MagicMock()
    req.get_json.return_value = {}
    return req


def _make_bot_config(
    client_id: str = "c1",
    marketplaces: list[str] | None = None,
    enabled: bool = True,
) -> dict:
    return {
        "id": client_id,
        "client_id": client_id,
        "hourly_bot": {"enabled": enabled},
        "marketplaces": marketplaces or ["US"],
        "slack_channel_id": "C123",
    }


def _make_event(
    event_id: str = "e1",
    status: str = "live",
    start_date: str = "2026-07-13",
    end_date: str = "2026-07-14",
) -> dict:
    return {
        "id": event_id,
        "name": "Prime Day 2026",
        "start_date": start_date,
        "end_date": end_date,
        "status": status,
    }


class TestNoOpWhenNoEvent:
    def test_returns_immediately_when_no_live_event(self):
        from event_report_scheduler.main import handler

        with (
            patch("event_report_scheduler.main.list_events", return_value=[]),
            patch("event_report_scheduler.main.get_live_event", return_value=None),
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["launched"] == 0

    def test_returns_immediately_when_no_enabled_configs(self):
        from event_report_scheduler.main import handler

        with (
            patch("event_report_scheduler.main.list_events", return_value=[]),
            patch("event_report_scheduler.main.get_live_event", return_value=_make_event()),
            patch("event_report_scheduler.main.list_bot_configs", return_value=[
                _make_bot_config(enabled=False),
            ]),
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["launched"] == 0


class TestLaunchReports:
    def test_launches_orders_report(self):
        from event_report_scheduler.main import handler, ORDERS_REPORT

        with (
            patch("event_report_scheduler.main.list_events", return_value=[]),
            patch("event_report_scheduler.main.get_live_event", return_value=_make_event()),
            patch("event_report_scheduler.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("event_report_scheduler.main.get_client", return_value={
                "id": "c1", "is_active": True, "sp_api_secret_name": "secret",
            }),
            patch("event_report_scheduler.main.create_job", return_value="job-1"),
            patch("event_report_scheduler.main.launch_execution") as mock_launch,
            patch("event_report_scheduler.main.time"),
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["launched"] >= 1
        launched_payloads = [c[0][1] for c in mock_launch.call_args_list]
        report_types = [p["report_type"] for p in launched_payloads]
        assert ORDERS_REPORT in report_types

    def test_launches_ads_reports_on_ads_slot(self):
        from event_report_scheduler.main import handler, ADS_REPORTS

        fixed_now = datetime(2026, 7, 13, 14, 20, 0, tzinfo=timezone.utc)  # :20 ads slot

        with (
            patch("event_report_scheduler.main.datetime") as mock_dt,
            patch("event_report_scheduler.main.list_events", return_value=[]),
            patch("event_report_scheduler.main.get_live_event", return_value=_make_event()),
            patch("event_report_scheduler.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("event_report_scheduler.main.get_client", return_value={
                "id": "c1", "is_active": True,
                "sp_api_secret_name": "secret", "ads_profile_id": "123",
            }),
            patch("event_report_scheduler.main.create_job", return_value="job-1"),
            patch("event_report_scheduler.main.launch_execution") as mock_launch,
            patch("event_report_scheduler.main.time"),
        ):
            mock_dt.now.return_value = fixed_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            body, status = handler(_make_request())

        assert status == 200
        launched_payloads = [c[0][1] for c in mock_launch.call_args_list]
        report_types = [p["report_type"] for p in launched_payloads]
        for ads_rt in ADS_REPORTS:
            assert ads_rt in report_types

    def test_skips_ads_off_ads_slot(self):
        from event_report_scheduler.main import handler, ADS_REPORTS

        fixed_now = datetime(2026, 7, 13, 14, 50, 0, tzinfo=timezone.utc)  # :50 orders-only slot

        with (
            patch("event_report_scheduler.main.datetime") as mock_dt,
            patch("event_report_scheduler.main.list_events", return_value=[]),
            patch("event_report_scheduler.main.get_live_event", return_value=_make_event()),
            patch("event_report_scheduler.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("event_report_scheduler.main.get_client", return_value={
                "id": "c1", "is_active": True,
                "sp_api_secret_name": "secret", "ads_profile_id": "123",
            }),
            patch("event_report_scheduler.main.create_job", return_value="job-1"),
            patch("event_report_scheduler.main.launch_execution") as mock_launch,
            patch("event_report_scheduler.main.time"),
        ):
            mock_dt.now.return_value = fixed_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            body, status = handler(_make_request())

        launched_payloads = [c[0][1] for c in mock_launch.call_args_list]
        report_types = [p["report_type"] for p in launched_payloads]
        for ads_rt in ADS_REPORTS:
            assert ads_rt not in report_types

    def test_skips_client_without_sp_credentials(self):
        from event_report_scheduler.main import handler

        with (
            patch("event_report_scheduler.main.list_events", return_value=[]),
            patch("event_report_scheduler.main.get_live_event", return_value=_make_event()),
            patch("event_report_scheduler.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("event_report_scheduler.main.get_client", return_value={
                "id": "c1", "is_active": True,
            }),
            patch("event_report_scheduler.main.create_job") as mock_create,
            patch("event_report_scheduler.main.launch_execution"),
            patch("event_report_scheduler.main.time"),
        ):
            body, status = handler(_make_request())

        mock_create.assert_not_called()


class TestPriorYearBackfill:
    """When the live event is linked to a prior-year event, the scheduler must
    backfill that event's window into BigQuery once, so the midnight recap's YoY
    queries have data instead of rendering 0 (the reported bug)."""

    _CONFIGS = [_make_bot_config()]

    @staticmethod
    def _client(sp: bool = True, ads: bool = True) -> dict:
        c = {"id": "c1", "is_active": True}
        if sp:
            c["sp_api_secret_name"] = "secret"
        if ads:
            c["ads_profile_id"] = "123"
        return c

    def test_backfills_orders_and_ads_when_linked_and_unsynced(self):
        from event_report_scheduler.main import (
            _maybe_backfill_prior_year, ORDERS_REPORT, ADS_REPORTS,
        )

        live = _make_event(event_id="live1", start_date="2026-06-08", end_date="2026-06-10")
        live["prior_event_id"] = "prior1"
        # Recent prior event (within ads retention window).
        prior = _make_event(event_id="prior1", start_date="2026-04-08", end_date="2026-04-10")
        now = datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc)

        with (
            patch("event_report_scheduler.main.get_event", return_value=prior),
            patch("event_report_scheduler.main.get_client", return_value=self._client()),
            patch("event_report_scheduler.main.create_job", return_value="job-1"),
            patch("event_report_scheduler.main.launch_execution") as mock_launch,
            patch("event_report_scheduler.main.update_event") as mock_update,
            patch("event_report_scheduler.main.time"),
        ):
            launched = _maybe_backfill_prior_year("parent", live, self._CONFIGS, now)

        assert launched == 1 + len(ADS_REPORTS)
        payloads = [c[0][1] for c in mock_launch.call_args_list]
        by_type = {p["report_type"]: p for p in payloads}
        assert ORDERS_REPORT in by_type
        for rt in ADS_REPORTS:
            assert rt in by_type
        # Prior-year reports must target the prior event's dates (range pull).
        ads_payload = by_type[ADS_REPORTS[0]]
        assert ads_payload["report_params"]["startDate"] == "2026-04-08"
        assert ads_payload["report_params"]["endDate"] == "2026-04-10"
        # The link is recorded as synced so it does not re-fan-out every run.
        mock_update.assert_called_once()
        assert mock_update.call_args[0][1]["prior_year_synced_for"] == "prior1"

    def test_skips_when_already_synced(self):
        from event_report_scheduler.main import _maybe_backfill_prior_year

        live = _make_event(event_id="live1")
        live["prior_event_id"] = "prior1"
        live["prior_year_synced_for"] = "prior1"
        now = datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc)

        with (
            patch("event_report_scheduler.main.get_event") as mock_get,
            patch("event_report_scheduler.main.launch_execution") as mock_launch,
            patch("event_report_scheduler.main.update_event") as mock_update,
        ):
            launched = _maybe_backfill_prior_year("parent", live, self._CONFIGS, now)

        assert launched == 0
        mock_get.assert_not_called()
        mock_launch.assert_not_called()
        mock_update.assert_not_called()

    def test_noop_without_prior_link(self):
        from event_report_scheduler.main import _maybe_backfill_prior_year

        live = _make_event(event_id="live1")  # no prior_event_id
        now = datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc)

        with patch("event_report_scheduler.main.launch_execution") as mock_launch:
            launched = _maybe_backfill_prior_year("parent", live, self._CONFIGS, now)

        assert launched == 0
        mock_launch.assert_not_called()

    def test_skips_ads_when_prior_event_too_old_but_still_backfills_orders(self):
        from event_report_scheduler.main import (
            _maybe_backfill_prior_year, ORDERS_REPORT, ADS_REPORTS,
        )

        live = _make_event(event_id="live1", start_date="2026-06-08", end_date="2026-06-10")
        live["prior_event_id"] = "prior1"
        # A true prior-year event: orders retained (~2y) but ads are out of
        # Amazon's reporting window, so ads must be skipped.
        prior = _make_event(event_id="prior1", start_date="2025-06-08", end_date="2025-06-10")
        now = datetime(2026, 6, 10, 14, 0, tzinfo=timezone.utc)

        with (
            patch("event_report_scheduler.main.get_event", return_value=prior),
            patch("event_report_scheduler.main.get_client", return_value=self._client()),
            patch("event_report_scheduler.main.create_job", return_value="job-1"),
            patch("event_report_scheduler.main.launch_execution") as mock_launch,
            patch("event_report_scheduler.main.update_event"),
            patch("event_report_scheduler.main.time"),
        ):
            launched = _maybe_backfill_prior_year("parent", live, self._CONFIGS, now)

        assert launched == 1
        report_types = [c[0][1]["report_type"] for c in mock_launch.call_args_list]
        assert ORDERS_REPORT in report_types
        for rt in ADS_REPORTS:
            assert rt not in report_types

    def test_handler_invokes_backfill_for_linked_event(self):
        from event_report_scheduler.main import handler

        live = _make_event(start_date="2026-06-08", end_date="2026-06-10")
        live["prior_event_id"] = "prior1"
        prior = _make_event(event_id="prior1", start_date="2026-04-08", end_date="2026-04-10")
        now = datetime(2026, 6, 10, 14, 30, tzinfo=timezone.utc)  # off the hour

        with (
            patch("event_report_scheduler.main.datetime") as mock_dt,
            patch("event_report_scheduler.main.list_events", return_value=[]),
            patch("event_report_scheduler.main.get_live_event", return_value=live),
            patch("event_report_scheduler.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("event_report_scheduler.main.get_event", return_value=prior),
            patch("event_report_scheduler.main.get_client", return_value=self._client()),
            patch("event_report_scheduler.main.create_job", return_value="job-1"),
            patch("event_report_scheduler.main.launch_execution") as mock_launch,
            patch("event_report_scheduler.main.update_event"),
            patch("event_report_scheduler.main.time"),
        ):
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            body, status = handler(_make_request())

        assert status == 200
        frequencies = [c[0][1].get("frequency") for c in mock_launch.call_args_list]
        assert "event_prior_year" in frequencies


class TestAutoTransition:
    def test_upcoming_to_live(self):
        from event_report_scheduler.main import _auto_transition_events

        event = _make_event(status="upcoming", start_date="2026-07-13")

        with (
            patch("event_report_scheduler.main.list_events", return_value=[event]),
            patch("event_report_scheduler.main.update_event") as mock_update,
        ):
            _auto_transition_events(date(2026, 7, 13))

        mock_update.assert_called_once()
        args = mock_update.call_args[0]
        assert args[0] == "e1"
        assert args[1]["status"] == "live"

    def test_live_to_completed(self):
        from event_report_scheduler.main import _auto_transition_events

        event = _make_event(status="live", end_date="2026-07-14")

        with (
            patch("event_report_scheduler.main.list_events", return_value=[event]),
            patch("event_report_scheduler.main.update_event") as mock_update,
        ):
            _auto_transition_events(date(2026, 7, 15))

        mock_update.assert_called_once()
        assert mock_update.call_args[0][1]["status"] == "completed"

    def test_no_transition_when_dates_not_met(self):
        from event_report_scheduler.main import _auto_transition_events

        event = _make_event(status="upcoming", start_date="2026-07-13")

        with (
            patch("event_report_scheduler.main.list_events", return_value=[event]),
            patch("event_report_scheduler.main.update_event") as mock_update,
        ):
            _auto_transition_events(date(2026, 7, 12))

        mock_update.assert_not_called()
