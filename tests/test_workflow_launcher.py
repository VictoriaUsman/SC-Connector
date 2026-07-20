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
# client_has_credentials
# ---------------------------------------------------------------------------

class TestClientHasCredentials:
    def test_sp_api_requires_secret(self):
        from shared.workflow_launcher import client_has_credentials

        assert client_has_credentials({"sp_api_secret_name": "s"}, "sp_api")
        assert not client_has_credentials({}, "sp_api")

    def test_ads_api_accepts_single_profile(self):
        from shared.workflow_launcher import client_has_credentials

        assert client_has_credentials({"ads_profile_id": "111"}, "ads_api")
        assert not client_has_credentials({}, "ads_api")

    def test_ads_api_accepts_per_marketplace_map_only(self):
        """Multi-marketplace accounts may carry only the per-marketplace map."""
        from shared.workflow_launcher import client_has_credentials

        assert client_has_credentials({"ads_profile_ids": {"US": "111"}}, "ads_api")
        assert not client_has_credentials({"ads_profile_ids": {}}, "ads_api")

    def test_both_accepts_either_source(self):
        from shared.workflow_launcher import client_has_credentials

        assert client_has_credentials({"sp_api_secret_name": "s"}, "both")
        assert client_has_credentials({"ads_profile_ids": {"US": "1"}}, "both")
        assert not client_has_credentials({}, "both")


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
# get_report_types — plural + legacy singular support
# ---------------------------------------------------------------------------

class TestGetReportTypes:
    def test_plural_report_types(self):
        from shared.workflow_launcher import get_report_types

        assert get_report_types({"report_types": ["spCampaigns", "spTargeting"]}) == [
            "spCampaigns",
            "spTargeting",
        ]

    def test_legacy_singular_report_type(self):
        """A schedule persisted with only a singular ``report_type`` must still
        resolve to a one-element list — otherwise its run produces zero jobs."""
        from shared.workflow_launcher import get_report_types

        assert get_report_types({"report_type": "spCampaigns"}) == ["spCampaigns"]

    def test_plural_takes_precedence_over_singular(self):
        from shared.workflow_launcher import get_report_types

        assert get_report_types(
            {"report_types": ["spCampaigns"], "report_type": "ignored"}
        ) == ["spCampaigns"]

    def test_empty_returns_empty_list(self):
        from shared.workflow_launcher import get_report_types

        assert get_report_types({}) == []
        assert get_report_types({"report_types": []}) == []
        assert get_report_types({"report_type": ""}) == []


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
            "report_types": ["GET_SALES_AND_TRAFFIC_REPORT"],
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

    def test_legacy_singular_report_type_produces_job(self, mock_exec_client):
        """Regression: a schedule persisted with a legacy singular ``report_type``
        (no ``report_types`` array) must still fan out at least one job, instead
        of silently producing none ("No jobs found for this run")."""
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(reconciliation_days=[])
        del sched["report_types"]
        sched["report_type"] = "GET_SALES_AND_TRAFFIC_REPORT"
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", return_value="job-1"):
            ids = launch_for_marketplace("parent", now, sched, "c1", "US")

        assert ids == ["job-1"]
        mock_exec_client.create_execution.assert_called_once()
        payload = json.loads(mock_exec_client.create_execution.call_args.kwargs["execution"].argument)
        assert payload["report_type"] == "GET_SALES_AND_TRAFFIC_REPORT"

    def test_execution_date_in_payload(self, mock_exec_client):
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(reconciliation_days=[])
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", return_value="job-1") as mock_create:
            launch_for_marketplace("parent", now, sched, "c1", "US")

        payload = json.loads(mock_exec_client.create_execution.call_args.kwargs["execution"].argument)
        assert payload["execution_date"] == "2026-03-20"

        job_data = mock_create.call_args[0][0]
        assert job_data["execution_date"] == "2026-03-20"

    def test_execution_date_is_marketplace_today(self, mock_exec_client):
        """execution_date should be marketplace today, not the report data date."""
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(
            timeframe={"strategy": "last_calendar_month"},
            reconciliation_days=[],
        )
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", return_value="job-1"):
            launch_for_marketplace("parent", now, sched, "c1", "US")

        payload = json.loads(mock_exec_client.create_execution.call_args.kwargs["execution"].argument)
        assert payload["execution_date"] == "2026-03-20"
        assert "2026-02" in payload["report_params"]["dataStartTime"]

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
            "report_types": ["GET_SALES_AND_TRAFFIC_REPORT"],
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


# ---------------------------------------------------------------------------
# execution_date determinism — US and CA MUST get the same folder date
# ---------------------------------------------------------------------------

class TestExecutionDateDeterminism:
    """For every timeframe strategy, two concurrent marketplace launches
    (US and CA) from the same scheduler run must produce the EXACT same
    execution_date.  If they don't, they'll create separate date folders
    in Drive — the duplicate-folder bug.
    """

    _ALL_TIMEFRAMES = [
        {"strategy": "yesterday"},
        {"strategy": "today"},
        {"strategy": "last_n_days", "days": 30},
        {"strategy": "last_n_days", "days": 30, "end_offset_days": 3},
        {"strategy": "rolling_window", "start_offset": -7, "end_offset": -1},
        {"strategy": "last_calendar_week", "week_start": 0},
        {"strategy": "last_calendar_week", "week_start": 3},
        {"strategy": "last_calendar_month"},
    ]

    def _make_schedule(self, timeframe: dict) -> dict:
        return {
            "id": "s1",
            "api_source": "sp_api",
            "report_types": ["GET_SALES_AND_TRAFFIC_REPORT"],
            "frequency": "daily",
            "report_params": {},
            "folder_name": "",
            "subfolder_strategy": "date",
            "reconciliation_days": [],
            "timeframe": timeframe,
        }

    @pytest.mark.parametrize("timeframe", _ALL_TIMEFRAMES, ids=lambda tf: tf["strategy"])
    def test_same_execution_date_for_us_and_ca(self, timeframe, mock_exec_client):
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(timeframe)
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        job_counter = iter(range(100))
        with patch("shared.workflow_launcher.create_job", side_effect=lambda _: f"j{next(job_counter)}"):
            launch_for_marketplace("parent", now, sched, "c1", "US")
        us_payload = json.loads(mock_exec_client.create_execution.call_args.kwargs["execution"].argument)

        mock_exec_client.reset_mock()
        with patch("shared.workflow_launcher.create_job", side_effect=lambda _: f"j{next(job_counter)}"):
            launch_for_marketplace("parent", now, sched, "c1", "CA")
        ca_payload = json.loads(mock_exec_client.create_execution.call_args.kwargs["execution"].argument)

        assert us_payload["execution_date"] == ca_payload["execution_date"], (
            f"US got execution_date={us_payload['execution_date']} but "
            f"CA got execution_date={ca_payload['execution_date']} — "
            f"they would create separate Drive folders!"
        )

    @pytest.mark.parametrize("timeframe", _ALL_TIMEFRAMES, ids=lambda tf: tf["strategy"])
    def test_execution_date_is_today_not_report_date(self, timeframe, mock_exec_client):
        """execution_date must be marketplace today, never the report data date."""
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(timeframe)
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", return_value="j1"):
            launch_for_marketplace("parent", now, sched, "c1", "US")

        payload = json.loads(mock_exec_client.create_execution.call_args.kwargs["execution"].argument)
        assert payload["execution_date"] == "2026-03-20"

    @pytest.mark.parametrize("timeframe", _ALL_TIMEFRAMES, ids=lambda tf: tf["strategy"])
    def test_execution_date_in_job_data(self, timeframe, mock_exec_client):
        """execution_date must be stored in the Firestore job document too."""
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(timeframe)
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", return_value="j1") as mock_create:
            launch_for_marketplace("parent", now, sched, "c1", "US")

        job_data = mock_create.call_args[0][0]
        assert "execution_date" in job_data
        assert job_data["execution_date"] == "2026-03-20"


# ---------------------------------------------------------------------------
# launch_for_marketplace — Sales & Traffic end-date consistency
# ---------------------------------------------------------------------------

class TestLaunchForMarketplaceSalesTraffic:
    """Regression for the ~28-account incident: Sales & Traffic must pull
    through the same complete end date (D-2 from the operations run date) for
    every account, regardless of marketplace timezone or the hour the
    scheduler fires.
    """

    def _make_schedule(self, **overrides) -> dict:
        sched = {
            "id": "s1",
            "api_source": "sp_api",
            "report_types": ["GET_SALES_AND_TRAFFIC_REPORT"],
            "frequency": "daily",
            "report_params": {},
            "folder_name": "",
            "subfolder_strategy": "date",
            "reconciliation_days": [],
            "timeframe": {"strategy": "last_n_days", "days": 30, "end_offset_days": 0},
        }
        sched.update(overrides)
        return sched

    def _data_end_date(self, mock_exec_client) -> str:
        payload = json.loads(mock_exec_client.create_execution.call_args.kwargs["execution"].argument)
        # dataEndTime is midnight after the end day in the marketplace tz; its
        # date component can roll to the next day, so assert via report_date +
        # report_end_date which are plain marketplace calendar dates.
        return payload["report_params"]["dataEndTime"]

    def test_same_end_date_across_marketplaces_at_6am_pht(self, mock_exec_client):
        """US and DE must request the same end date for a 6am-PHT run."""
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule()
        now = datetime(2026, 6, 10, 22, 0, tzinfo=timezone.utc)  # 6am PHT Jun 11

        ends = {}
        for mkt in ["US", "DE"]:
            mock_exec_client.reset_mock()
            with patch("shared.workflow_launcher.create_job", return_value="j1") as mc:
                launch_for_marketplace("parent", now, sched, "c1", mkt)
            ends[mkt] = mc.call_args[0][0]["report_end_date"]

        assert ends["US"] == ends["DE"] == "2026-06-09", ends

    def test_other_report_types_remain_marketplace_local(self, mock_exec_client):
        """A non-S&T SP report keeps marketplace-local dates (DE differs from US)."""
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(report_types=["GET_FLAT_FILE_OPEN_LISTINGS_DATA"])
        now = datetime(2026, 6, 10, 22, 0, tzinfo=timezone.utc)

        ends = {}
        for mkt in ["US", "DE"]:
            mock_exec_client.reset_mock()
            with patch("shared.workflow_launcher.create_job", return_value="j1") as mc:
                launch_for_marketplace("parent", now, sched, "c1", mkt)
            ends[mkt] = mc.call_args[0][0]["report_end_date"]

        assert ends["US"] == "2026-06-09"
        assert ends["DE"] == "2026-06-10"


# ---------------------------------------------------------------------------
# launch_for_marketplace — resolved-window structured logging
# ---------------------------------------------------------------------------

class TestLaunchForMarketplaceWindowLog:
    """The launcher must emit one structured 'Resolved report pull window' log
    per report type so a short/inconsistent-window report is a single log query
    rather than a job-document reconstruction.
    """

    def _make_schedule(self, **overrides) -> dict:
        sched = {
            "id": "s1",
            "api_source": "sp_api",
            "report_types": ["GET_SALES_AND_TRAFFIC_REPORT"],
            "frequency": "daily",
            "report_params": {},
            "folder_name": "",
            "subfolder_strategy": "date",
            "reconciliation_days": [],
            "timeframe": {"strategy": "last_n_days", "days": 30, "end_offset_days": 0},
        }
        sched.update(overrides)
        return sched

    def _window_record(self, caplog):
        recs = [r for r in caplog.records if r.getMessage() == "Resolved report pull window"]
        assert len(recs) == 1, [r.getMessage() for r in caplog.records]
        return recs[0]

    def test_sales_traffic_last_n_days_log_is_ops_anchored(self, mock_exec_client, caplog):
        import logging
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule()
        now = datetime(2026, 6, 10, 22, 0, tzinfo=timezone.utc)  # 6am PHT Jun 11

        with caplog.at_level(logging.INFO, logger="shared.workflow_launcher"):
            with patch("shared.workflow_launcher.create_job", return_value="j1"):
                launch_for_marketplace("parent", now, sched, "c1", "US")

        rec = self._window_record(caplog)
        assert rec.report_type == "GET_SALES_AND_TRAFFIC_REPORT"
        assert rec.strategy == "last_n_days"
        assert rec.ops_anchored is True
        assert rec.window_end == "2026-06-09"
        assert rec.window_days == 30

    def test_br_ly_rolling_window_log_is_full_30_days_not_ops_anchored(self, mock_exec_client, caplog):
        """BR last-year rolling window: 30 inclusive days, no S&T lag/anchoring.

        Regression for the recurrence where the last-year comparison window came
        back 29 days ending one day early (rolling_window off-by-one + the S&T
        data lag wrongly applied to a year-old window).
        """
        import logging
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(
            timeframe={"strategy": "rolling_window", "start_offset": -364, "end_offset": -336},
        )
        now = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)

        with caplog.at_level(logging.INFO, logger="shared.workflow_launcher"):
            with patch("shared.workflow_launcher.create_job", return_value="j1"):
                launch_for_marketplace("parent", now, sched, "c1", "US")

        rec = self._window_record(caplog)
        assert rec.strategy == "rolling_window"
        assert rec.ops_anchored is False
        assert rec.window_start == "2025-07-20"
        assert rec.window_end == "2025-08-18"
        assert rec.window_days == 30


# ---------------------------------------------------------------------------
# launch_for_marketplace — synchronous API operations (mode="api_call")
# ---------------------------------------------------------------------------

class TestLaunchForMarketplaceApiOperations:
    def _make_schedule(self, **overrides) -> dict:
        sched = {
            "id": "s-sns",
            "api_source": "sp_api",
            "report_types": ["SNS_OFFER_METRICS"],
            "frequency": "daily",
            "report_params": {},
            "folder_name": "",
            "subfolder_strategy": "date",
            "reconciliation_days": [3, 7],
            "timeframe": {"strategy": "last_calendar_week"},
        }
        sched.update(overrides)
        return sched

    def test_api_operation_routes_to_api_call_mode(self, mock_exec_client):
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule()
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", return_value="job-sns") as mock_create:
            ids = launch_for_marketplace("parent", now, sched, "c1", "US")

        assert ids == ["job-sns"]
        payload = json.loads(mock_exec_client.create_execution.call_args.kwargs["execution"].argument)
        assert payload["mode"] == "api_call"
        assert payload["report_type"] == "SNS_OFFER_METRICS"
        assert payload["api_source"] == "sp_api"
        # Job doc is stamped with mode too.
        assert mock_create.call_args[0][0]["mode"] == "api_call"

    def test_api_operation_skips_reconciliation(self, mock_exec_client):
        """Even on the yesterday strategy, sync ops never fan out recon re-pulls."""
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(timeframe={"strategy": "yesterday"}, reconciliation_days=[3, 7])
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with patch("shared.workflow_launcher.create_job", return_value="job-sns"):
            ids = launch_for_marketplace("parent", now, sched, "c1", "US")

        assert ids == ["job-sns"]
        assert mock_exec_client.create_execution.call_count == 1


# ---------------------------------------------------------------------------
# launch_for_marketplace — idempotency / duplicate-pull guard
# ---------------------------------------------------------------------------

class TestLaunchForMarketplaceDedupe:
    """A single logical pull (schedule+execution_date+client+marketplace+
    report_type+date-range) must be launched at most once, even if the
    scheduler fires twice — the root of the duplicate-Drive-file bug.
    """

    def _make_schedule(self, **overrides) -> dict:
        sched = {
            "id": "s1",
            "api_source": "sp_api",
            "report_types": ["GET_FLAT_FILE_OPEN_LISTINGS_DATA"],
            "frequency": "daily",
            "report_params": {},
            "folder_name": "",
            "subfolder_strategy": "date",
            "reconciliation_days": [],
            "timeframe": {"strategy": "yesterday"},
        }
        sched.update(overrides)
        return sched

    def test_second_identical_run_is_fully_deduped(self, mock_exec_client):
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule()
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        seen: set[str] = set()

        def fake_claim(key, metadata=None):
            if key in seen:
                return False
            seen.add(key)
            return True

        with (
            patch("shared.workflow_launcher.try_claim_job_launch", side_effect=fake_claim),
            patch("shared.workflow_launcher.create_job", side_effect=lambda d: "j"),
        ):
            ids1 = launch_for_marketplace("parent", now, sched, "c1", "US")
            ids2 = launch_for_marketplace("parent", now, sched, "c1", "US")

        assert len(ids1) == 1
        assert ids2 == []  # duplicate run produced no new jobs
        assert mock_exec_client.create_execution.call_count == 1

    def test_dedupe_skips_job_creation_and_launch(self, mock_exec_client):
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule()
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        with (
            patch("shared.workflow_launcher.try_claim_job_launch", return_value=False),
            patch("shared.workflow_launcher.create_job") as mock_create,
        ):
            ids = launch_for_marketplace("parent", now, sched, "c1", "US")

        assert ids == []
        mock_create.assert_not_called()
        mock_exec_client.create_execution.assert_not_called()

    def test_reconciliation_pulls_have_distinct_keys(self, mock_exec_client):
        """Primary + T-3/T-7 differ by report_date, so none are wrongly deduped."""
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule(reconciliation_days=[3, 7])
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        keys: list[str] = []

        def capture(key, metadata=None):
            keys.append(key)
            return True

        with (
            patch("shared.workflow_launcher.try_claim_job_launch", side_effect=capture),
            patch("shared.workflow_launcher.create_job", side_effect=["j1", "j2", "j3"]),
        ):
            ids = launch_for_marketplace("parent", now, sched, "c1", "US")

        assert len(ids) == 3
        assert len(set(keys)) == 3

    def test_manual_trigger_not_deduped_against_scheduled(self, mock_exec_client):
        """A deliberate manual re-run uses a different trigger scope, so it is
        not suppressed by the scheduled run's dedupe key."""
        from shared.workflow_launcher import launch_for_marketplace

        sched = self._make_schedule()
        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)

        keys: list[str] = []

        def capture(key, metadata=None):
            keys.append(key)
            return True

        with (
            patch("shared.workflow_launcher.try_claim_job_launch", side_effect=capture),
            patch("shared.workflow_launcher.create_job", side_effect=lambda d: "j"),
        ):
            launch_for_marketplace("parent", now, sched, "c1", "US")
            launch_for_marketplace(
                "parent", now, sched, "c1", "US",
                extra_job_fields={"trigger": "manual"},
            )

        assert len(keys) == 2
        assert keys[0] != keys[1]
        assert keys[0].startswith("schedule|")
        assert keys[1].startswith("manual|")
