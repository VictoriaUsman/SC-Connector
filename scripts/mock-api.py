#!/usr/bin/env python3
"""Lightweight mock backend for local frontend dev/demo.

Implements the REST surface `frontend/src/lib/api.ts` calls against
`VITE_API_URL`, backed by a JSON file instead of real Firestore/GCP.
No Google Cloud credentials, no emulator, no Java required.

Usage:
    python scripts/mock-api.py [--port 8080] [--reset]

Then in frontend/.env:
    VITE_API_URL=http://localhost:8080
    VITE_API_KEY=

Note: the dashboard's live job table uses a direct Firestore listener
(firebase/firestore `onSnapshot`), not this REST API, so it will stay
empty/erroring under the mock backend. Everything routed through
`src/lib/api.ts` (Clients, Schedules, Events, Bot Configs, Ads
profiles, On-Demand) is covered here.
"""
from __future__ import annotations

import argparse
import json
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

import flask
from flask import Flask, jsonify, request

DATA_FILE = Path(__file__).parent / "mock-api-data.json"
_lock = threading.Lock()

app = Flask(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_data() -> dict:
    ts = now_iso()
    return {
        "clients": {
            "test-client": {
                "id": "test-client",
                "name": "Test Client",
                "marketplaces": ["US"],
                "is_active": True,
                "account_type": "seller",
                "sp_api_secret_name": "kalilos-staging-sp-api-test-client",
                "ads_api_secret_name": "kalilos-staging-ads-api-test-client",
                "created_at": ts,
                "updated_at": ts,
            }
        },
        "schedules": {
            "demo-schedule-1": {
                "id": "demo-schedule-1",
                "name": "Daily SP-API Orders",
                "client_ids": ["test-client"],
                "api_source": "sp_api",
                "report_types": ["GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL"],
                "marketplaces": ["US"],
                "frequency": "daily",
                "schedule_config": {"type": "daily", "time": "06:00"},
                "timeframe": {"strategy": "yesterday"},
                "folder_name": "Test Client",
                "subfolder_strategy": "date",
                "reconciliation_days": [3, 7],
                "report_params": {},
                "is_active": True,
                "last_run_status": "success",
                "last_run_at": ts,
                "next_run_at": ts,
                "created_at": ts,
                "updated_at": ts,
            }
        },
        "jobs": {
            "demo-job-1": {
                "id": "demo-job-1",
                "client_id": "test-client",
                "schedule_id": "demo-schedule-1",
                "execution_date": ts[:10],
                "status": "completed",
                "api_source": "sp_api",
                "report_type": "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL",
                "marketplace": "US",
                "retry_count": 0,
                "poll_count": 3,
                "started_at": ts,
                "completed_at": ts,
            }
        },
        "events": {
            "demo-event-1": {
                "id": "demo-event-1",
                "name": "Prime Day Demo",
                "start_date": ts[:10],
                "end_date": ts[:10],
                "status": "upcoming",
                "created_at": ts,
                "updated_at": ts,
            }
        },
        "bot_configs": {
            "test-client": {
                "id": "test-client",
                "client_id": "test-client",
                "channels": [
                    {"id": "C0TESTCHANNEL", "name": "test-client-alerts"},
                    {"id": "C0TESTCHANNEL2", "name": "test-client-internal"},
                ],
                "base_currency": "USD",
                "client_timezone": "America/New_York",
                "marketplaces": ["US"],
                "hourly_bot": {"enabled": True},
                "daily_recap_enabled": True,
                "sku_breakdown_enabled": False,
                "use_test_channel": False,
                "created_at": ts,
                "updated_at": ts,
            }
        },
    }


def load_data() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    data = default_data()
    save_data(data)
    return data


def save_data(data: dict) -> None:
    DATA_FILE.write_text(json.dumps(data, indent=2))


DB = load_data()


@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = request.headers.get("Origin", "*")
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
    return resp


@app.route("/<path:_any>", methods=["OPTIONS"])
def preflight(_any):
    return "", 204


def _err(code: int, msg: str, err_code: str = "NOT_FOUND"):
    return jsonify({"error": msg, "code": err_code}), code


# --- Clients ---------------------------------------------------------------

@app.route("/clients", methods=["GET"])
def list_clients():
    active = request.args.get("active")
    items = list(DB["clients"].values())
    if active == "true":
        items = [c for c in items if c.get("is_active")]
    return jsonify(items), 200


@app.route("/clients/<client_id>", methods=["GET"])
def get_client(client_id):
    c = DB["clients"].get(client_id)
    if not c:
        return _err(404, "Client not found")
    return jsonify(c), 200


@app.route("/clients", methods=["POST"])
def create_client():
    with _lock:
        data = request.get_json(force=True)
        cid = data["id"]
        ts = now_iso()
        DB["clients"][cid] = {**data, "is_active": True, "created_at": ts, "updated_at": ts}
        save_data(DB)
    return jsonify({"id": cid, "status": "created"}), 201


@app.route("/clients/<client_id>", methods=["PUT"])
def update_client(client_id):
    with _lock:
        if client_id not in DB["clients"]:
            return _err(404, "Client not found")
        data = request.get_json(force=True)
        DB["clients"][client_id] = {**DB["clients"][client_id], **data, "updated_at": now_iso()}
        save_data(DB)
    return jsonify({"id": client_id, "status": "updated"}), 200


@app.route("/clients/<client_id>", methods=["DELETE"])
def delete_client(client_id):
    with _lock:
        DB["clients"].pop(client_id, None)
        save_data(DB)
    return jsonify({"id": client_id, "status": "deleted"}), 200


@app.route("/clients/<client_id>/connect", methods=["POST"])
def connect_manual(client_id):
    data = request.get_json(force=True)
    return jsonify({"id": client_id, "api_source": data.get("api_source", "sp_api"), "status": "connected"}), 200


@app.route("/clients/<client_id>/sp-api-token", methods=["GET"])
def sp_api_token(client_id):
    return jsonify({"refresh_token": "Atzr|FAKE-DEMO-TOKEN"}), 200


@app.route("/oauth/status/<client_id>", methods=["GET"])
def oauth_status(client_id):
    return jsonify({"client_id": client_id, "sp_api_connected": True, "ads_api_connected": True}), 200


# --- Schedules ---------------------------------------------------------------

@app.route("/schedules", methods=["GET"])
def list_schedules():
    items = list(DB["schedules"].values())
    client_id = request.args.get("client_id")
    if client_id:
        items = [s for s in items if client_id in s.get("client_ids", [])]
    if request.args.get("active") == "true":
        items = [s for s in items if s.get("is_active")]
    return jsonify(items), 200


@app.route("/schedules", methods=["POST"])
def create_schedule():
    with _lock:
        data = request.get_json(force=True)
        sid = f"schedule-{secrets.token_hex(4)}"
        ts = now_iso()
        DB["schedules"][sid] = {**data, "id": sid, "is_active": True, "created_at": ts, "updated_at": ts}
        save_data(DB)
    return jsonify({"id": sid, "status": "created"}), 201


@app.route("/schedules/<sid>", methods=["PUT"])
def update_schedule(sid):
    with _lock:
        if sid not in DB["schedules"]:
            return _err(404, "Schedule not found")
        data = request.get_json(force=True)
        DB["schedules"][sid] = {**DB["schedules"][sid], **data, "updated_at": now_iso()}
        save_data(DB)
    return jsonify({"id": sid, "status": "updated"}), 200


@app.route("/schedules/<sid>", methods=["DELETE"])
def delete_schedule(sid):
    with _lock:
        DB["schedules"].pop(sid, None)
        save_data(DB)
    return jsonify({"id": sid, "status": "deleted"}), 200


@app.route("/schedules/<sid>/trigger", methods=["POST"])
def trigger_schedule(sid):
    return jsonify({"schedule_id": sid, "status": "triggered", "jobs_started": 1, "job_ids": ["demo-job-1"], "errors": 0}), 200


# --- Jobs ---------------------------------------------------------------

@app.route("/jobs", methods=["GET"])
def list_jobs():
    items = list(DB["jobs"].values())
    for key in ("client_id", "status", "schedule_id", "execution_date"):
        val = request.args.get(key)
        if val:
            items = [j for j in items if j.get(key) == val]
    limit = request.args.get("limit")
    if limit:
        items = items[: int(limit)]
    return jsonify(items), 200


@app.route("/jobs/<jid>", methods=["GET"])
def get_job(jid):
    j = DB["jobs"].get(jid)
    if not j:
        return _err(404, "Job not found")
    return jsonify(j), 200


@app.route("/jobs/<jid>/retry", methods=["POST"])
def retry_job(jid):
    new_id = f"job-{secrets.token_hex(4)}"
    return jsonify({"status": "retried", "original_job_id": jid, "new_job_id": new_id}), 200


@app.route("/jobs/batch-retry", methods=["POST"])
def batch_retry():
    data = request.get_json(force=True)
    ids = data.get("job_ids", [])
    results = [{"job_id": jid, "status": "retried", "new_job_id": f"job-{secrets.token_hex(4)}"} for jid in ids]
    return jsonify({"results": results, "summary": {"total": len(ids), "retried": len(ids), "skipped": 0, "errors": 0}}), 200


# --- Events ---------------------------------------------------------------

@app.route("/events", methods=["GET"])
def list_events():
    return jsonify(list(DB["events"].values())), 200


@app.route("/events", methods=["POST"])
def create_event():
    with _lock:
        data = request.get_json(force=True)
        eid = f"event-{secrets.token_hex(4)}"
        ts = now_iso()
        DB["events"][eid] = {**data, "id": eid, "status": "upcoming", "created_at": ts, "updated_at": ts}
        save_data(DB)
    return jsonify({"id": eid, "status": "created"}), 201


@app.route("/events/<eid>", methods=["PUT"])
def update_event(eid):
    with _lock:
        if eid not in DB["events"]:
            return _err(404, "Event not found")
        data = request.get_json(force=True)
        DB["events"][eid] = {**DB["events"][eid], **data, "updated_at": now_iso()}
        save_data(DB)
    return jsonify({"id": eid, "status": "updated"}), 200


@app.route("/events/<eid>", methods=["DELETE"])
def delete_event(eid):
    with _lock:
        DB["events"].pop(eid, None)
        save_data(DB)
    return jsonify({"id": eid, "status": "deleted"}), 200


@app.route("/events/<eid>/activate", methods=["POST"])
def activate_event(eid):
    with _lock:
        if eid not in DB["events"]:
            return _err(404, "Event not found")
        DB["events"][eid]["status"] = "live"
        DB["events"][eid]["manually_activated"] = True
        DB["events"][eid]["activated_at"] = now_iso()
        save_data(DB)
    return jsonify({"id": eid, "status": "activated"}), 200


@app.route("/events/<eid>/deactivate", methods=["POST"])
def deactivate_event(eid):
    with _lock:
        if eid not in DB["events"]:
            return _err(404, "Event not found")
        DB["events"][eid]["status"] = "completed"
        save_data(DB)
    return jsonify({"id": eid, "status": "deactivated"}), 200


# --- Bot Configs ---------------------------------------------------------------

@app.route("/bot-configs", methods=["GET"])
def list_bot_configs():
    return jsonify(list(DB["bot_configs"].values())), 200


@app.route("/bot-configs/<client_id>", methods=["GET"])
def get_bot_config(client_id):
    c = DB["bot_configs"].get(client_id)
    if not c:
        return _err(404, "Bot config not found")
    return jsonify(c), 200


@app.route("/bot-configs/<client_id>", methods=["PUT"])
def upsert_bot_config(client_id):
    with _lock:
        if client_id not in DB["clients"]:
            return _err(404, "Client not found")
        data = request.get_json(force=True)
        data.pop("id", None)
        data["client_id"] = client_id
        ts = now_iso()
        existing = DB["bot_configs"].get(client_id, {})
        DB["bot_configs"][client_id] = {
            **existing,
            **data,
            "id": client_id,
            "updated_at": ts,
            "created_at": existing.get("created_at", ts),
        }
        save_data(DB)
    return jsonify({"id": client_id, "status": "updated"}), 200


# --- Ads / on-demand (static demo data) -----------------------------------

@app.route("/ads-profiles", methods=["GET"])
def list_ads_profiles():
    return jsonify([
        {
            "profileId": 1234567890,
            "countryCode": "US",
            "currencyCode": "USD",
            "dailyBudget": 100.0,
            "timezone": "America/Los_Angeles",
            "accountInfo": {"marketplaceStringId": "ATVPDKIKX0DER", "id": "A1DEMO", "type": "seller", "name": "Test Client"},
            "_linked_client_id": "test-client",
            "_region": "na",
        }
    ]), 200


@app.route("/sp-api-accounts", methods=["GET"])
def list_sp_api_accounts():
    return jsonify([
        {
            "id": "test-client",
            "name": "Test Client",
            "marketplaces": ["US"],
            "sp_api_connected": True,
            "ads_profile_id": "1234567890",
            "ads_profiles": [],
            "ads_profiles_error": None,
        }
    ]), 200


@app.route("/ads-report-config", methods=["GET"])
def ads_report_config():
    types = ["spCampaigns", "spSearchTerm", "spTargeting", "spAdvertisedProduct",
              "sbCampaigns", "sbSearchTerm", "sdCampaigns", "sdTargeting",
              "sdAdvertisedProduct", "spPlacement"]
    return jsonify({t: {"adProduct": t[:2].upper(), "groupBy": [], "columns": {"dimensions": [], "metrics": []}, "timeUnits": ["SUMMARY"]} for t in types}), 200


@app.route("/on-demand", methods=["POST"])
def on_demand():
    data = request.get_json(force=True)
    report_types = data.get("report_types", [])
    job_ids = [f"job-{secrets.token_hex(4)}" for _ in report_types] or [f"job-{secrets.token_hex(4)}"]
    return jsonify({"job_ids": job_ids, "jobs_started": len(job_ids), "errors": 0, "status": "started"}), 200


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--reset", action="store_true", help="Reset mock-api-data.json to defaults")
    args = parser.parse_args()

    if args.reset:
        DB = default_data()
        save_data(DB)
        print(f"[mock-api] reset {DATA_FILE} to defaults")

    print(f"[mock-api] serving fake data from {DATA_FILE}")
    print(f"[mock-api] listening on http://localhost:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=True, use_reloader=False)
