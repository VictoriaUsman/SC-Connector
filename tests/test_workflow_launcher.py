"""Tests for shared.workflow_launcher — payload building, retry logic, and marketplace fan-out."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, date, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")
os.environ.setdefault("WORKFLOW_NAME", "test-workflow")
os.environ.setdefault("WORKFLOW_LOCATION", "us-central1")


@pytest.fixture(autouse=True)
def _mock_firestore():
    with patch("shared.firestore_utils.firestore.Client"):
        yield


@pytest.fixture()
def mock_exec_client():
    with patch("shared.workflow_launcher._get_exec_client") as m:
        mock_client = MagicMock()
        m.return_value = mock_client
        yield mock_client


# ---------------------------------------------------------------------------
# get_workflow_parent
# ---------------------------------------------------------------------------

class TestGetWorkflowParent:
    def test_builds_from_env(self):
        from shared.workflow_launcher import get_workflow_parent

        result = get_workflow_parent()
        assert result == "projects/test-project/locations/us-central1/workflows/test-workflow"


# ---------------------------------------------------------------------------
# build_payload
# ---------------------------------------------------------------------------

class TestBuildPayload:
    def test_basic_payload(self):
        from shared.workflow_launcher import build_payload

        result = build_payload(
            api_source="sp_api",
            client_id="c1",
            marketplace="US",
            report_type="GET_FLAT_FILE_OPEN_LISTINGS_DATA",
            report_params={"dataStartTime": "2026-03-19T08:00:00Z"},
            job_id="job-1",
            frequency="daily",
        )
        assert result["api_source"] == "sp_api"
        assert result["client_id"] == "c1"
        assert result["job_id"] == "job-1"
        assert result["folder_name"] == ""
        assert result["subfolder_strategy"] == "date"
        assert "schedule_id" not in result

    def test_includes_schedule_id_when_provided(self):
        from shared.workflow_launcher import build_payload

        result = build_payload(
            api_source="sp_api",
            client_id="c1",
            marketplace="US",
            report_type="X",
            report_params={},
            job_id="job-1",
            frequency="daily",
            schedule_id="sched-1",
        )
        assert result["schedule_id"] == "sched-1"

    def test_custom_folder_settings(self):
        from shared.workflow_launcher import build_payload

        result = build_payload(
            api_source="ads_api",
            client_id="c1",
            marketplace="UK",
            report_type="SP_TRAFFIC",
            report_params={},
            job_id="job-2",
            frequency="weekly",
            folder_name="Weekly Reports",
            subfolder_strategy="none",
        )
        assert result["folder_name"] == "Weekly Reports"
        assert result["subfolder_strategy"] == "none"


# ---------------------------------------------------------------------------
# launch_execution — retry behavior
# ---------------------------------------------------------------------------

class TestLaunchExecution:
    def test_success_on_first_attempt(self, mock_exec_client):
        from shared.workflow_launcher import launch_execution

        mock_exec_client.create_execution.return_value = MagicMock(name="exec-1")
        result = launch_execution("parent", {"key": "val"}, "job-1")
        assert result is not None
        mock_exec_client.create_execution.assert_called_once()

    def test_retries_on_failure(self, mock_exec_client):
        from shared.workflow_launcher import launch_execution

        mock_exec_client.create_execution.side_effect = [
            RuntimeError("transient"),
            MagicMock(name="exec-1"),
        ]
        with patch("shared.workflow_launcher.time.sleep"):
            result = launch_execution("parent", {"key": "val"}, "job-1")
        assert result is not None
        assert mock_exec_client.create_execution.call_count == 2

    def test_exhausted_retries_marks_job_failed(self, mock_exec_client):
        from shared.workflow_launcher import launch_execution

        mock_exec_client.create_execution.side_effect = RuntimeError("persistent")

        with (
            patch("shared.workflow_launcher.time.sleep"),
            patch("shared.workflow_launcher.update_job_status") as mock_fail,
            pytest.raises(RuntimeError, match="persistent"),
        ):
            launch_execution("parent", {"key": "val"}, "job-1", retries=3)

        mock_fail.assert_called_once()
        assert mock_fail.call_args[0][1] == "failed"
        assert mock_exec_client.create_execution.call_count == 3


# ---------------------------------------------------------------------------
# launch_for_marketplace — fan-out with reconciliation
# ---------------------------------------------------------------------------

class TestLaunchForMarketplace:
    def _make_schedule(self, **overrides) -> dict:
        sched = {
            "id": "s1",
            "api_source": "sp_api",
            "report_type": "GET_SALES_AND_TRAFFIC_REPORT",
            "frequency": "daily",
            "report_params": {},
            "folder_name": "",
            "subfolder_strategy": "date",
            "reconciliation_days": [],
        }
        sched.update(overrides)
        return sched

    def test_primary_only(self, mock_exec_client):
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(reconciliation_days=[])
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", return_value="job-1"):
            ids = launch_for_marketplace("parent", now, sched, "c1", "US")

        assert ids == ["job-1"]
        mock_exec_client.create_execution.assert_called_once()
        payload = json.loads(mock_exec_client.create_execution.call_args.kwargs["execution"].argument)
        assert payload["client_id"] == "c1"
        assert payload["marketplace"] == "US"
        assert payload["schedule_id"] == "s1"

    def test_with_reconciliation(self, mock_exec_client):
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(reconciliation_days=[3, 7])
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", side_effect=["j1", "j2", "j3"]):
            ids = launch_for_marketplace("parent", now, sched, "c1", "US")

        assert len(ids) == 3
        assert mock_exec_client.create_execution.call_count == 3

    def test_extra_job_fields_applied(self, mock_exec_client):
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(reconciliation_days=[])
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", return_value="job-1") as mock_create:
            launch_for_marketplace(
                "parent", now, sched, "c1", "US",
                extra_job_fields={"trigger": "manual"},
            )

        job_data = mock_create.call_args[0][0]
        assert job_data["trigger"] == "manual"

    def test_folder_name_in_payload(self, mock_exec_client):
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(folder_name="Custom Reports", subfolder_strategy="none", reconciliation_days=[])
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", return_value="job-1"):
            launch_for_marketplace("parent", now, sched, "c1", "US")

        payload = json.loads(mock_exec_client.create_execution.call_args.kwargs["execution"].argument)
        assert payload["folder_name"] == "Custom Reports"
        assert payload["subfolder_strategy"] == "none"

    def test_launch_failure_propagates(self, mock_exec_client):
        from shared.workflow_launcher import launch_for_marketplace

        mock_exec_client.create_execution.side_effect = RuntimeError("boom")
        sched = self._make_schedule(reconciliation_days=[])
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with (
            patch("shared.workflow_launcher.create_job", return_value="job-1"),
            patch("shared.workflow_launcher.update_job_status"),
            patch("shared.workflow_launcher.time.sleep"),
            pytest.raises(RuntimeError, match="boom"),
        ):
            launch_for_marketplace("parent", now, sched, "c1", "US")


# ---------------------------------------------------------------------------
# Timeframe-aware launch
# ---------------------------------------------------------------------------

class TestLaunchForMarketplaceTimeframe:
    def _make_schedule(self, **overrides) -> dict:
        sched = {
            "id": "s1",
            "api_source": "sp_api",
            "report_type": "GET_SALES_AND_TRAFFIC_REPORT",
            "frequency": "daily",
            "report_params": {},
            "folder_name": "",
            "subfolder_strategy": "date",
            "reconciliation_days": [3, 7],
        }
        sched.update(overrides)
        return sched

    def test_yesterday_strategy_includes_reconciliation(self, mock_exec_client):
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(
            timeframe={"strategy": "yesterday"},
            reconciliation_days=[3, 7],
        )
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", side_effect=["j1", "j2", "j3"]):
            ids = launch_for_marketplace("parent", now, sched, "c1", "US")

        assert len(ids) == 3

    def test_last_n_days_skips_reconciliation(self, mock_exec_client):
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(
            timeframe={"strategy": "last_n_days", "days": 30},
            reconciliation_days=[3, 7],
        )
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", return_value="j1"):
            ids = launch_for_marketplace("parent", now, sched, "c1", "US")

        assert len(ids) == 1  # no reconciliation

    def test_last_calendar_month_skips_reconciliation(self, mock_exec_client):
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(
            timeframe={"strategy": "last_calendar_month"},
            reconciliation_days=[3, 7],
        )
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", return_value="j1"):
            ids = launch_for_marketplace("parent", now, sched, "c1", "US")

        assert len(ids) == 1

    def test_last_calendar_week_skips_reconciliation(self, mock_exec_client):
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(
            timeframe={"strategy": "last_calendar_week", "week_start": 3},
            reconciliation_days=[3, 7],
        )
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", return_value="j1"):
            ids = launch_for_marketplace("parent", now, sched, "c1", "US")

        assert len(ids) == 1

    def test_no_timeframe_defaults_to_yesterday(self, mock_exec_client):
        """Schedules without a timeframe field should behave exactly like yesterday."""
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(reconciliation_days=[3])
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", side_effect=["j1", "j2"]):
            ids = launch_for_marketplace("parent", now, sched, "c1", "US")

        assert len(ids) == 2  # primary + T-3

    def test_range_report_params_passed(self, mock_exec_client):
        """Verify the payload contains correct date range params for a multi-day strategy."""
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(
            timeframe={"strategy": "last_n_days", "days": 7},
            reconciliation_days=[3, 7],
        )
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", return_value="j1"):
            launch_for_marketplace("parent", now, sched, "c1", "US")

        payload = json.loads(mock_exec_client.create_execution.call_args.kwargs["execution"].argument)
        assert "dataStartTime" in payload["report_params"]
        assert "dataEndTime" in payload["report_params"]

    def test_range_job_has_end_date(self, mock_exec_client):
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(
            timeframe={"strategy": "last_n_days", "days": 7},
        )
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", return_value="j1") as mock_create:
            launch_for_marketplace("parent", now, sched, "c1", "US")

        job_data = mock_create.call_args[0][0]
        assert "report_end_date" in job_data

    def test_yesterday_job_has_no_end_date(self, mock_exec_client):
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(
            timeframe={"strategy": "yesterday"},
            reconciliation_days=[],
        )
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", return_value="j1") as mock_create:
            launch_for_marketplace("parent", now, sched, "c1", "US")

        job_data = mock_create.call_args[0][0]
        assert "report_end_date" not in job_data
