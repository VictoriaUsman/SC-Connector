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

    def test_create_client_invalid_id_rejected(self, client):
        with patch("api.main.upsert_client") as mock:
            resp = client.post(
                "/clients", json={"id": "the-home-&-office", "name": "The Home & Office"}
            )
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "INVALID_CLIENT_ID"
        mock.assert_not_called()

    def test_create_client_uppercase_id_rejected(self, client):
        with patch("api.main.upsert_client") as mock:
            resp = client.post("/clients", json={"id": "Acme Corp", "name": "Acme Corp"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "INVALID_CLIENT_ID"
        mock.assert_not_called()

    def test_create_client_valid_slug_accepted(self, client):
        with patch("api.main.upsert_client") as mock:
            resp = client.post(
                "/clients", json={"id": "the-home-office", "name": "The Home & Office"}
            )
        assert resp.status_code == 201
        assert resp.get_json()["id"] == "the-home-office"
        mock.assert_called_once()

    def test_connect_rejects_both(self, client):
        with patch("api.main.get_client", return_value={"id": "c1", "name": "Acme"}):
            resp = client.post("/clients/c1/connect", json={"api_source": "both"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "INVALID_REQUEST"

    def test_connect_sp_api_invalid_client_id(self, client):
        with patch(
            "api.main.get_client", return_value={"id": "the-home-&-office", "name": "Home"}
        ):
            resp = client.post(
                "/clients/the-home-&-office/connect",
                json={"api_source": "sp_api", "refresh_token": "Atzr|token"},
            )
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "INVALID_CLIENT_ID"

    def test_connect_sp_api_valid_client_id(self, client):
        with (
            patch("api.main.get_client", return_value={"id": "acme", "name": "Acme"}),
            patch("api.main._store_client_secret", return_value="kalilos-staging-sp-api-acme"),
            patch("api.main.upsert_client") as mock_upsert,
        ):
            resp = client.post(
                "/clients/acme/connect",
                json={"api_source": "sp_api", "refresh_token": "Atzr|token"},
            )
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "connected"
        mock_upsert.assert_called_once()

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


class TestSpApiToken:
    def test_returns_token_when_connected(self, client):
        with (
            patch("api.main._API_KEY", "test-key"),
            patch(
                "api.main.get_client",
                return_value={"id": "acme", "sp_api_secret_name": "kalilos-staging-sp-api-acme"},
            ),
            patch("api.main._read_client_secret", return_value={"refresh_token": "Atzr|token"}),
        ):
            resp = client.get("/clients/acme/sp-api-token", headers={"X-API-Key": "test-key"})
        assert resp.status_code == 200
        assert resp.get_json()["refresh_token"] == "Atzr|token"

    def test_not_connected(self, client):
        with (
            patch("api.main._API_KEY", "test-key"),
            patch("api.main.get_client", return_value={"id": "acme", "name": "Acme"}),
        ):
            resp = client.get("/clients/acme/sp-api-token", headers={"X-API-Key": "test-key"})
        assert resp.status_code == 404
        assert resp.get_json()["code"] == "INVALID_REQUEST"

    def test_client_not_found(self, client):
        with (
            patch("api.main._API_KEY", "test-key"),
            patch("api.main.get_client", return_value=None),
        ):
            resp = client.get("/clients/missing/sp-api-token", headers={"X-API-Key": "test-key"})
        assert resp.status_code == 404
        assert resp.get_json()["code"] == "NOT_FOUND"

    def test_missing_api_key(self, client):
        with patch("api.main._API_KEY", "test-key"):
            resp = client.get("/clients/acme/sp-api-token")
        assert resp.status_code == 401
        assert resp.get_json()["code"] == "UNAUTHORIZED"

    def test_invalid_api_key(self, client):
        with patch("api.main._API_KEY", "test-key"):
            resp = client.get("/clients/acme/sp-api-token", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401
        assert resp.get_json()["code"] == "UNAUTHORIZED"


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
                "report_types": ["GET_FLAT_FILE_OPEN_LISTINGS_DATA"],
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
            "client_id": "c1", "api_source": "bad", "report_types": ["X"],
            "marketplace": "US", "frequency": "daily",
        })
        assert resp.status_code == 400
        assert "api_source" in resp.get_json()["error"]

    def test_create_schedule_invalid_frequency(self, client):
        resp = client.post("/schedules", json={
            "client_id": "c1", "api_source": "sp_api", "report_types": ["X"],
            "marketplace": "US", "frequency": "biweekly",
        })
        assert resp.status_code == 400
        assert "frequency" in resp.get_json()["error"]

    def test_create_schedule_client_not_found(self, client):
        with patch("api.main.get_client", return_value=None):
            resp = client.post("/schedules", json={
                "client_id": "gone", "api_source": "sp_api", "report_types": ["X"],
                "marketplace": "US", "frequency": "daily",
            })
        assert resp.status_code == 404

    def test_update_schedule_recomputes_next_run(self, client):
        existing = {
            "id": "s1",
            "frequency": "daily",
            "is_active": True,
            "schedule_config": {"type": "daily", "time": "03:00"},
        }
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
        mock.assert_called_once_with(client_id="c1", status="completed", schedule_id=None, execution_date=None, limit=10)

    def test_list_jobs_limit_capped(self, client):
        with patch("api.main.list_jobs", return_value=[]) as mock:
            client.get("/jobs?limit=999")
        mock.assert_called_once_with(client_id=None, status=None, schedule_id=None, execution_date=None, limit=200)

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
                "report_types": ["GET_FLAT_FILE_OPEN_LISTINGS_DATA"],
                "start_date": "2026-03-20",
                "end_date": "2026-03-20",
            })

        assert resp.status_code == 201
        body = resp.get_json()
        assert body["jobs_started"] == 1
        assert body["job_ids"] == ["job-123"]
        assert body["status"] == "started"

    def test_on_demand_multiple_report_types(self, client):
        mock_execution = MagicMock()
        mock_execution.name = "projects/p/locations/l/workflows/w/executions/e1"

        with (
            patch("api.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("api.main.create_job", side_effect=["job-1", "job-2"]),
            patch("api.main.launch_execution", return_value=mock_execution),
        ):
            resp = client.post("/on-demand", json={
                "client_id": "c1",
                "api_source": "both",
                "marketplace": "US",
                "report_types": ["GET_SALES_AND_TRAFFIC_REPORT", "spCampaigns"],
                "report_params": {"spCampaigns": {"timeUnit": "DAILY"}},
                "start_date": "2026-03-20",
                "end_date": "2026-03-21",
            })

        assert resp.status_code == 201
        body = resp.get_json()
        assert body["jobs_started"] == 2

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
                "report_types": ["GET_FLAT_FILE_OPEN_LISTINGS_DATA"],
                "start_date": "2026-03-20",
                "end_date": "2026-03-20",
            })

        assert resp.status_code == 201
        body = resp.get_json()
        assert body["jobs_started"] == 0
        assert body["errors"] == 1

    def test_on_demand_missing_fields(self, client):
        resp = client.post("/on-demand", json={"client_id": "c1"})
        assert resp.status_code == 400

    def test_on_demand_invalid_api_source(self, client):
        resp = client.post("/on-demand", json={
            "client_id": "c1", "api_source": "bad", "marketplace": "US",
            "report_types": ["X"],
        })
        assert resp.status_code == 400

    def test_on_demand_inactive_client(self, client):
        with patch("api.main.get_client", return_value={"id": "c1", "is_active": False}):
            resp = client.post("/on-demand", json={
                "client_id": "c1", "api_source": "sp_api", "marketplace": "US",
                "report_types": ["X"],
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
            "report_types": ["GET_FLAT_FILE_OPEN_LISTINGS_DATA"],
            "marketplaces": ["US"],
            "frequency": "daily",
            "report_params": {},
            "reconciliation_days": [],
        }

        with (
            patch("api.main.get_schedule", return_value=fake_schedule),
            patch("api.main.get_client", return_value={"id": "c1", "is_active": True}),
            patch("api.main.client_has_credentials", return_value=True),
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
            "report_types": ["X"],
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
                "report_types": ["GET_FLAT_FILE_OPEN_LISTINGS_DATA"],
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
                "report_types": ["X"],
                "marketplace": "US",
                "frequency": "daily",
                "timeframe": {"strategy": "last_n_days", "days": 30, "end_offset_days": 3},
            })
        assert resp.status_code == 201

    def test_create_schedule_invalid_strategy(self, client):
        resp = client.post("/schedules", json={
            "client_id": "c1",
            "api_source": "sp_api",
            "report_types": ["X"],
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
            "report_types": ["X"],
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
            "report_types": ["X"],
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
            "report_types": ["X"],
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
            "report_types": ["X"],
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
            "report_types": ["X"],
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
            "report_types": ["X"],
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
                "report_types": ["X"],
                "marketplace": "US",
                "frequency": "daily",
            })
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Ads profiles — multi-region discovery
# ---------------------------------------------------------------------------

def _mock_token_post(*_args, **_kwargs):
    """Mock the LWA refresh-token exchange used before profile discovery."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"access_token": "tok-123"}
    return resp


class TestAdsProfiles:
    _APP_CREDS = {
        "refresh_token": "rt",
        "client_id": "amzn1.app",
        "client_secret": "cs",
    }

    def _region_get(self, profiles_by_host, failing_hosts=()):
        """Build a requests.get side_effect that returns per-host profiles."""
        def _get(url, *_args, **_kwargs):
            resp = MagicMock()
            for host, profiles in profiles_by_host.items():
                if host in url:
                    if host in failing_hosts:
                        resp.raise_for_status.side_effect = RuntimeError("region down")
                        resp.json.return_value = []
                    else:
                        resp.raise_for_status.return_value = None
                        resp.json.return_value = profiles
                    return resp
            resp.raise_for_status.return_value = None
            resp.json.return_value = []
            return resp
        return _get

    def test_lists_profiles_across_all_regions(self, client):
        profiles_by_host = {
            "advertising-api.amazon.com": [{"profileId": 1, "countryCode": "US"}],
            "advertising-api-eu.amazon.com": [{"profileId": 2, "countryCode": "UK"}],
            "advertising-api-fe.amazon.com": [{"profileId": 3, "countryCode": "AU"}],
        }
        with (
            patch("api.main._read_app_secret", return_value=self._APP_CREDS),
            patch("api.main.requests.post", side_effect=_mock_token_post),
            patch("api.main.requests.get", side_effect=self._region_get(profiles_by_host)),
            patch("api.main.list_clients", return_value=[]),
        ):
            resp = client.get("/ads-profiles")

        assert resp.status_code == 200
        data = resp.get_json()
        by_id = {p["profileId"]: p for p in data}
        assert set(by_id) == {1, 2, 3}
        assert by_id[1]["_region"] == "na"
        assert by_id[2]["_region"] == "eu"
        # AU profile (item from the plan) now appears, annotated with FE region
        assert by_id[3]["_region"] == "fe"
        assert by_id[3]["countryCode"] == "AU"

    def test_tolerates_per_region_failure(self, client):
        profiles_by_host = {
            "advertising-api.amazon.com": [{"profileId": 1, "countryCode": "US"}],
            "advertising-api-eu.amazon.com": [],
            "advertising-api-fe.amazon.com": [{"profileId": 3, "countryCode": "AU"}],
        }
        # EU host errors; NA + FE should still come back.
        side_effect = self._region_get(
            profiles_by_host, failing_hosts={"advertising-api-eu.amazon.com"}
        )
        with (
            patch("api.main._read_app_secret", return_value=self._APP_CREDS),
            patch("api.main.requests.post", side_effect=_mock_token_post),
            patch("api.main.requests.get", side_effect=side_effect),
            patch("api.main.list_clients", return_value=[]),
        ):
            resp = client.get("/ads-profiles")

        assert resp.status_code == 200
        regions = {p["profileId"]: p["_region"] for p in resp.get_json()}
        assert regions == {1: "na", 3: "fe"}

    def test_dedupes_profile_across_regions(self, client):
        # Same profileId returned by two hosts — first (na) wins.
        profiles_by_host = {
            "advertising-api.amazon.com": [{"profileId": 7, "countryCode": "US"}],
            "advertising-api-eu.amazon.com": [{"profileId": 7, "countryCode": "US"}],
            "advertising-api-fe.amazon.com": [],
        }
        with (
            patch("api.main._read_app_secret", return_value=self._APP_CREDS),
            patch("api.main.requests.post", side_effect=_mock_token_post),
            patch("api.main.requests.get", side_effect=self._region_get(profiles_by_host)),
            patch("api.main.list_clients", return_value=[]),
        ):
            resp = client.get("/ads-profiles")

        data = resp.get_json()
        assert len([p for p in data if p["profileId"] == 7]) == 1
        assert data[0]["_region"] == "na"

    def test_cross_references_linked_client(self, client):
        profiles_by_host = {
            "advertising-api.amazon.com": [],
            "advertising-api-eu.amazon.com": [],
            "advertising-api-fe.amazon.com": [{"profileId": 3, "countryCode": "AU"}],
        }
        with (
            patch("api.main._read_app_secret", return_value=self._APP_CREDS),
            patch("api.main.requests.post", side_effect=_mock_token_post),
            patch("api.main.requests.get", side_effect=self._region_get(profiles_by_host)),
            patch("api.main.list_clients", return_value=[
                {"id": "skylight-frame-au", "ads_profile_id": "3"},
            ]),
        ):
            resp = client.get("/ads-profiles")

        data = resp.get_json()
        assert data[0]["_linked_client_id"] == "skylight-frame-au"
