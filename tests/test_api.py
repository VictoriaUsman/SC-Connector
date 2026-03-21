"""Tests for the API function — routing, validation, CORS, and business logic."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# Ensure shared/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")
os.environ.setdefault("WORKFLOW_NAME", "test-workflow")
os.environ.setdefault("WORKFLOW_LOCATION", "us-central1")


@pytest.fixture(autouse=True)
def _mock_firestore():
    """Prevent real Firestore connections."""
    with patch("shared.firestore_utils.firestore.Client"):
        yield


@pytest.fixture()
def client():
    from api.main import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_root(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"

    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

class TestCORS:
    def test_preflight(self, client):
        resp = client.options("/clients")
        assert resp.status_code == 204
        assert resp.headers["Access-Control-Allow-Origin"] == "*"
        assert "POST" in resp.headers["Access-Control-Allow-Methods"]

    def test_cors_on_regular_response(self, client):
        with patch("api.main.list_clients", return_value=[]):
            resp = client.get("/clients")
        assert resp.headers["Access-Control-Allow-Origin"] == "*"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrors:
    def test_404(self, client):
        resp = client.get("/nonexistent")
        assert resp.status_code == 404
        assert resp.get_json()["code"] == "NOT_FOUND"

    def test_405(self, client):
        resp = client.patch("/clients")
        assert resp.status_code == 405
        assert resp.get_json()["code"] == "METHOD_NOT_ALLOWED"


# ---------------------------------------------------------------------------
# Clients CRUD
# ---------------------------------------------------------------------------

class TestClients:
    def test_list_clients(self, client):
        fake_clients = [
            {"id": "c1", "name": "Acme", "is_active": True, "created_at": datetime.now(timezone.utc)},
        ]
        with patch("api.main.list_clients", return_value=fake_clients):
            resp = client.get("/clients")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["name"] == "Acme"
        assert isinstance(data[0]["created_at"], str)  # serialized to ISO

    def test_list_clients_active_filter(self, client):
        with patch("api.main.list_clients", return_value=[]) as mock:
            client.get("/clients?active=true")
        mock.assert_called_once_with(active_only=True)

    def test_get_client(self, client):
        fake = {"id": "c1", "name": "Acme", "is_active": True}
        with patch("api.main.get_client", return_value=fake):
            resp = client.get("/clients/c1")
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "Acme"

    def test_get_client_not_found(self, client):
        with patch("api.main.get_client", return_value=None):
            resp = client.get("/clients/missing")
        assert resp.status_code == 404

    def test_create_client(self, client):
        with patch("api.main.upsert_client") as mock:
            resp = client.post("/clients", json={"id": "c1", "name": "Acme", "marketplaces": ["US"]})
        assert resp.status_code == 201
        assert resp.get_json()["id"] == "c1"
        mock.assert_called_once()
        call_data = mock.call_args[0][1]
        assert call_data["name"] == "Acme"
        assert "id" not in call_data  # id popped from body

    def test_create_client_missing_id(self, client):
        resp = client.post("/clients", json={"name": "Acme"})
        assert resp.status_code == 400
        assert "id" in resp.get_json()["error"].lower()

    def test_create_client_missing_name(self, client):
        resp = client.post("/clients", json={"id": "c1"})
        assert resp.status_code == 400
        assert "name" in resp.get_json()["error"].lower()

    def test_update_client(self, client):
        with (
            patch("api.main.get_client", return_value={"id": "c1", "name": "Old"}),
            patch("api.main.upsert_client") as mock,
        ):
            resp = client.put("/clients/c1", json={"name": "New"})
        assert resp.status_code == 200
        mock.assert_called_once_with("c1", {"name": "New"})

    def test_update_client_not_found(self, client):
        with patch("api.main.get_client", return_value=None):
            resp = client.put("/clients/missing", json={"name": "New"})
        assert resp.status_code == 404

    def test_delete_client(self, client):
        with patch("api.main.fs_delete_client") as mock:
            resp = client.delete("/clients/c1")
        assert resp.status_code == 200
        mock.assert_called_once_with("c1")


# ---------------------------------------------------------------------------
# Schedules CRUD
# ---------------------------------------------------------------------------

class TestSchedules:
    def test_list_schedules(self, client):
        with patch("api.main.list_schedules", return_value=[]):
            resp = client.get("/schedules")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_list_schedules_with_filters(self, client):
        with patch("api.main.list_schedules", return_value=[]) as mock:
            client.get("/schedules?client_id=c1&active=true")
        mock.assert_called_once_with(client_id="c1", active_only=True)

    def test_create_schedule(self, client):
        with (
            patch("api.main.get_client", return_value={"id": "c1", "name": "Acme"}),
            patch("api.main.create_schedule", return_value="sched-1"),
        ):
            resp = client.post("/schedules", json={
                "client_id": "c1",
                "api_source": "sp_api",
                "report_type": "GET_FLAT_FILE_OPEN_LISTINGS_DATA",
                "marketplace": "US",
                "frequency": "daily",
            })
        assert resp.status_code == 201
        assert resp.get_json()["id"] == "sched-1"

    def test_create_schedule_missing_fields(self, client):
        resp = client.post("/schedules", json={"client_id": "c1"})
        assert resp.status_code == 400
        assert "api_source" in resp.get_json()["error"]

    def test_create_schedule_invalid_api_source(self, client):
        resp = client.post("/schedules", json={
            "client_id": "c1", "api_source": "bad", "report_type": "X",
            "marketplace": "US", "frequency": "daily",
        })
        assert resp.status_code == 400
        assert "api_source" in resp.get_json()["error"]

    def test_create_schedule_invalid_frequency(self, client):
        resp = client.post("/schedules", json={
            "client_id": "c1", "api_source": "sp_api", "report_type": "X",
            "marketplace": "US", "frequency": "biweekly",
        })
        assert resp.status_code == 400
        assert "frequency" in resp.get_json()["error"]

    def test_create_schedule_client_not_found(self, client):
        with patch("api.main.get_client", return_value=None):
            resp = client.post("/schedules", json={
                "client_id": "gone", "api_source": "sp_api", "report_type": "X",
                "marketplace": "US", "frequency": "daily",
            })
        assert resp.status_code == 404

    def test_update_schedule_recomputes_next_run(self, client):
        existing = {"id": "s1", "frequency": "daily", "is_active": True}
        with (
            patch("api.main.get_schedule", return_value=existing),
            patch("api.main.update_schedule") as mock,
        ):
            resp = client.put("/schedules/s1", json={"frequency": "hourly"})
        assert resp.status_code == 200
        call_data = mock.call_args[0][1]
        assert "next_run_at" in call_data

    def test_delete_schedule(self, client):
        with patch("api.main.fs_delete_schedule") as mock:
            resp = client.delete("/schedules/s1")
        assert resp.status_code == 200
        mock.assert_called_once_with("s1")


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class TestJobs:
    def test_list_jobs(self, client):
        fake_jobs = [{"id": "j1", "status": "completed", "started_at": datetime.now(timezone.utc)}]
        with patch("api.main.list_jobs", return_value=fake_jobs):
            resp = client.get("/jobs")
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_list_jobs_with_filters(self, client):
        with patch("api.main.list_jobs", return_value=[]) as mock:
            client.get("/jobs?client_id=c1&status=completed&limit=10")
        mock.assert_called_once_with(client_id="c1", status="completed", limit=10)

    def test_list_jobs_limit_capped(self, client):
        with patch("api.main.list_jobs", return_value=[]) as mock:
            client.get("/jobs?limit=999")
        mock.assert_called_once_with(client_id=None, status=None, limit=200)

    def test_get_job(self, client):
        with patch("api.main.get_job", return_value={"id": "j1", "status": "polling"}):
            resp = client.get("/jobs/j1")
        assert resp.status_code == 200

    def test_get_job_not_found(self, client):
        with patch("api.main.get_job", return_value=None):
            resp = client.get("/jobs/missing")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# On-demand trigger
# ---------------------------------------------------------------------------

class TestOnDemand:
    def test_on_demand_success(self, client):
        mock_execution = MagicMock()
        mock_execution.name = "projects/p/locations/l/workflows/w/executions/e1"

        with (
            patch("api.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("api.main.create_job", return_value="job-123"),
            patch("api.main.launch_execution", return_value=mock_execution),
        ):
            resp = client.post("/on-demand", json={
                "client_id": "c1",
                "api_source": "sp_api",
                "marketplace": "US",
                "report_type": "GET_FLAT_FILE_OPEN_LISTINGS_DATA",
            })

        assert resp.status_code == 201
        body = resp.get_json()
        assert body["job_id"] == "job-123"
        assert body["status"] == "started"
        assert "execution_name" in body

    def test_on_demand_workflow_failure(self, client):
        with (
            patch("api.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("api.main.create_job", return_value="job-123"),
            patch("api.main.launch_execution", side_effect=RuntimeError("workflow down")),
        ):
            resp = client.post("/on-demand", json={
                "client_id": "c1",
                "api_source": "sp_api",
                "marketplace": "US",
                "report_type": "GET_FLAT_FILE_OPEN_LISTINGS_DATA",
            })

        assert resp.status_code == 502
        body = resp.get_json()
        assert body["code"] == "WORKFLOW_START_FAILED"
        assert body["job_id"] == "job-123"

    def test_on_demand_missing_fields(self, client):
        resp = client.post("/on-demand", json={"client_id": "c1"})
        assert resp.status_code == 400

    def test_on_demand_invalid_api_source(self, client):
        resp = client.post("/on-demand", json={
            "client_id": "c1", "api_source": "bad", "marketplace": "US", "report_type": "X",
        })
        assert resp.status_code == 400

    def test_on_demand_inactive_client(self, client):
        with patch("api.main.get_client", return_value={"id": "c1", "is_active": False}):
            resp = client.post("/on-demand", json={
                "client_id": "c1", "api_source": "sp_api", "marketplace": "US", "report_type": "X",
            })
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Schedule trigger (Run Now)
# ---------------------------------------------------------------------------

class TestTriggerSchedule:
    def test_trigger_success(self, client):
        fake_schedule = {
            "id": "s1",
            "client_ids": ["c1"],
            "api_source": "sp_api",
            "report_type": "GET_FLAT_FILE_OPEN_LISTINGS_DATA",
            "marketplaces": ["US"],
            "frequency": "daily",
            "report_params": {},
            "reconciliation_days": [],
        }

        with (
            patch("api.main.get_schedule", return_value=fake_schedule),
            patch("api.main.get_client", return_value={"id": "c1", "is_active": True}),
            patch("api.main.launch_for_marketplace", return_value=["job-1"]),
            patch("api.main.update_schedule"),
        ):
            resp = client.post("/schedules/s1/trigger")

        assert resp.status_code == 201
        body = resp.get_json()
        assert body["jobs_started"] == 1
        assert body["job_ids"] == ["job-1"]

    def test_trigger_not_found(self, client):
        with patch("api.main.get_schedule", return_value=None):
            resp = client.post("/schedules/missing/trigger")
        assert resp.status_code == 404

    def test_trigger_inactive_client(self, client):
        fake_schedule = {
            "id": "s1",
            "client_ids": ["c1"],
            "api_source": "sp_api",
            "report_type": "X",
            "marketplaces": ["US"],
        }

        with (
            patch("api.main.get_schedule", return_value=fake_schedule),
            patch("api.main.get_client", return_value={"id": "c1", "is_active": False}),
        ):
            resp = client.post("/schedules/s1/trigger")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Timeframe validation
# ---------------------------------------------------------------------------

class TestTimeframeValidation:

    def test_create_schedule_with_valid_yesterday(self, client):
        with (
            patch("api.main.get_client", return_value={"id": "c1", "name": "Acme"}),
            patch("api.main.create_schedule", return_value="sched-1"),
        ):
            resp = client.post("/schedules", json={
                "client_id": "c1",
                "api_source": "sp_api",
                "report_type": "GET_FLAT_FILE_OPEN_LISTINGS_DATA",
                "marketplace": "US",
                "frequency": "daily",
                "timeframe": {"strategy": "yesterday"},
            })
        assert resp.status_code == 201

    def test_create_schedule_with_valid_last_n_days(self, client):
        with (
            patch("api.main.get_client", return_value={"id": "c1", "name": "Acme"}),
            patch("api.main.create_schedule", return_value="sched-1"),
        ):
            resp = client.post("/schedules", json={
                "client_id": "c1",
                "api_source": "sp_api",
                "report_type": "X",
                "marketplace": "US",
                "frequency": "daily",
                "timeframe": {"strategy": "last_n_days", "days": 30, "end_offset_days": 3},
            })
        assert resp.status_code == 201

    def test_create_schedule_invalid_strategy(self, client):
        resp = client.post("/schedules", json={
            "client_id": "c1",
            "api_source": "sp_api",
            "report_type": "X",
            "marketplace": "US",
            "frequency": "daily",
            "timeframe": {"strategy": "next_year"},
        })
        assert resp.status_code == 400
        assert "strategy" in resp.get_json()["error"]

    def test_create_schedule_missing_strategy(self, client):
        resp = client.post("/schedules", json={
            "client_id": "c1",
            "api_source": "sp_api",
            "report_type": "X",
            "marketplace": "US",
            "frequency": "daily",
            "timeframe": {"days": 30},
        })
        assert resp.status_code == 400
        assert "strategy" in resp.get_json()["error"]

    def test_create_schedule_last_n_days_missing_days(self, client):
        resp = client.post("/schedules", json={
            "client_id": "c1",
            "api_source": "sp_api",
            "report_type": "X",
            "marketplace": "US",
            "frequency": "daily",
            "timeframe": {"strategy": "last_n_days"},
        })
        assert resp.status_code == 400
        assert "days" in resp.get_json()["error"]

    def test_create_schedule_last_n_days_exceeds_max(self, client):
        resp = client.post("/schedules", json={
            "client_id": "c1",
            "api_source": "sp_api",
            "report_type": "X",
            "marketplace": "US",
            "frequency": "daily",
            "timeframe": {"strategy": "last_n_days", "days": 500},
        })
        assert resp.status_code == 400
        assert "365" in resp.get_json()["error"]

    def test_create_schedule_rolling_window_missing_offsets(self, client):
        resp = client.post("/schedules", json={
            "client_id": "c1",
            "api_source": "sp_api",
            "report_type": "X",
            "marketplace": "US",
            "frequency": "daily",
            "timeframe": {"strategy": "rolling_window"},
        })
        assert resp.status_code == 400
        assert "start_offset" in resp.get_json()["error"]

    def test_create_schedule_rolling_window_start_after_end(self, client):
        resp = client.post("/schedules", json={
            "client_id": "c1",
            "api_source": "sp_api",
            "report_type": "X",
            "marketplace": "US",
            "frequency": "daily",
            "timeframe": {"strategy": "rolling_window", "start_offset": -1, "end_offset": -5},
        })
        assert resp.status_code == 400
        assert "start_offset" in resp.get_json()["error"]

    def test_create_schedule_calendar_week_invalid_day(self, client):
        resp = client.post("/schedules", json={
            "client_id": "c1",
            "api_source": "sp_api",
            "report_type": "X",
            "marketplace": "US",
            "frequency": "daily",
            "timeframe": {"strategy": "last_calendar_week", "week_start": 9},
        })
        assert resp.status_code == 400
        assert "week_start" in resp.get_json()["error"]

    def test_update_schedule_with_valid_timeframe(self, client):
        existing = {"id": "s1", "frequency": "daily", "is_active": True}
        with (
            patch("api.main.get_schedule", return_value=existing),
            patch("api.main.update_schedule"),
        ):
            resp = client.put("/schedules/s1", json={
                "timeframe": {"strategy": "last_calendar_month"},
            })
        assert resp.status_code == 200

    def test_update_schedule_with_invalid_timeframe(self, client):
        existing = {"id": "s1", "frequency": "daily", "is_active": True}
        with patch("api.main.get_schedule", return_value=existing):
            resp = client.put("/schedules/s1", json={
                "timeframe": {"strategy": "bad"},
            })
        assert resp.status_code == 400

    def test_create_schedule_without_timeframe_still_works(self, client):
        """Backward compat: omitting timeframe should not fail."""
        with (
            patch("api.main.get_client", return_value={"id": "c1", "name": "Acme"}),
            patch("api.main.create_schedule", return_value="sched-1"),
        ):
            resp = client.post("/schedules", json={
                "client_id": "c1",
                "api_source": "sp_api",
                "report_type": "X",
                "marketplace": "US",
                "frequency": "daily",
            })
        assert resp.status_code == 201
