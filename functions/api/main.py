"""API function — CRUD for clients, schedules, jobs, on-demand triggers, and OAuth.

Publicly accessible Cloud Function that serves as the backend for the React UI.
Uses Flask routing internally for clean path-based dispatch.
"""

from __future__ import annotations

import io
import json
import logging
import os
import secrets
from datetime import datetime, date, timezone
from typing import Any
from urllib.parse import urlencode

import flask
import requests
from google.cloud import secretmanager
from shared.ads_report_config import ADS_REPORT_TYPES as _ADS_REPORT_TYPES, TIME_UNITS, get_report_defaults
from shared.config import LWA_TOKEN_URL, get_environment, get_project
from shared.schedule_compute import VALID_TIMEFRAME_STRATEGIES, compute_next_run
from shared.firestore_utils import (
    create_job,
    create_schedule,
    delete_client as fs_delete_client,
    delete_schedule as fs_delete_schedule,
    get_client,
    get_job,
    get_schedule,
    list_clients,
    list_jobs,
    list_schedules,
    update_schedule,
    upsert_client,
)
from shared.workflow_launcher import (
    build_payload,
    get_workflow_parent,
    launch_execution,
    launch_for_marketplace,
)

logger = logging.getLogger(__name__)

app = flask.Flask(__name__)

VALID_API_SOURCES = {"sp_api", "ads_api"}
VALID_FREQUENCIES = {"hourly", "daily", "weekly", "monthly"}
VALID_SCHEDULE_TYPES = {"hourly", "daily", "weekly", "monthly"}
VALID_SUBFOLDER_STRATEGIES = {"date", "none"}


def _validate_timeframe(timeframe: dict) -> str | None:
    """Return an error message if the timeframe config is invalid, or None if valid."""
    if not isinstance(timeframe, dict):
        return "timeframe must be a dict"

    strategy = timeframe.get("strategy")
    if strategy is None:
        return "timeframe.strategy is required"
    if strategy not in VALID_TIMEFRAME_STRATEGIES:
        return f"timeframe.strategy must be one of: {sorted(VALID_TIMEFRAME_STRATEGIES)}"

    if strategy == "last_n_days":
        days = timeframe.get("days")
        if days is None or not isinstance(days, (int, float)) or int(days) < 1:
            return "timeframe.days must be a positive integer for last_n_days"
        if int(days) > 365:
            return "timeframe.days cannot exceed 365"
        end_offset = timeframe.get("end_offset_days", 0)
        if not isinstance(end_offset, (int, float)) or int(end_offset) < 0:
            return "timeframe.end_offset_days must be a non-negative integer"

    elif strategy == "rolling_window":
        for field in ("start_offset", "end_offset"):
            val = timeframe.get(field)
            if val is None or not isinstance(val, (int, float)):
                return f"timeframe.{field} is required (integer) for rolling_window"
        if int(timeframe["start_offset"]) > int(timeframe["end_offset"]):
            return "timeframe.start_offset must be <= end_offset"

    elif strategy == "last_calendar_week":
        ws = timeframe.get("week_start", 0)
        if not isinstance(ws, (int, float)) or int(ws) not in range(7):
            return "timeframe.week_start must be 0-6 (Mon-Sun)"

    return None


# ---------------------------------------------------------------------------
# Serialization — Firestore datetimes → ISO strings
# ---------------------------------------------------------------------------

def _serialize(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Max-Age": "3600",
}


@app.after_request
def _add_cors(response: flask.Response) -> flask.Response:
    response.headers.update(_CORS_HEADERS)
    return response


@app.before_request
def _handle_preflight():
    if flask.request.method == "OPTIONS":
        return "", 204


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def _not_found(_e: Exception):
    return flask.jsonify({"error": "Not found", "code": "NOT_FOUND"}), 404


@app.errorhandler(405)
def _method_not_allowed(_e: Exception):
    return flask.jsonify({"error": "Method not allowed", "code": "METHOD_NOT_ALLOWED"}), 405


@app.errorhandler(Exception)
def _internal_error(e: Exception):
    logger.exception("Unhandled API error")
    return flask.jsonify({"error": "Internal server error", "code": "INTERNAL_ERROR"}), 500


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return flask.jsonify({"status": "ok"}), 200


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

@app.route("/clients", methods=["GET"])
def list_clients_route():
    active = flask.request.args.get("active") == "true"
    return flask.jsonify(_serialize(list_clients(active_only=active))), 200


@app.route("/clients/<client_id>", methods=["GET"])
def get_client_route(client_id: str):
    client = get_client(client_id)
    if not client:
        return flask.jsonify({"error": "Client not found", "code": "NOT_FOUND"}), 404
    return flask.jsonify(_serialize(client)), 200


@app.route("/clients", methods=["POST"])
def create_client_route():
    data = flask.request.get_json(silent=True) or {}
    client_id = data.pop("id", None)
    if not client_id:
        return flask.jsonify({"error": "Missing 'id'", "code": "INVALID_REQUEST"}), 400
    if not data.get("name"):
        return flask.jsonify({"error": "Missing 'name'", "code": "INVALID_REQUEST"}), 400

    upsert_client(client_id, data)
    return flask.jsonify({"id": client_id, "status": "created"}), 201


@app.route("/clients/<client_id>", methods=["PUT"])
def update_client_route(client_id: str):
    if not get_client(client_id):
        return flask.jsonify({"error": "Client not found", "code": "NOT_FOUND"}), 404

    data = flask.request.get_json(silent=True) or {}
    data.pop("id", None)
    upsert_client(client_id, data)
    return flask.jsonify({"id": client_id, "status": "updated"}), 200


@app.route("/clients/<client_id>", methods=["DELETE"])
def delete_client_route(client_id: str):
    fs_delete_client(client_id)
    return flask.jsonify({"id": client_id, "status": "deleted"}), 200


@app.route("/clients/<client_id>/connect", methods=["POST"])
def connect_client_manual(client_id: str):
    """Manually connect a client's API credentials.

    SP API: stores refresh_token in Secret Manager.
    Ads API: stores profile_id directly in Firestore (refresh_token lives
    in the app-level secret).
    """
    client = get_client(client_id)
    if not client:
        return flask.jsonify({"error": "Client not found", "code": "NOT_FOUND"}), 404

    data = flask.request.get_json(silent=True) or {}
    api_source = data.get("api_source")

    if api_source not in VALID_API_SOURCES:
        return flask.jsonify({"error": f"Invalid api_source, must be one of {VALID_API_SOURCES}", "code": "INVALID_REQUEST"}), 400

    try:
        if api_source == "sp_api":
            refresh_token = data.get("refresh_token", "").strip()
            if not refresh_token:
                return flask.jsonify({"error": "Missing refresh_token", "code": "INVALID_REQUEST"}), 400
            secret_name = _store_client_secret(client_id, api_source, {"refresh_token": refresh_token})
            upsert_client(client_id, {"sp_api_secret_name": secret_name})

        elif api_source == "ads_api":
            profile_id = data.get("profile_id", "").strip()
            if not profile_id:
                return flask.jsonify({"error": "Missing profile_id", "code": "INVALID_REQUEST"}), 400
            upsert_client(client_id, {"ads_profile_id": profile_id})

        logger.info("Manual connect completed", extra={"client_id": client_id, "api_source": api_source})
        return flask.jsonify({"id": client_id, "api_source": api_source, "status": "connected"}), 200
    except Exception as exc:
        logger.exception("Manual connect failed", extra={"client_id": client_id})
        return flask.jsonify({"error": str(exc)[:200], "code": "INTERNAL_ERROR"}), 500


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------

@app.route("/schedules", methods=["GET"])
def list_schedules_route():
    client_id = flask.request.args.get("client_id")
    active = flask.request.args.get("active") == "true"
    return flask.jsonify(_serialize(list_schedules(client_id=client_id, active_only=active))), 200


@app.route("/schedules/<schedule_id>", methods=["GET"])
def get_schedule_route(schedule_id: str):
    schedule = get_schedule(schedule_id)
    if not schedule:
        return flask.jsonify({"error": "Schedule not found", "code": "NOT_FOUND"}), 404
    return flask.jsonify(_serialize(schedule)), 200


@app.route("/schedules", methods=["POST"])
def create_schedule_route():
    data = flask.request.get_json(silent=True) or {}

    required = ["api_source", "report_type"]
    missing = [f for f in required if not data.get(f)]

    has_clients = bool(data.get("client_ids") or data.get("client_id"))
    has_markets = bool(data.get("marketplaces") or data.get("marketplace"))
    if not has_clients:
        missing.append("client_ids")
    if not has_markets:
        missing.append("marketplaces")

    if missing:
        return flask.jsonify({"error": f"Missing fields: {', '.join(missing)}", "code": "INVALID_REQUEST"}), 400

    if data["api_source"] not in VALID_API_SOURCES:
        return flask.jsonify({"error": f"api_source must be one of: {sorted(VALID_API_SOURCES)}", "code": "INVALID_REQUEST"}), 400

    client_ids = data.get("client_ids") or [data.pop("client_id")]
    data["client_ids"] = client_ids
    data.pop("client_id", None)

    marketplaces = data.get("marketplaces") or [data.pop("marketplace")]
    data["marketplaces"] = marketplaces
    data.pop("marketplace", None)

    for cid in client_ids:
        if not get_client(cid):
            return flask.jsonify({"error": f"Client '{cid}' not found", "code": "NOT_FOUND"}), 404

    schedule_config = data.get("schedule_config", {})
    if not schedule_config:
        freq = data.get("frequency", "daily")
        if freq not in VALID_FREQUENCIES:
            return flask.jsonify({"error": f"frequency must be one of: {sorted(VALID_FREQUENCIES)}", "code": "INVALID_REQUEST"}), 400
        schedule_config = {"type": freq, "time": "03:00"}
    data["schedule_config"] = schedule_config
    data.setdefault("frequency", schedule_config.get("type", "daily"))

    subfolder = data.get("subfolder_strategy", "date")
    if subfolder not in VALID_SUBFOLDER_STRATEGIES:
        return flask.jsonify({"error": f"subfolder_strategy must be one of: {sorted(VALID_SUBFOLDER_STRATEGIES)}", "code": "INVALID_REQUEST"}), 400

    if "timeframe" in data:
        tf_error = _validate_timeframe(data["timeframe"])
        if tf_error:
            return flask.jsonify({"error": tf_error, "code": "INVALID_REQUEST"}), 400

    data.setdefault("folder_name", "")
    data.setdefault("subfolder_strategy", "date")
    data.setdefault("reconciliation_days", [3, 7])
    data.setdefault("report_params", {})

    now = datetime.now(timezone.utc)
    data.setdefault("next_run_at", compute_next_run(now, schedule_config))

    schedule_id = create_schedule(data)
    return flask.jsonify({"id": schedule_id, "status": "created"}), 201


@app.route("/schedules/<schedule_id>", methods=["PUT"])
def update_schedule_route(schedule_id: str):
    existing = get_schedule(schedule_id)
    if not existing:
        return flask.jsonify({"error": "Schedule not found", "code": "NOT_FOUND"}), 404

    data = flask.request.get_json(silent=True) or {}
    data.pop("id", None)

    if "api_source" in data and data["api_source"] not in VALID_API_SOURCES:
        return flask.jsonify({"error": f"api_source must be one of: {sorted(VALID_API_SOURCES)}", "code": "INVALID_REQUEST"}), 400

    if "frequency" in data and data["frequency"] not in VALID_FREQUENCIES:
        return flask.jsonify({"error": f"frequency must be one of: {sorted(VALID_FREQUENCIES)}", "code": "INVALID_REQUEST"}), 400

    if "subfolder_strategy" in data and data["subfolder_strategy"] not in VALID_SUBFOLDER_STRATEGIES:
        return flask.jsonify({"error": f"subfolder_strategy must be one of: {sorted(VALID_SUBFOLDER_STRATEGIES)}", "code": "INVALID_REQUEST"}), 400

    if "timeframe" in data:
        tf_error = _validate_timeframe(data["timeframe"])
        if tf_error:
            return flask.jsonify({"error": tf_error, "code": "INVALID_REQUEST"}), 400

    schedule_config = data.get("schedule_config") or existing.get("schedule_config")
    if "frequency" in data or "schedule_config" in data:
        is_active = data.get("is_active", existing.get("is_active", True))
        if is_active and schedule_config:
            data["next_run_at"] = compute_next_run(datetime.now(timezone.utc), schedule_config)

    data["updated_at"] = datetime.now(timezone.utc)
    update_schedule(schedule_id, data)
    return flask.jsonify({"id": schedule_id, "status": "updated"}), 200


@app.route("/schedules/<schedule_id>", methods=["DELETE"])
def delete_schedule_route(schedule_id: str):
    fs_delete_schedule(schedule_id)
    return flask.jsonify({"id": schedule_id, "status": "deleted"}), 200


@app.route("/schedules/<schedule_id>/trigger", methods=["POST"])
def trigger_schedule_route(schedule_id: str):
    """Immediately trigger all jobs for a schedule (same logic as the cron scheduler)."""
    schedule = get_schedule(schedule_id)
    if not schedule:
        return flask.jsonify({"error": "Schedule not found", "code": "NOT_FOUND"}), 404

    client_ids = schedule.get("client_ids") or ([schedule["client_id"]] if schedule.get("client_id") else [])
    marketplaces = schedule.get("marketplaces") or ([schedule["marketplace"]] if schedule.get("marketplace") else [])

    if not client_ids or not marketplaces:
        return flask.jsonify({"error": "Schedule has no clients or marketplaces", "code": "INVALID_STATE"}), 400

    for cid in client_ids:
        client = get_client(cid)
        if not client or not client.get("is_active", True):
            return flask.jsonify({"error": f"Client '{cid}' not found or inactive", "code": "NOT_FOUND"}), 404

    parent = get_workflow_parent()
    now = datetime.now(timezone.utc)
    job_ids: list[str] = []
    errors: list[dict[str, str]] = []

    for cid in client_ids:
        for marketplace in marketplaces:
            try:
                ids = launch_for_marketplace(
                    parent, now, schedule, cid, marketplace,
                    extra_job_fields={"trigger": "manual"},
                )
                job_ids.extend(ids)
            except Exception as exc:
                logger.exception("Failed to start workflow for manual trigger", extra={"schedule_id": schedule_id})
                errors.append({"schedule_id": schedule_id, "error": str(exc)[:200]})

    update_schedule(schedule_id, {"last_run_at": now, "updated_at": now})

    logger.info("Manual schedule trigger", extra={"schedule_id": schedule_id, "jobs": len(job_ids), "errors": len(errors)})
    return flask.jsonify({
        "schedule_id": schedule_id,
        "status": "triggered",
        "jobs_started": len(job_ids),
        "job_ids": job_ids,
        "errors": len(errors),
    }), 201


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@app.route("/jobs", methods=["GET"])
def list_jobs_route():
    client_id = flask.request.args.get("client_id")
    status = flask.request.args.get("status")
    limit = min(int(flask.request.args.get("limit", "50")), 200)
    return flask.jsonify(_serialize(list_jobs(client_id=client_id, status=status, limit=limit))), 200


@app.route("/jobs/<job_id>", methods=["GET"])
def get_job_route(job_id: str):
    job = get_job(job_id)
    if not job:
        return flask.jsonify({"error": "Job not found", "code": "NOT_FOUND"}), 404
    return flask.jsonify(_serialize(job)), 200


# ---------------------------------------------------------------------------
# On-demand report trigger
# ---------------------------------------------------------------------------

@app.route("/on-demand", methods=["POST"])
def on_demand_route():
    data = flask.request.get_json(silent=True) or {}

    missing = [f for f in ("client_id", "api_source", "marketplace", "report_type") if not data.get(f)]
    if missing:
        return flask.jsonify({"error": f"Missing fields: {', '.join(missing)}", "code": "INVALID_REQUEST"}), 400

    if data["api_source"] not in VALID_API_SOURCES:
        return flask.jsonify({"error": f"api_source must be one of: {sorted(VALID_API_SOURCES)}", "code": "INVALID_REQUEST"}), 400

    client = get_client(data["client_id"])
    if not client or not client.get("is_active", True):
        return flask.jsonify({"error": "Client not found or inactive", "code": "NOT_FOUND"}), 404

    job_id = create_job({
        "client_id": data["client_id"],
        "api_source": data["api_source"],
        "marketplace": data["marketplace"],
        "report_type": data["report_type"],
        "frequency": "on_demand",
    })

    parent = get_workflow_parent()
    payload = build_payload(
        api_source=data["api_source"],
        client_id=data["client_id"],
        marketplace=data["marketplace"],
        report_type=data["report_type"],
        report_params=data.get("report_params", {}),
        job_id=job_id,
        frequency="on_demand",
        folder_name=data.get("folder_name", ""),
        subfolder_strategy=data.get("subfolder_strategy", "date"),
    )

    try:
        result = launch_execution(parent, payload, job_id, error_phase="trigger")
        logger.info("On-demand workflow started", extra={"job_id": job_id, "execution": result.name})
        return flask.jsonify({
            "job_id": job_id,
            "execution_name": result.name,
            "status": "started",
        }), 201
    except Exception as exc:
        logger.exception("Failed to start workflow after retries", extra={"job_id": job_id})
        return flask.jsonify({
            "error": f"Failed to start workflow: {exc}",
            "code": "WORKFLOW_START_FAILED",
            "job_id": job_id,
        }), 502


# ---------------------------------------------------------------------------
# Ads Report Config — expose available columns/config per report type
# ---------------------------------------------------------------------------

@app.route("/ads-report-config", methods=["GET"])
def ads_report_config_list():
    result = {}
    for rt, cfg in _ADS_REPORT_TYPES.items():
        result[rt] = {
            "adProduct": cfg["adProduct"],
            "groupBy": cfg["groupBy"],
            "columns": cfg["columns"],
            "timeUnits": TIME_UNITS,
        }
    return flask.jsonify(result), 200


@app.route("/ads-report-config/<report_type>", methods=["GET"])
def ads_report_config_detail(report_type: str):
    defaults = get_report_defaults(report_type)
    if not defaults:
        return flask.jsonify({"error": f"Unknown report type: {report_type}", "code": "NOT_FOUND"}), 404
    return flask.jsonify({
        **defaults,
        "timeUnits": TIME_UNITS,
    }), 200


# ---------------------------------------------------------------------------
# OAuth — Amazon SP API + Ads API authorization
# ---------------------------------------------------------------------------

_sm_client: secretmanager.SecretManagerServiceClient | None = None

_oauth_states: dict[str, dict[str, str]] = {}

SP_API_AUTH_URL = "https://sellercentral.amazon.com/apps/authorize/consent"
ADS_API_AUTH_URL = "https://www.amazon.com/ap/oa"


def _get_sm() -> secretmanager.SecretManagerServiceClient:
    global _sm_client
    if _sm_client is None:
        _sm_client = secretmanager.SecretManagerServiceClient()
    return _sm_client


def _read_app_secret(api_source: str) -> dict[str, str]:
    env = get_environment()
    prefix = "sp-api" if api_source == "sp_api" else "ads-api"
    secret_name = f"kalilos-{env}-{prefix}-app-credentials"
    project = get_project()
    name = f"projects/{project}/secrets/{secret_name}/versions/latest"
    resp = _get_sm().access_secret_version(name=name)
    return json.loads(resp.payload.data.decode("utf-8"))


def _store_client_secret(client_id: str, api_source: str, data: dict[str, str]) -> str:
    """Create or update a client-level secret in Secret Manager. Returns secret name."""
    env = get_environment()
    prefix = "sp-api" if api_source == "sp_api" else "ads-api"
    secret_name = f"kalilos-{env}-{prefix}-{client_id}"
    project = get_project()
    parent = f"projects/{project}"
    full_name = f"{parent}/secrets/{secret_name}"

    sm = _get_sm()
    try:
        sm.get_secret(request={"name": full_name})
    except Exception:
        sm.create_secret(request={
            "parent": parent,
            "secret_id": secret_name,
            "secret": {"replication": {"automatic": {}}},
        })

    sm.add_secret_version(request={
        "parent": full_name,
        "payload": {"data": json.dumps(data).encode("utf-8")},
    })

    return secret_name


def _get_oauth_redirect_uri() -> str:
    return os.environ.get("OAUTH_REDIRECT_URI", "")


def _get_frontend_url() -> str:
    return os.environ.get("FRONTEND_URL", "http://localhost:5173")


@app.route("/oauth/sp-api/authorize", methods=["GET"])
def oauth_sp_api_authorize():
    client_id = flask.request.args.get("client_id")
    if not client_id:
        return flask.jsonify({"error": "Missing client_id", "code": "INVALID_REQUEST"}), 400

    client = get_client(client_id)
    if not client:
        return flask.jsonify({"error": "Client not found", "code": "NOT_FOUND"}), 404

    app_creds = _read_app_secret("sp_api")

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {"client_id": client_id, "api_source": "sp_api"}

    params = {
        "application_id": app_creds.get("app_id", app_creds.get("client_id", "")),
        "state": state,
        "redirect_uri": _get_oauth_redirect_uri(),
        "version": "beta",
    }
    auth_url = f"{SP_API_AUTH_URL}?{urlencode(params)}"
    return flask.redirect(auth_url)


@app.route("/oauth/ads-api/authorize", methods=["GET"])
def oauth_ads_api_authorize():
    client_id = flask.request.args.get("client_id")
    if not client_id:
        return flask.jsonify({"error": "Missing client_id", "code": "INVALID_REQUEST"}), 400

    client = get_client(client_id)
    if not client:
        return flask.jsonify({"error": "Client not found", "code": "NOT_FOUND"}), 404

    app_creds = _read_app_secret("ads_api")

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = {"client_id": client_id, "api_source": "ads_api"}

    params = {
        "client_id": app_creds["client_id"],
        "scope": "advertising::campaign_management",
        "response_type": "code",
        "redirect_uri": _get_oauth_redirect_uri(),
        "state": state,
    }
    auth_url = f"{ADS_API_AUTH_URL}?{urlencode(params)}"
    return flask.redirect(auth_url)


@app.route("/oauth/callback", methods=["GET"])
def oauth_callback():
    error = flask.request.args.get("error")
    if error:
        logger.warning("OAuth error from Amazon", extra={"error": error})
        return flask.redirect(f"{_get_frontend_url()}/clients?oauth=error&message={error}")

    code = flask.request.args.get("spapi_oauth_code") or flask.request.args.get("code")
    state = flask.request.args.get("state", "")

    if not code or state not in _oauth_states:
        return flask.redirect(f"{_get_frontend_url()}/clients?oauth=error&message=invalid_state")

    state_data = _oauth_states.pop(state)
    client_id = state_data["client_id"]
    api_source = state_data["api_source"]

    try:
        app_creds = _read_app_secret(api_source)

        token_resp = requests.post(LWA_TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _get_oauth_redirect_uri(),
            "client_id": app_creds["client_id"],
            "client_secret": app_creds["client_secret"],
        }, timeout=15)
        token_resp.raise_for_status()
        tokens = token_resp.json()

        refresh_token = tokens["refresh_token"]

        if api_source == "sp_api":
            secret_name = _store_client_secret(client_id, api_source, {"refresh_token": refresh_token})
            upsert_client(client_id, {"sp_api_secret_name": secret_name})
        elif api_source == "ads_api":
            access_token = tokens["access_token"]
            update_fields: dict[str, str] = {}
            try:
                profiles_resp = requests.get(
                    "https://advertising-api.amazon.com/v2/profiles",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Amazon-Advertising-API-ClientId": app_creds["client_id"],
                    },
                    timeout=15,
                )
                profiles_resp.raise_for_status()
                profiles = profiles_resp.json()
                if profiles:
                    update_fields["ads_profile_id"] = str(profiles[0]["profileId"])
            except Exception as exc:
                logger.warning("Profile discovery failed during OAuth", extra={"error": str(exc)})
            upsert_client(client_id, update_fields)

        logger.info("OAuth completed", extra={"client_id": client_id, "api_source": api_source})
        return flask.redirect(f"{_get_frontend_url()}/clients?oauth=success&api_source={api_source}&client_id={client_id}")

    except Exception as exc:
        logger.exception("OAuth token exchange failed", extra={"client_id": client_id})
        return flask.redirect(f"{_get_frontend_url()}/clients?oauth=error&message={str(exc)[:100]}")


@app.route("/oauth/status/<client_id>", methods=["GET"])
def oauth_status(client_id: str):
    client = get_client(client_id)
    if not client:
        return flask.jsonify({"error": "Client not found", "code": "NOT_FOUND"}), 404

    return flask.jsonify({
        "client_id": client_id,
        "sp_api_connected": bool(client.get("sp_api_secret_name")),
        "ads_api_connected": bool(client.get("ads_profile_id")),
    }), 200


# ---------------------------------------------------------------------------
# Entry point — forward to Flask app for routing
# ---------------------------------------------------------------------------

def handler(request: flask.Request) -> flask.Response:
    environ = request.environ.copy()
    body = request.get_data()
    environ["wsgi.input"] = io.BytesIO(body)
    environ["CONTENT_LENGTH"] = str(len(body))

    with app.request_context(environ):
        return app.full_dispatch_request()
