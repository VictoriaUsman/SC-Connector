"""Tests for the scheduler function — schedule fan-out, capping, and error handling."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")
os.environ.setdefault("WORKFLOW_NAME", "test-workflow")
os.environ.setdefault("WORKFLOW_LOCATION", "us-central1")


def _make_request(body: dict | None = None) -> MagicMock:
    req = MagicMock()
    req.get_json.return_value = body or {}
    return req


def _make_schedule(
    schedule_id: str = "s1",
    client_id: str = "c1",
    frequency: str = "daily",
) -> dict:
    return {
        "id": schedule_id,
        "client_id": client_id,
        "api_source": "sp_api",
        "report_type": "GET_FLAT_FILE_OPEN_LISTINGS_DATA",
        "marketplace": "US",
        "frequency": frequency,
        "report_params": {},
        "is_active": True,
        "reconciliation_days": [],
    }


@pytest.fixture(autouse=True)
def _mock_firestore():
    with patch("shared.firestore_utils.firestore.Client"):
        yield


@pytest.fixture()
def mock_launch():
    """Mock the shared launch_for_marketplace so no real workflow calls happen."""
    with patch("scheduler.main.launch_for_marketplace") as m:
        m.return_value = ["job-x"]
        yield m


# ---------------------------------------------------------------------------
# Core flow
# ---------------------------------------------------------------------------

class TestSchedulerHandler:
    def test_no_due_schedules(self, mock_launch):
        from scheduler.main import handler

        with patch("scheduler.main.list_due_schedules", return_value=[]):
            body, status = handler(_make_request())

        assert status == 200
        assert body["launched"] == 0
        mock_launch.assert_not_called()

    def test_launches_workflow_for_due_schedule(self, mock_launch):
        from scheduler.main import handler

        sched = _make_schedule()

        with (
            patch("scheduler.main.list_due_schedules", return_value=[sched]),
            patch("scheduler.main.get_client", return_value={"id": "c1", "is_active": True}),
            patch("scheduler.main.update_schedule_run_times") as mock_update,
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["launched"] == 1
        mock_launch.assert_called_once()

        call_kwargs = mock_launch.call_args
        assert call_kwargs[0][2] == sched  # schedule dict
        assert call_kwargs[0][3] == "c1"   # client_id
        assert call_kwargs[0][4] == "US"   # marketplace

        mock_update.assert_called_once()

    def test_skips_inactive_client(self, mock_launch):
        from scheduler.main import handler

        sched = _make_schedule()

        with (
            patch("scheduler.main.list_due_schedules", return_value=[sched]),
            patch("scheduler.main.get_client", return_value={"id": "c1", "is_active": False}),
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["launched"] == 0
        assert body["skipped"] == 1
        mock_launch.assert_not_called()

    def test_skips_missing_client(self, mock_launch):
        from scheduler.main import handler

        sched = _make_schedule()

        with (
            patch("scheduler.main.list_due_schedules", return_value=[sched]),
            patch("scheduler.main.get_client", return_value=None),
        ):
            body, status = handler(_make_request())

        assert body["launched"] == 0
        assert body["skipped"] == 1

    def test_caps_per_client(self, mock_launch):
        from scheduler.main import handler, MAX_EXECUTIONS_PER_CLIENT

        schedules = [_make_schedule(schedule_id=f"s{i}") for i in range(MAX_EXECUTIONS_PER_CLIENT + 5)]

        with (
            patch("scheduler.main.list_due_schedules", return_value=schedules),
            patch("scheduler.main.get_client", return_value={"id": "c1", "is_active": True}),
            patch("scheduler.main.update_schedule_run_times"),
        ):
            body, status = handler(_make_request())

        assert body["launched"] == MAX_EXECUTIONS_PER_CLIENT
        assert body["skipped"] == 5

    def test_multiple_clients(self, mock_launch):
        from scheduler.main import handler

        scheds = [
            _make_schedule(schedule_id="s1", client_id="c1"),
            _make_schedule(schedule_id="s2", client_id="c2"),
        ]

        def fake_get_client(cid):
            return {"id": cid, "is_active": True}

        with (
            patch("scheduler.main.list_due_schedules", return_value=scheds),
            patch("scheduler.main.get_client", side_effect=fake_get_client),
            patch("scheduler.main.update_schedule_run_times"),
        ):
            body, status = handler(_make_request())

        assert body["launched"] == 2


# ---------------------------------------------------------------------------
# Multi-marketplace fan-out (the scenario that caused duplicate Drive folders)
# ---------------------------------------------------------------------------

class TestMultiMarketplaceFanOut:
    """Verify the scheduler fans out one execution per (client, marketplace)."""

    def test_two_marketplaces_launch_two_executions(self, mock_launch):
        from scheduler.main import handler

        sched = {
            "id": "s1",
            "client_ids": ["testy"],
            "api_source": "sp_api",
            "report_type": "GET_SALES_AND_TRAFFIC_REPORT",
            "marketplaces": ["US", "CA"],
            "frequency": "daily",
            "report_params": {},
            "is_active": True,
            "reconciliation_days": [],
        }

        with (
            patch("scheduler.main.list_due_schedules", return_value=[sched]),
            patch("scheduler.main.get_client", return_value={"id": "testy", "is_active": True, "name": "testy"}),
            patch("scheduler.main.update_schedule_run_times"),
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["launched"] == 2
        assert mock_launch.call_count == 2

        marketplaces_launched = {c[0][4] for c in mock_launch.call_args_list}
        assert marketplaces_launched == {"US", "CA"}

    def test_folder_name_passed_to_launcher(self, mock_launch):
        """Verify schedule with folder_name passes it through to launch_for_marketplace."""
        from scheduler.main import handler

        sched = {
            "id": "s1",
            "client_ids": ["testy"],
            "api_source": "sp_api",
            "report_type": "GET_SALES_AND_TRAFFIC_REPORT",
            "marketplaces": ["US"],
            "frequency": "daily",
            "report_params": {},
            "is_active": True,
            "folder_name": "testem",
            "subfolder_strategy": "date",
            "reconciliation_days": [],
        }

        with (
            patch("scheduler.main.list_due_schedules", return_value=[sched]),
            patch("scheduler.main.get_client", return_value={"id": "testy", "is_active": True}),
            patch("scheduler.main.update_schedule_run_times"),
        ):
            body, status = handler(_make_request())

        call_args = mock_launch.call_args
        assert call_args[0][2]["folder_name"] == "testem"
        assert call_args[0][2]["subfolder_strategy"] == "date"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestSchedulerErrors:
    def test_workflow_start_failure_counted_as_error(self, mock_launch):
        from scheduler.main import handler

        mock_launch.side_effect = RuntimeError("API down")

        sched = _make_schedule()

        with (
            patch("scheduler.main.list_due_schedules", return_value=[sched]),
            patch("scheduler.main.get_client", return_value={"id": "c1", "is_active": True}),
            patch("scheduler.main.update_schedule_run_times"),
        ):
            body, status = handler(_make_request())

        assert body["launched"] == 0
        assert body["errors"] == 1


# ---------------------------------------------------------------------------
# Next run computation (uses shared.schedule_compute.compute_next_run)
# ---------------------------------------------------------------------------

class TestComputeNextRun:
    def test_hourly(self):
        from shared.schedule_compute import compute_next_run

        now = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)
        result = compute_next_run(now, {"type": "hourly"})
        assert result == now + timedelta(hours=1)

    def test_daily(self):
        from shared.schedule_compute import compute_next_run

        now = datetime(2026, 3, 19, 2, 0, tzinfo=timezone.utc)
        result = compute_next_run(now, {"type": "daily", "time": "03:00"})
        assert result == datetime(2026, 3, 19, 3, 0, tzinfo=timezone.utc)

    def test_daily_past_time_advances_to_next_day(self):
        from shared.schedule_compute import compute_next_run

        now = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)
        result = compute_next_run(now, {"type": "daily", "time": "03:00"})
        assert result == datetime(2026, 3, 20, 3, 0, tzinfo=timezone.utc)

    def test_weekly_selects_next_matching_day(self):
        from shared.schedule_compute import compute_next_run

        # 2026-03-18 is a Wednesday (weekday=2)
        now = datetime(2026, 3, 18, 12, 0, tzinfo=timezone.utc)
        result = compute_next_run(now, {"type": "weekly", "time": "03:00", "days_of_week": [3]})
        # Next match: Thursday (3) = 2026-03-19
        assert result == datetime(2026, 3, 19, 3, 0, tzinfo=timezone.utc)

    def test_monthly(self):
        from shared.schedule_compute import compute_next_run

        now = datetime(2026, 3, 10, 12, 0, tzinfo=timezone.utc)
        result = compute_next_run(now, {"type": "monthly", "time": "03:00", "day_of_month": 15})
        assert result == datetime(2026, 3, 15, 3, 0, tzinfo=timezone.utc)

    def test_unknown_type_defaults_to_daily_delta(self):
        from shared.schedule_compute import compute_next_run

        now = datetime(2026, 3, 19, 12, 0, tzinfo=timezone.utc)
        result = compute_next_run(now, {"type": "biweekly"})
        assert result == now + timedelta(days=1)
