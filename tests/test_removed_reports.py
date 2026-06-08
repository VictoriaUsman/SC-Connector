"""Tests for removed SP-API report-type handling.

Amazon permanently removed the FBA Subscribe & Save reports
(GET_FBA_SNS_PERFORMANCE_DATA / GET_FBA_SNS_FORECAST_DATA) from the SP-API on
2025-12-11.  Requests are accepted (HTTP 202) but the report is immediately
CANCELLED, surfacing a generic "no data" failure and burning createReport quota.

These tests pin the registry plus the two guards that reject removed report
types up front: the create_report function and the scheduler/launcher fan-out.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
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


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class TestRemovedReportRegistry:
    def test_sns_reports_are_removed(self):
        from shared.removed_reports import (
            FBA_SNS_FORECAST_REPORT,
            FBA_SNS_PERFORMANCE_REPORT,
            is_removed_report_type,
        )

        assert is_removed_report_type(FBA_SNS_PERFORMANCE_REPORT)
        assert is_removed_report_type(FBA_SNS_FORECAST_REPORT)

    def test_active_report_is_not_removed(self):
        from shared.removed_reports import is_removed_report_type

        assert not is_removed_report_type("GET_FLAT_FILE_OPEN_LISTINGS_DATA")
        assert not is_removed_report_type("GET_SALES_AND_TRAFFIC_REPORT")

    def test_reason_is_actionable(self):
        from shared.removed_reports import (
            FBA_SNS_PERFORMANCE_REPORT,
            removed_report_reason,
        )

        reason = removed_report_reason(FBA_SNS_PERFORMANCE_REPORT)
        assert reason is not None
        # References the removal date and the recommended replacement.
        assert "2025-12-11" in reason
        assert "Replenishment API" in reason

    def test_reason_none_for_active_report(self):
        from shared.removed_reports import removed_report_reason

        assert removed_report_reason("GET_SALES_AND_TRAFFIC_REPORT") is None


# ---------------------------------------------------------------------------
# create_report guard
# ---------------------------------------------------------------------------

class _FakeRequest:
    def __init__(self, payload: dict):
        self._payload = payload

    def get_json(self, silent: bool = False):
        return self._payload


class TestCreateReportGuard:
    def _payload(self, **overrides) -> dict:
        data = {
            "api_source": "sp_api",
            "client_id": "moxe",
            "marketplace": "US",
            "report_type": "GET_FBA_SNS_PERFORMANCE_DATA",
            "job_id": "job-1",
        }
        data.update(overrides)
        return data

    def test_removed_report_rejected_without_calling_amazon(self):
        from create_report import main as cr

        with (
            patch.object(cr, "update_job_status") as mock_status,
            patch.object(cr, "get_sp_credentials") as mock_creds,
            patch.object(cr, "sp_api_client") as mock_sp,
        ):
            body, code = cr.handler(_FakeRequest(self._payload()))

        assert code == 422
        assert body["code"] == "REPORT_REMOVED"
        assert "2025-12-11" in body["error"]
        # Amazon must NOT be contacted for a removed report.
        mock_creds.assert_not_called()
        mock_sp.create_report.assert_not_called()
        # Job is marked failed with the documented reason (after "requesting").
        assert mock_status.call_args_list[-1].args[0] == "job-1"
        assert mock_status.call_args_list[-1].args[1] == "failed"
        err = mock_status.call_args_list[-1].kwargs["error_details"]
        assert err["code"] == "REPORT_REMOVED"
        assert err["phase"] == "create_report"

    def test_active_report_not_blocked(self):
        from create_report import main as cr

        with (
            patch.object(cr, "update_job_status"),
            patch.object(cr, "get_sp_credentials", return_value={}),
            patch.object(cr, "sp_api_client") as mock_sp,
        ):
            mock_sp.create_report.return_value = "report-123"
            body, code = cr.handler(
                _FakeRequest(self._payload(report_type="GET_FLAT_FILE_OPEN_LISTINGS_DATA"))
            )

        assert code == 200
        assert body["report_id"] == "report-123"
        mock_sp.create_report.assert_called_once()


# ---------------------------------------------------------------------------
# launcher skip
# ---------------------------------------------------------------------------

class TestLauncherSkipsRemovedReports:
    def _make_schedule(self, **overrides) -> dict:
        sched = {
            "id": "s1",
            "api_source": "sp_api",
            "report_types": ["GET_FBA_SNS_PERFORMANCE_DATA"],
            "frequency": "daily",
            "report_params": {},
            "folder_name": "",
            "subfolder_strategy": "date",
            "reconciliation_days": [3, 7],
            "timeframe": {"strategy": "yesterday"},
        }
        sched.update(overrides)
        return sched

    def test_removed_report_records_failed_job_and_no_workflow(self):
        from shared import workflow_launcher as wl

        sched = self._make_schedule()
        now = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)

        exec_client = MagicMock()
        with (
            patch.object(wl, "_get_exec_client", return_value=exec_client),
            patch.object(wl, "create_job", return_value="job-1"),
            patch.object(wl, "update_job_status") as mock_status,
        ):
            ids = wl.launch_for_marketplace("parent", now, sched, "moxe", "US")

        # A failed job is recorded, but NO workflow execution is launched and
        # no reconciliation re-pulls are created.
        assert ids == ["job-1"]
        exec_client.create_execution.assert_not_called()
        mock_status.assert_called_once()
        assert mock_status.call_args.args[0] == "job-1"
        assert mock_status.call_args.args[1] == "failed"
        err = mock_status.call_args.kwargs["error_details"]
        assert err["code"] == "REPORT_REMOVED"

    def test_failed_job_carries_extra_fields(self):
        from shared import workflow_launcher as wl

        sched = self._make_schedule()
        now = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)

        exec_client = MagicMock()
        with (
            patch.object(wl, "_get_exec_client", return_value=exec_client),
            patch.object(wl, "create_job", return_value="job-1") as mock_create,
            patch.object(wl, "update_job_status"),
        ):
            wl.launch_for_marketplace(
                "parent", now, sched, "moxe", "US",
                extra_job_fields={"trigger": "manual"},
            )

        job_data = mock_create.call_args.args[0]
        assert job_data["trigger"] == "manual"
        assert job_data["report_type"] == "GET_FBA_SNS_PERFORMANCE_DATA"

    def test_active_report_still_launches(self):
        from shared import workflow_launcher as wl

        sched = self._make_schedule(
            report_types=["GET_SALES_AND_TRAFFIC_REPORT"],
            reconciliation_days=[],
        )
        now = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)

        exec_client = MagicMock()
        with (
            patch.object(wl, "_get_exec_client", return_value=exec_client),
            patch.object(wl, "create_job", return_value="job-1"),
            patch.object(wl, "update_job_status"),
        ):
            ids = wl.launch_for_marketplace("parent", now, sched, "moxe", "US")

        assert ids == ["job-1"]
        exec_client.create_execution.assert_called_once()

    def test_mixed_report_types_only_removed_skipped(self):
        from shared import workflow_launcher as wl

        sched = self._make_schedule(
            report_types=["GET_SALES_AND_TRAFFIC_REPORT", "GET_FBA_SNS_PERFORMANCE_DATA"],
            reconciliation_days=[],
        )
        now = datetime(2026, 6, 8, 12, 0, tzinfo=timezone.utc)

        exec_client = MagicMock()
        with (
            patch.object(wl, "_get_exec_client", return_value=exec_client),
            patch.object(wl, "create_job", side_effect=["job-active", "job-removed"]),
            patch.object(wl, "update_job_status") as mock_status,
        ):
            ids = wl.launch_for_marketplace("parent", now, sched, "moxe", "US")

        # Both jobs created; only the active one launches a workflow.
        assert set(ids) == {"job-active", "job-removed"}
        exec_client.create_execution.assert_called_once()
        launched = json.loads(
            exec_client.create_execution.call_args.kwargs["execution"].argument
        )
        assert launched["report_type"] == "GET_SALES_AND_TRAFFIC_REPORT"
        # The removed report's job was the one marked failed.
        assert mock_status.call_args.args[0] == "job-removed"
        assert mock_status.call_args.args[1] == "failed"
