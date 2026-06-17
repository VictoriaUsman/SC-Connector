"""Tests for the Events and Bot Config API routes."""

from __future__ import annotations

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


@pytest.fixture()
def client():
    from api.main import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Events CRUD
# ---------------------------------------------------------------------------

class TestEventsAPI:
    def test_list_events(self, client):
        fake = [{"id": "e1", "name": "Prime Day", "status": "upcoming"}]
        with patch("api.main.list_events", return_value=fake):
            resp = client.get("/events")
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_get_event(self, client):
        fake = {"id": "e1", "name": "Prime Day", "status": "upcoming"}
        with patch("api.main.get_event", return_value=fake):
            resp = client.get("/events/e1")
        assert resp.status_code == 200
        assert resp.get_json()["name"] == "Prime Day"

    def test_get_event_not_found(self, client):
        with patch("api.main.get_event", return_value=None):
            resp = client.get("/events/missing")
        assert resp.status_code == 404

    def test_create_event(self, client):
        with patch("api.main.create_event", return_value="e1"):
            resp = client.post("/events", json={
                "name": "Prime Day 2026",
                "start_date": "2026-07-13",
                "end_date": "2026-07-14",
            })
        assert resp.status_code == 201
        assert resp.get_json()["id"] == "e1"

    def test_create_event_missing_name(self, client):
        resp = client.post("/events", json={
            "start_date": "2026-07-13", "end_date": "2026-07-14",
        })
        assert resp.status_code == 400
        assert "name" in resp.get_json()["error"].lower()

    def test_create_event_missing_dates(self, client):
        resp = client.post("/events", json={"name": "Test"})
        assert resp.status_code == 400

    def test_create_event_invalid_dates(self, client):
        resp = client.post("/events", json={
            "name": "Test", "start_date": "not-a-date", "end_date": "2026-07-14",
        })
        assert resp.status_code == 400

    def test_create_event_start_after_end(self, client):
        resp = client.post("/events", json={
            "name": "Test", "start_date": "2026-07-15", "end_date": "2026-07-14",
        })
        assert resp.status_code == 400

    def test_update_event(self, client):
        with (
            patch("api.main.get_event", return_value={"id": "e1", "name": "Old"}),
            patch("api.main.update_event") as mock,
        ):
            resp = client.put("/events/e1", json={"name": "New"})
        assert resp.status_code == 200
        mock.assert_called_once()

    # -- Manual prior-year ads (CU-868jx21hw follow-up) --------------------
    # Amazon Ads' reporting API only retains ~95 days, so a year-ago prior
    # event can't be pulled and recap YoY ads stayed 0. Operators provide the
    # figures by hand; the API validates and coerces them before persisting.

    _MANUAL_BASE = {
        "name": "Prime Day 2025",
        "start_date": "2025-07-13",
        "end_date": "2025-07-14",
    }

    def test_create_normalizes_manual_ads(self, client):
        payload = {
            **self._MANUAL_BASE,
            "manual_ads": {"US": {"2025-07-13": {"spend": "100.5", "ppc_sales": 400}}},
        }
        with patch("api.main.create_event", return_value="e1") as mock:
            resp = client.post("/events", json=payload)

        assert resp.status_code == 201
        saved = mock.call_args[0][0]
        assert saved["manual_ads"] == {
            "US": {"2025-07-13": {"spend": 100.5, "ppc_sales": 400.0}}
        }

    def test_create_rejects_bad_date(self, client):
        payload = {
            **self._MANUAL_BASE,
            "manual_ads": {"US": {"July 13": {"spend": 1, "ppc_sales": 2}}},
        }
        with patch("api.main.create_event") as mock:
            resp = client.post("/events", json=payload)

        assert resp.status_code == 400
        mock.assert_not_called()

    def test_create_rejects_non_numeric(self, client):
        payload = {
            **self._MANUAL_BASE,
            "manual_ads": {"US": {"2025-07-13": {"spend": "abc", "ppc_sales": 2}}},
        }
        with patch("api.main.create_event") as mock:
            resp = client.post("/events", json=payload)

        assert resp.status_code == 400
        mock.assert_not_called()

    def test_create_rejects_negative(self, client):
        payload = {
            **self._MANUAL_BASE,
            "manual_ads": {"US": {"2025-07-13": {"spend": -1, "ppc_sales": 2}}},
        }
        with patch("api.main.create_event") as mock:
            resp = client.post("/events", json=payload)

        assert resp.status_code == 400
        mock.assert_not_called()

    def test_update_normalizes_manual_ads(self, client):
        payload = {"manual_ads": {"US": {"2025-07-13": {"spend": 5, "ppc_sales": 9}}}}
        with (
            patch("api.main.get_event", return_value={"id": "e1", **self._MANUAL_BASE}),
            patch("api.main.update_event") as mock,
        ):
            resp = client.put("/events/e1", json=payload)

        assert resp.status_code == 200
        saved = mock.call_args[0][1]
        assert saved["manual_ads"] == {
            "US": {"2025-07-13": {"spend": 5.0, "ppc_sales": 9.0}}
        }

    def test_update_event_not_found(self, client):
        with patch("api.main.get_event", return_value=None):
            resp = client.put("/events/missing", json={"name": "New"})
        assert resp.status_code == 404

    def test_delete_event(self, client):
        with patch("api.main.fs_delete_event") as mock:
            resp = client.delete("/events/e1")
        assert resp.status_code == 200
        mock.assert_called_once_with("e1")


# ---------------------------------------------------------------------------
# Event activation/deactivation
# ---------------------------------------------------------------------------

class TestEventActivation:
    def test_activate_upcoming_event(self, client):
        with (
            patch("api.main.get_event", return_value={"id": "e1", "status": "upcoming"}),
            patch("api.main.update_event") as mock,
        ):
            resp = client.post("/events/e1/activate")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "live"
        update_data = mock.call_args[0][1]
        assert update_data["status"] == "live"
        assert update_data["manually_activated"] is True

    def test_activate_completed_event_fails(self, client):
        with patch("api.main.get_event", return_value={"id": "e1", "status": "completed"}):
            resp = client.post("/events/e1/activate")
        assert resp.status_code == 400

    def test_deactivate_live_event(self, client):
        with (
            patch("api.main.get_event", return_value={"id": "e1", "status": "live"}),
            patch("api.main.update_event") as mock,
        ):
            resp = client.post("/events/e1/deactivate")
        assert resp.status_code == 200
        assert mock.call_args[0][1]["status"] == "completed"

    def test_deactivate_non_live_event_fails(self, client):
        with patch("api.main.get_event", return_value={"id": "e1", "status": "upcoming"}):
            resp = client.post("/events/e1/deactivate")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Bot Configs
# ---------------------------------------------------------------------------

class TestBotConfigsAPI:
    def test_list_bot_configs(self, client):
        fake = [{"id": "c1", "client_id": "c1", "slack_channel_id": "C123"}]
        with patch("api.main.list_bot_configs", return_value=fake):
            resp = client.get("/bot-configs")
        assert resp.status_code == 200
        assert len(resp.get_json()) == 1

    def test_get_bot_config(self, client):
        fake = {"id": "c1", "client_id": "c1", "slack_channel_id": "C123"}
        with patch("api.main.get_bot_config", return_value=fake):
            resp = client.get("/bot-configs/c1")
        assert resp.status_code == 200

    def test_get_bot_config_not_found(self, client):
        with patch("api.main.get_bot_config", return_value=None):
            resp = client.get("/bot-configs/missing")
        assert resp.status_code == 404

    def test_upsert_bot_config(self, client):
        with (
            patch("api.main.get_client", return_value={"id": "c1", "name": "Acme"}),
            patch("api.main.upsert_bot_config") as mock,
        ):
            resp = client.put("/bot-configs/c1", json={
                "slack_channel_id": "C456",
                "marketplaces": ["US", "CA"],
                "hourly_bot": {"enabled": True},
            })
        assert resp.status_code == 200
        call_data = mock.call_args[0][1]
        assert call_data["client_id"] == "c1"
        assert call_data["slack_channel_id"] == "C456"

    def test_upsert_bot_config_client_not_found(self, client):
        with patch("api.main.get_client", return_value=None):
            resp = client.put("/bot-configs/missing", json={"slack_channel_id": "C456"})
        assert resp.status_code == 404
