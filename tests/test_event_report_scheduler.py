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


@pytest.fixture(autouse=True)
def _mock_firestore():
    with patch("shared.firestore_utils.firestore.Client"):
        yield


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

    def test_launches_ads_reports_on_the_hour(self):
        from event_report_scheduler.main import handler, ADS_REPORTS

        fixed_now = datetime(2026, 7, 13, 14, 0, 0, tzinfo=timezone.utc)  # on the hour

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

    def test_skips_ads_off_the_hour(self):
        from event_report_scheduler.main import handler, ADS_REPORTS

        fixed_now = datetime(2026, 7, 13, 14, 30, 0, tzinfo=timezone.utc)  # half past

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
