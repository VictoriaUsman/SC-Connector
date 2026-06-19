"""API function — CRUD for clients, schedules, jobs, on-demand triggers, and OAuth.

Publicly accessible Cloud Function that serves as the backend for the React UI.
Uses Flask routing internally for clean path-based dispatch.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import secrets
from datetime import datetime, date, timezone
from typing import Any
from urllib.parse import urlencode

import flask
import requests
from google.cloud import secretmanager
from shared.ads_report_config import ADS_REPORT_TYPES as _ADS_REPORT_TYPES, TIME_UNITS, get_report_defaults
from shared.config import ADS_API_ENDPOINTS, LWA_TOKEN_URL, get_environment, get_project
from shared.logging_setup import (
    bind_log_context,
    clear_log_context,
    init_logging,
    new_request_id,
    trace_from_cloud_header,
)
from shared.schedule_compute import VALID_TIMEFRAME_STRATEGIES, compute_next_run, marketplace_today
from shared.firestore_utils import (
    create_event,
    create_job,
    create_schedule,
    delete_client as fs_delete_client,
    delete_event as fs_delete_event,
    delete_schedule as fs_delete_schedule,
    get_bot_config,
    get_client,
    get_event,
    get_job,
    get_schedule,
    list_bot_configs,
    list_clients,
    list_events,
    list_jobs,
    list_schedules,
    resolve_client,
    update_event,
    update_schedule,
    upsert_bot_config,
    upsert_client,
)
from shared.api_operations import is_api_operation
from shared.workflow_launcher import (
    _expand_report_option_variants,
    build_payload,
    client_has_credentials,
    get_report_types,
    get_workflow_parent,
    infer_api_source,
    launch_execution,
    launch_for_marketplace,
    validate_report_types,
)

logger = logging.getLogger(__name__)
init_logging("api")

app = flask.Flask(__name__)

VALID_API_SOURCES = {"sp_api", "ads_api", "both"}
VALID_FREQUENCIES = {"hourly", "daily", "weekly", "monthly"}
VALID_SCHEDULE_TYPES = {"hourly", "daily", "weekly", "monthly"}
VALID_SUBFOLDER_STRATEGIES = {"date", "none"}
# Amazon account type. "seller" = Seller Central (3P), "vendor" = Vendor Central (1P).
# Both authorize via SP-API/LWA and store credentials identically; the type only
# changes the OAuth consent host and which report surface applies.
VALID_ACCOUNT_TYPES = {"seller", "vendor"}
DEFAULT_ACCOUNT_TYPE = "seller"

# A client_id becomes part of GCP Secret Manager resource names
# ("kalilos-{env}-sp-api-{client_id}"), which only allow [a-zA-Z0-9_-].
# We enforce a stricter kebab-case slug so ids stay URL-safe and stable.
_CLIENT_ID_PATTERN = re.compile(r"^[a-z0-9-]+$")


def _is_valid_client_id(client_id: str) -> bool:
    """True if client_id is a kebab-case slug safe for Secret Manager names."""
    return bool(client_id) and bool(_CLIENT_ID_PATTERN.fullmatch(client_id))


# Secret Manager secret ids only allow [A-Za-z0-9_-]. Some existing clients have
# legacy free-form ids (e.g. "thehome&office") that contain characters Secret
# Manager rejects, so embedding the raw id in a secret name makes create_secret
# throw INVALID_ARGUMENT and the OAuth connect silently fails to persist.
_SECRET_UNSAFE_PATTERN = re.compile(r"[^A-Za-z0-9_-]")


def _secret_safe_segment(client_id: str) -> str:
    """Return a Secret Manager-safe segment derived from a client id.

    Valid kebab-case ids pass through unchanged so previously created secrets
    keep resolving. Ids with characters Secret Manager rejects are sanitized and
    given a short deterministic hash suffix derived from the original id, so two
    distinct ids can never collide onto the same secret name.
    """
    if _is_valid_client_id(client_id):
        return client_id
    sanitized = _SECRET_UNSAFE_PATTERN.sub("-", client_id).strip("-")
    digest = hashlib.sha1(client_id.encode("utf-8")).hexdigest()[:8]
    return f"{sanitized}-{digest}" if sanitized else f"client-{digest}"


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

    elif strategy == "prior_year_window":
        days_before = timeframe.get("days_before")
        if days_before is None or not isinstance(days_before, (int, float)) or int(days_before) < 1:
            return "timeframe.days_before must be a positive integer for prior_year_window"
        if int(days_before) > 365:
            return "timeframe.days_before cannot exceed 365"
        days_after = timeframe.get("days_after")
        if days_after is None or not isinstance(days_after, (int, float)) or int(days_after) < 0:
            return "timeframe.days_after must be a non-negative integer for prior_year_window"
        if int(days_after) > 365:
            return "timeframe.days_after cannot exceed 365"
        years_back = timeframe.get("years_back", 1)
        if not isinstance(years_back, (int, float)) or int(years_back) < 1 or int(years_back) > 5:
            return "timeframe.years_back must be 1-5"
        anchor_offset = timeframe.get("anchor_offset_days", 0)
        if not isinstance(anchor_offset, (int, float)) or int(anchor_offset) < 0:
            return "timeframe.anchor_offset_days must be a non-negative integer"
        if int(anchor_offset) > 30:
            return "timeframe.anchor_offset_days cannot exceed 30"

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
    "Access-Control-Allow-Headers": "Content-Type, Authorization, X-API-Key",
    "Access-Control-Max-Age": "3600",
}


@app.after_request
def _add_cors(response: flask.Response) -> flask.Response:
    response.headers.update(_CORS_HEADERS)
    return response


@app.before_request
def _bind_correlation():
    """Start every request with a clean, correlated logging context.

    A reused (warm) instance must not leak the previous request's ids, so we
    reset first. We honour an inbound ``X-Request-Id`` (or mint one) and pull the
    GCP trace id from ``X-Cloud-Trace-Context`` so all of a request's log lines
    group together and can be found with a single filter.
    """
    clear_log_context()
    request_id = flask.request.headers.get("X-Request-Id") or new_request_id()
    trace, span_id = trace_from_cloud_header(
        flask.request.headers.get("X-Cloud-Trace-Context"), get_project()
    )
    flask.g.request_id = request_id
    bind_log_context(
        request_id=request_id,
        trace=trace,
        span_id=span_id,
        method=flask.request.method,
        path=flask.request.path,
    )


@app.after_request
def _echo_request_id(response: flask.Response) -> flask.Response:
    """Return the request id so callers (and the agent) can correlate a response
    to its server-side logs."""
    request_id = getattr(flask.g, "request_id", None)
    if request_id:
        response.headers["X-Request-Id"] = request_id
    return response


@app.teardown_request
def _clear_correlation(_exc: BaseException | None = None) -> None:
    clear_log_context()


@app.before_request
def _handle_preflight():
    if flask.request.method == "OPTIONS":
        return "", 204


# ---------------------------------------------------------------------------
# API Key authentication
# ---------------------------------------------------------------------------

_API_KEY: str = os.environ.get("API_KEY", "")

_AUTH_EXEMPT_PREFIXES = ("/health", "/oauth/")


@app.before_request
def _check_api_key():
    if not _API_KEY:
        return None
    path = flask.request.path
    if path == "/" or any(path.startswith(p) for p in _AUTH_EXEMPT_PREFIXES):
        return None
    key = flask.request.headers.get("X-API-Key", "")
    if not key or key != _API_KEY:
        return flask.jsonify({"error": "Invalid or missing API key", "code": "UNAUTHORIZED"}), 401


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
    """Liveness by default; readiness (dependency checks) with ``?deep=1``.

    The default path stays a cheap static ``ok`` (used by the uptime check). The
    deep variant verifies Firestore reachability and returns 503 if degraded, so
    a broken dependency surfaces instead of a green-but-broken service.
    """
    if flask.request.args.get("deep") != "1":
        return flask.jsonify({"status": "ok"}), 200

    checks: dict[str, str] = {}
    healthy = True
    try:
        from shared.firestore_utils import get_db
        list(get_db().collection("clients").limit(1).stream())
        checks["firestore"] = "ok"
    except Exception as exc:
        healthy = False
        checks["firestore"] = f"error: {str(exc)[:120]}"

    return flask.jsonify({
        "status": "ok" if healthy else "degraded",
        "checks": checks,
    }), (200 if healthy else 503)


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
    if not _is_valid_client_id(client_id):
        return flask.jsonify({
            "error": (
                f"Invalid client id '{client_id}'. Use lowercase letters, numbers, "
                "and hyphens only (e.g. 'the-home-office')."
            ),
            "code": "INVALID_CLIENT_ID",
        }), 400

    account_type = data.get("account_type", DEFAULT_ACCOUNT_TYPE)
    if account_type not in VALID_ACCOUNT_TYPES:
        return flask.jsonify({
            "error": f"account_type must be one of: {sorted(VALID_ACCOUNT_TYPES)}",
            "code": "INVALID_REQUEST",
        }), 400
    data["account_type"] = account_type

    upsert_client(client_id, data)
    return flask.jsonify({"id": client_id, "status": "created"}), 201


@app.route("/clients/<client_id>", methods=["PUT"])
def update_client_route(client_id: str):
    if not get_client(client_id):
        return flask.jsonify({"error": "Client not found", "code": "NOT_FOUND"}), 404

    data = flask.request.get_json(silent=True) or {}
    data.pop("id", None)
    if "account_type" in data and data["account_type"] not in VALID_ACCOUNT_TYPES:
        return flask.jsonify({
            "error": f"account_type must be one of: {sorted(VALID_ACCOUNT_TYPES)}",
            "code": "INVALID_REQUEST",
        }), 400
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

    # "both" passes VALID_API_SOURCES but connect can only store one credential
    # type at a time; previously it silently returned 200 storing nothing.
    if api_source == "both":
        return flask.jsonify({
            "error": "Connect one credential at a time: api_source must be 'sp_api' or 'ads_api'.",
            "code": "INVALID_REQUEST",
        }), 400

    # SP API stores a Secret Manager secret named with the client_id; an id with
    # characters outside [a-z0-9-] would make create_secret throw a raw 500.
    if api_source == "sp_api" and not _is_valid_client_id(client_id):
        return flask.jsonify({
            "error": (
                f"Invalid client id '{client_id}'. Use lowercase letters, numbers, "
                "and hyphens only."
            ),
            "code": "INVALID_CLIENT_ID",
        }), 400

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
            # An optional marketplace records this profile in the per-marketplace
            # ``ads_profile_ids`` map so multi-marketplace accounts can pull each
            # marketplace under its own Ads profile (a single account/region has
            # one profile per country). The default ``ads_profile_id`` is still
            # set/kept so single-marketplace flows and credential checks are
            # unaffected.
            marketplace = (data.get("marketplace") or "").strip()
            updates: dict = {}
            existing = get_client(client_id) or {}
            if marketplace:
                profile_map = dict(existing.get("ads_profile_ids") or {})
                profile_map[marketplace] = profile_id
                updates["ads_profile_ids"] = profile_map
                if not existing.get("ads_profile_id"):
                    updates["ads_profile_id"] = profile_id
            else:
                updates["ads_profile_id"] = profile_id
            upsert_client(client_id, updates)

        logger.info("Manual connect completed", extra={"client_id": client_id, "api_source": api_source})
        return flask.jsonify({"id": client_id, "api_source": api_source, "status": "connected"}), 200
    except Exception as exc:
        logger.exception("Manual connect failed", extra={"client_id": client_id})
        return flask.jsonify({"error": str(exc)[:200], "code": "INTERNAL_ERROR"}), 500


@app.route("/clients/<client_id>/sp-api-token", methods=["GET"])
def get_client_sp_api_token(client_id: str):
    """Return the stored SP API refresh token for a connected client."""
    client = get_client(client_id)
    if not client:
        return flask.jsonify({"error": "Client not found", "code": "NOT_FOUND"}), 404

    secret_name = client.get("sp_api_secret_name")
    if not secret_name:
        return flask.jsonify({
            "error": "SP API not connected for this client",
            "code": "INVALID_REQUEST",
        }), 404

    try:
        secret_data = _read_client_secret(secret_name)
        refresh_token = secret_data.get("refresh_token", "").strip()
        if not refresh_token:
            return flask.jsonify({
                "error": "SP API refresh token not found",
                "code": "INVALID_REQUEST",
            }), 404
        return flask.jsonify({"refresh_token": refresh_token}), 200
    except Exception:
        logger.exception("Failed to read SP API token", extra={"client_id": client_id})
        return flask.jsonify({"error": "Failed to retrieve token", "code": "INTERNAL_ERROR"}), 500


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

    missing: list[str] = []
    if not data.get("api_source"):
        missing.append("api_source")

    report_types = get_report_types(data)
    if not report_types:
        missing.append("report_types")

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

    validation_err = validate_report_types(data["api_source"], report_types)
    if validation_err:
        return flask.jsonify({"error": validation_err, "code": "INVALID_REQUEST"}), 400

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

    data.setdefault("name", "")
    data.setdefault("folder_name", "")
    data.setdefault("subfolder_strategy", "date")
    data.setdefault("reconciliation_days", [3, 7])
    data.setdefault("report_params", {})

    # Strip stray whitespace so "MTD Ads KPIs " and "MTD Ads KPIs" don't create
    # two separate (visually-identical) Drive folders.
    if isinstance(data.get("folder_name"), str):
        data["folder_name"] = data["folder_name"].strip()

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

    if "report_types" in data:
        effective_source = data.get("api_source", existing.get("api_source", ""))
        validation_err = validate_report_types(effective_source, data["report_types"])
        if validation_err:
            return flask.jsonify({"error": validation_err, "code": "INVALID_REQUEST"}), 400

    if "frequency" in data and data["frequency"] not in VALID_FREQUENCIES:
        return flask.jsonify({"error": f"frequency must be one of: {sorted(VALID_FREQUENCIES)}", "code": "INVALID_REQUEST"}), 400

    if "subfolder_strategy" in data and data["subfolder_strategy"] not in VALID_SUBFOLDER_STRATEGIES:
        return flask.jsonify({"error": f"subfolder_strategy must be one of: {sorted(VALID_SUBFOLDER_STRATEGIES)}", "code": "INVALID_REQUEST"}), 400

    if isinstance(data.get("folder_name"), str):
        data["folder_name"] = data["folder_name"].strip()

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

    api_source = schedule.get("api_source", "")
    clients_by_id: dict[str, dict] = {}
    for cid in client_ids:
        client = get_client(cid)
        if not client or not client.get("is_active", True):
            return flask.jsonify({"error": f"Client '{cid}' not found or inactive", "code": "NOT_FOUND"}), 404
        clients_by_id[cid] = client

    parent = get_workflow_parent()
    now = datetime.now(timezone.utc)
    job_ids: list[str] = []
    skipped_clients: list[str] = []
    errors: list[dict[str, str]] = []

    for cid in client_ids:
        if not client_has_credentials(clients_by_id[cid], api_source):
            skipped_clients.append(cid)
            logger.info("Skipping client — missing credentials", extra={"client_id": cid, "api_source": api_source})
            continue
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

    logger.info("Manual schedule trigger", extra={
        "schedule_id": schedule_id, "jobs": len(job_ids),
        "skipped_clients": skipped_clients, "errors": len(errors),
    })
    return flask.jsonify({
        "schedule_id": schedule_id,
        "status": "triggered",
        "jobs_started": len(job_ids),
        "job_ids": job_ids,
        "skipped_clients": skipped_clients,
        "errors": len(errors),
    }), 201


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

@app.route("/jobs", methods=["GET"])
def list_jobs_route():
    client_id = flask.request.args.get("client_id")
    status = flask.request.args.get("status")
    schedule_id = flask.request.args.get("schedule_id")
    execution_date = flask.request.args.get("execution_date")
    limit = min(int(flask.request.args.get("limit", "50")), 200)
    return flask.jsonify(_serialize(list_jobs(
        client_id=client_id,
        status=status,
        schedule_id=schedule_id,
        execution_date=execution_date,
        limit=limit,
    ))), 200


@app.route("/jobs/<job_id>", methods=["GET"])
def get_job_route(job_id: str):
    job = get_job(job_id)
    if not job:
        return flask.jsonify({"error": "Job not found", "code": "NOT_FOUND"}), 404
    return flask.jsonify(_serialize(job)), 200


def _retry_single_job(job_id: str) -> dict[str, Any]:
    """Core retry logic for a single failed job. Returns a result dict."""
    job = get_job(job_id)
    if not job:
        return {"job_id": job_id, "status": "error", "error": "Job not found"}
    if job.get("status") != "failed":
        return {"job_id": job_id, "status": "skipped", "error": "Not a failed job"}

    client = get_client(job["client_id"])
    if not client or not client.get("is_active", True):
        return {"job_id": job_id, "status": "skipped", "error": "Client not found or inactive"}

    effective_source = job["api_source"]
    marketplace = job["marketplace"]
    report_type = job["report_type"]

    now = datetime.now(timezone.utc)
    execution_date = job.get("execution_date") or marketplace_today(marketplace, now).isoformat()

    report_date_str = job.get("report_date", "")
    report_end_str = job.get("report_end_date", "")

    rt_params: dict[str, Any] = {}
    if report_date_str:
        if effective_source == "sp_api":
            rt_params["dataStartTime"] = report_date_str
            rt_params["dataEndTime"] = report_end_str or report_date_str
        else:
            rt_params["startDate"] = report_date_str
            rt_params["endDate"] = report_end_str or report_date_str

    new_job_id = create_job({
        "client_id": job["client_id"],
        "api_source": effective_source,
        "marketplace": marketplace,
        "report_type": report_type,
        "schedule_id": job.get("schedule_id"),
        "frequency": job.get("frequency", "on_demand"),
        "report_date": report_date_str,
        "report_end_date": report_end_str or None,
        "execution_date": execution_date,
        "trigger": "retry",
        "retry_of": job_id,
    })

    parent = get_workflow_parent()
    payload = build_payload(
        api_source=effective_source,
        client_id=job["client_id"],
        marketplace=marketplace,
        report_type=report_type,
        report_params=rt_params,
        job_id=new_job_id,
        frequency=job.get("frequency", "on_demand"),
        folder_name=job.get("folder_name", ""),
        subfolder_strategy=job.get("subfolder_strategy", "date"),
        schedule_id=job.get("schedule_id"),
        execution_date=execution_date,
        report_date=report_date_str,
    )

    try:
        launch_execution(parent, payload, new_job_id, error_phase="retry")
        return {"job_id": job_id, "status": "retried", "new_job_id": new_job_id}
    except Exception as exc:
        return {"job_id": job_id, "status": "error", "error": str(exc)[:200]}


@app.route("/jobs/<job_id>/retry", methods=["POST"])
def retry_job_route(job_id: str):
    """Re-launch a failed job with the same parameters."""
    result = _retry_single_job(job_id)

    if result["status"] == "retried":
        logger.info("Job retry launched", extra={"original_job": job_id, "new_job": result["new_job_id"]})
        return flask.jsonify({
            "status": "retried",
            "original_job_id": job_id,
            "new_job_id": result["new_job_id"],
        }), 201

    code_map = {"error": 500, "skipped": 400}
    http_code = code_map.get(result["status"], 500)
    error_code = "RETRY_FAILED" if result["status"] == "error" else "INVALID_STATE"
    if "not found" in result.get("error", "").lower():
        http_code = 404
        error_code = "NOT_FOUND"
    return flask.jsonify({"error": result["error"], "code": error_code}), http_code


@app.route("/jobs/batch-retry", methods=["POST"])
def batch_retry_route():
    """Retry multiple failed jobs in a single request."""
    import time as _time

    data = flask.request.get_json(silent=True) or {}
    job_ids = data.get("job_ids", [])

    if not isinstance(job_ids, list) or not job_ids:
        return flask.jsonify({"error": "Provide a non-empty job_ids array", "code": "INVALID_REQUEST"}), 400
    if len(job_ids) > 200:
        return flask.jsonify({"error": "Maximum 200 jobs per batch", "code": "INVALID_REQUEST"}), 400

    results: list[dict[str, Any]] = []
    for i, jid in enumerate(job_ids):
        result = _retry_single_job(jid)
        results.append(result)
        if i < len(job_ids) - 1:
            _time.sleep(0.5)

    retried = sum(1 for r in results if r["status"] == "retried")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    errored = sum(1 for r in results if r["status"] == "error")

    logger.info("Batch retry complete", extra={
        "total": len(job_ids), "retried": retried, "skipped": skipped, "errors": errored,
    })
    return flask.jsonify({
        "results": results,
        "summary": {"total": len(job_ids), "retried": retried, "skipped": skipped, "errors": errored},
    }), 200


# ---------------------------------------------------------------------------
# On-demand report trigger
# ---------------------------------------------------------------------------

@app.route("/on-demand", methods=["POST"])
def on_demand_route():
    """Trigger on-demand report downloads.

    Accepts ``report_types`` (list) and fans out one workflow per report type,
    mirroring the scheduler fan-out model.  Date params are translated into the
    correct API-specific keys (SP API uses dataStartTime/dataEndTime, Ads API
    uses startDate/endDate) per report type's effective source.
    """
    data = flask.request.get_json(silent=True) or {}

    report_types = get_report_types(data)
    missing: list[str] = []
    if not data.get("client_id"):
        missing.append("client_id")
    if not data.get("api_source"):
        missing.append("api_source")
    if not data.get("marketplace"):
        missing.append("marketplace")
    if not report_types:
        missing.append("report_types")
    if missing:
        return flask.jsonify({"error": f"Missing fields: {', '.join(missing)}", "code": "INVALID_REQUEST"}), 400

    api_source = data["api_source"]
    if api_source not in VALID_API_SOURCES:
        return flask.jsonify({"error": f"api_source must be one of: {sorted(VALID_API_SOURCES)}", "code": "INVALID_REQUEST"}), 400

    validation_err = validate_report_types(api_source, report_types)
    if validation_err:
        return flask.jsonify({"error": validation_err, "code": "INVALID_REQUEST"}), 400

    client = get_client(data["client_id"])
    if not client or not client.get("is_active", True):
        return flask.jsonify({"error": "Client not found or inactive", "code": "NOT_FOUND"}), 404

    from shared.firestore_utils import get_db
    active_statuses = ["pending", "requesting", "polling", "downloading", "uploading"]
    active_jobs = (
        get_db().collection("jobs")
        .where("client_id", "==", data["client_id"])
        .where("status", "in", active_statuses)
        .limit(11)
        .get()
    )
    if len(active_jobs) >= 10:
        return flask.jsonify({
            "error": "Too many active reports for this client. Please wait for current jobs to finish.",
            "code": "RATE_LIMITED",
        }), 429

    start_date = data.get("start_date", "")
    end_date = data.get("end_date", "")
    if not start_date or not end_date:
        return flask.jsonify({"error": "start_date and end_date are required", "code": "INVALID_REQUEST"}), 400
    try:
        sd = date.fromisoformat(start_date)
        ed = date.fromisoformat(end_date)
    except ValueError:
        return flask.jsonify({"error": "start_date and end_date must be valid YYYY-MM-DD dates", "code": "INVALID_REQUEST"}), 400
    if sd > ed:
        return flask.jsonify({"error": "start_date must be on or before end_date", "code": "INVALID_REQUEST"}), 400
    if (ed - sd).days > 60:
        return flask.jsonify({"error": "Date range cannot exceed 60 days", "code": "INVALID_REQUEST"}), 400

    parent = get_workflow_parent()
    report_params_map: dict = data.get("report_params", {})

    now = datetime.now(timezone.utc)
    execution_date = marketplace_today(data["marketplace"], now).isoformat()

    job_ids: list[str] = []
    errors: list[dict[str, str]] = []

    for report_type in report_types:
        effective_source = infer_api_source(report_type, api_source)

        base_rt_params: dict = {**report_params_map.get(report_type, {})}

        # Synchronous API operations (e.g. Replenishment / S&S) go through the
        # fetch_api path: one job for the full range, no report-option variants.
        if is_api_operation(report_type):
            op_params = {**base_rt_params, "dataStartTime": start_date, "dataEndTime": end_date}
            job_id = create_job({
                "client_id": data["client_id"],
                "api_source": effective_source,
                "marketplace": data["marketplace"],
                "report_type": report_type,
                "frequency": "on_demand",
                "execution_date": execution_date,
                "mode": "api_call",
            })
            payload = build_payload(
                api_source=effective_source,
                client_id=data["client_id"],
                marketplace=data["marketplace"],
                report_type=report_type,
                report_params=op_params,
                job_id=job_id,
                frequency="on_demand",
                folder_name=data.get("folder_name", ""),
                subfolder_strategy=data.get("subfolder_strategy", "date"),
                execution_date=execution_date,
                report_date=start_date,
                report_end_date=end_date if end_date != start_date else None,
                mode="api_call",
            )
            try:
                launch_execution(parent, payload, job_id, error_phase="trigger")
                job_ids.append(job_id)
            except Exception as exc:
                logger.exception("On-demand API-call workflow failed", extra={"job_id": job_id, "report_type": report_type})
                errors.append({"report_type": report_type, "error": str(exc)[:200]})
            continue

        for variant_params in _expand_report_option_variants(base_rt_params):
            rt_params = {**variant_params}
            if effective_source == "sp_api":
                if start_date:
                    rt_params["dataStartTime"] = start_date
                if end_date:
                    rt_params["dataEndTime"] = end_date
            else:
                if start_date:
                    rt_params["startDate"] = start_date
                if end_date:
                    rt_params["endDate"] = end_date

            job_id = create_job({
                "client_id": data["client_id"],
                "api_source": effective_source,
                "marketplace": data["marketplace"],
                "report_type": report_type,
                "frequency": "on_demand",
                "execution_date": execution_date,
            })

            payload = build_payload(
                api_source=effective_source,
                client_id=data["client_id"],
                marketplace=data["marketplace"],
                report_type=report_type,
                report_params=rt_params,
                job_id=job_id,
                frequency="on_demand",
                folder_name=data.get("folder_name", ""),
                subfolder_strategy=data.get("subfolder_strategy", "date"),
                execution_date=execution_date,
                report_date=start_date,
            )

            try:
                launch_execution(parent, payload, job_id, error_phase="trigger")
                job_ids.append(job_id)
            except Exception as exc:
                logger.exception("On-demand workflow failed", extra={"job_id": job_id, "report_type": report_type})
                errors.append({"report_type": report_type, "error": str(exc)[:200]})

    logger.info("On-demand trigger", extra={"jobs": len(job_ids), "errors": len(errors)})
    return flask.jsonify({
        "job_ids": job_ids,
        "jobs_started": len(job_ids),
        "errors": len(errors),
        "status": "started",
    }), 201


# ---------------------------------------------------------------------------
# Ads Report Config — expose available columns/config per report type
# ---------------------------------------------------------------------------

def _discover_ads_profiles(access_token: str, client_id: str) -> list[dict]:
    """Fetch /v2/profiles across all Ads API regional hosts (NA/EU/FE).

    Amazon partitions advertising profiles by region: NA profiles (US/CA/MX) live
    on advertising-api.amazon.com, EU on advertising-api-eu, FE (AU/SG/JP) on
    advertising-api-fe. A single /v2/profiles call only returns profiles for that
    one host, so AU/SG profiles never appear when only NA is queried.

    Queries every region with the same access token + ClientId header, tolerating
    per-region failures (a region erroring is logged and skipped rather than
    failing the whole request). Results are merged and deduped by profileId, and
    each profile is annotated with its source `_region` (na/eu/fe).
    """
    merged: dict[str, dict] = {}
    for region, host in ADS_API_ENDPOINTS.items():
        try:
            resp = requests.get(
                f"{host}/v2/profiles",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Amazon-Advertising-API-ClientId": client_id,
                },
                timeout=20,
            )
            resp.raise_for_status()
            region_profiles: list[dict] = resp.json()
        except Exception as exc:
            logger.warning(
                "Ads profiles fetch failed for region; continuing",
                extra={"region": region, "error": str(exc)[:200]},
            )
            continue

        for p in region_profiles:
            pid = str(p.get("profileId", ""))
            if not pid:
                continue
            p["_region"] = region
            merged.setdefault(pid, p)
        logger.info(
            "Ads profiles fetched for region",
            extra={"region": region, "count": len(region_profiles)},
        )

    return list(merged.values())


@app.route("/ads-profiles", methods=["GET"])
def ads_profiles_list():
    """Return all Amazon Ads profiles visible to the shared app credentials.

    Performs a fresh LWA token exchange and calls GET /v2/profiles against every
    regional host (NA/EU/FE) so profiles in all regions (incl. AU/SG on FE) are
    returned. Cross-references Firestore clients by ads_profile_id.
    """
    try:
        app_creds = _read_app_secret("ads_api")
    except Exception as exc:
        logger.exception("Failed to read Ads API app credentials")
        return flask.jsonify({
            "error": f"Could not load Ads API app credentials: {str(exc)[:200]}",
            "code": "CREDENTIALS_MISSING",
        }), 500

    try:
        token_resp = requests.post(LWA_TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": app_creds["refresh_token"],
            "client_id": app_creds["client_id"],
            "client_secret": app_creds["client_secret"],
        }, timeout=15)
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]
    except Exception as exc:
        logger.exception("LWA token exchange failed for ads-profiles")
        return flask.jsonify({
            "error": f"Token exchange failed: {str(exc)[:200]}",
            "code": "TOKEN_EXCHANGE_FAILED",
        }), 500

    profiles = _discover_ads_profiles(access_token, app_creds["client_id"])

    clients = list_clients()
    profile_id_to_client: dict[str, str] = {}
    for c in clients:
        pid = c.get("ads_profile_id")
        if pid:
            profile_id_to_client[str(pid)] = c["id"]

    for p in profiles:
        pid_str = str(p.get("profileId", ""))
        linked_client = profile_id_to_client.get(pid_str)
        if linked_client:
            p["_linked_client_id"] = linked_client

    logger.info("Ads profiles fetched", extra={"count": len(profiles)})
    return flask.jsonify(profiles), 200


def _discover_ads_profiles_via_app(app_creds: dict[str, str]) -> list[dict]:
    """Mint an Ads access token from the shared Ads API app credentials and list
    every advertising profile the app can see (across NA/EU/FE).

    The Ads Profile ID comes from the Amazon Ads API (GET /v2/profiles), which can
    only be queried with the Ads API *application's* own LWA credentials. A single
    Advertising API application is authorized across all the managed advertiser
    accounts, so `/v2/profiles` returns the full set of profiles in one call — the
    same mechanism `/ads-profiles` and the OAuth callback already use.
    """
    token_resp = requests.post(LWA_TOKEN_URL, data={
        "grant_type": "refresh_token",
        "refresh_token": app_creds["refresh_token"],
        "client_id": app_creds["client_id"],
        "client_secret": app_creds["client_secret"],
    }, timeout=15)
    token_resp.raise_for_status()
    access_token = token_resp.json()["access_token"]

    return _discover_ads_profiles(access_token, app_creds["client_id"])


@app.route("/sp-api-accounts", methods=["GET"])
def sp_api_accounts_list():
    """List every account with an active SP-API connection, with its Ads profiles.

    Fixes the chicken-and-egg gap where the Admin page only surfaced accounts that
    already had an Ads profile linked: an SP-API-only account could never appear to
    have its Ads Profile ID read. This widens the listing to all SP-API-connected
    Firestore clients and shows the available Ads Profile ID(s) so an operator can
    pick the right one and connect the Ads API.

    The available profiles are discovered once via the shared Ads API *app*
    credentials (the only LWA security profile registered as an Advertising API
    application). A client's SP-API refresh token belongs to a different LWA app
    and cannot mint an Ads API access token, so attempting a per-account exchange
    with it failed for every account ("Could not fetch profiles"). If the shared
    discovery fails the accounts still list, annotated with `ads_profiles_error`,
    so the page is never fully blocked.
    """
    active = flask.request.args.get("active") == "true"
    clients = list_clients(active_only=active)
    sp_clients = [c for c in clients if c.get("sp_api_secret_name")]

    available_profiles: list[dict] = []
    profiles_error: str | None = None
    # Only hit Amazon when there's at least one account to annotate.
    if sp_clients:
        try:
            app_creds = _read_app_secret("ads_api")
            available_profiles = _discover_ads_profiles_via_app(app_creds)
        except Exception as exc:
            profiles_error = str(exc)[:200]
            logger.warning("Could not fetch Ads profiles for sp-api-accounts",
                           extra={"error": profiles_error})

    accounts: list[dict[str, Any]] = []
    for c in sp_clients:
        accounts.append({
            "id": c["id"],
            "name": c.get("name", c["id"]),
            "marketplaces": c.get("marketplaces", []),
            "sp_api_connected": True,
            "ads_profile_id": c.get("ads_profile_id"),
            "ads_profiles": available_profiles,
            "ads_profiles_error": profiles_error,
        })

    logger.info("SP-API accounts listed",
                extra={"count": len(accounts), "profile_count": len(available_profiles)})
    return flask.jsonify(_serialize(accounts)), 200


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

SP_API_SELLER_CENTRAL_URLS: dict[str, str] = {
    "na": "https://sellercentral.amazon.com",
    "eu": "https://sellercentral-europe.amazon.com",
    "fe": "https://sellercentral.amazon.co.jp",
}
# Vendor Central authorization hosts (1P). Vendor accounts grant the app from
# Vendor Central rather than Seller Central; the consent path is identical.
SP_API_VENDOR_CENTRAL_URLS: dict[str, str] = {
    "na": "https://vendorcentral.amazon.com",
    "eu": "https://vendorcentral.amazon.co.uk",
    "fe": "https://vendorcentral.amazon.co.jp",
}
ADS_API_AUTH_URL = "https://www.amazon.com/ap/oa"

_OAUTH_STATE_TTL_SECONDS = 600  # 10 minutes


def _get_sm() -> secretmanager.SecretManagerServiceClient:
    global _sm_client
    if _sm_client is None:
        _sm_client = secretmanager.SecretManagerServiceClient()
    return _sm_client


def _save_oauth_state(state: str, data: dict[str, str]) -> None:
    """Persist OAuth state to Firestore so any Cloud Function instance can read it."""
    from shared.firestore_utils import get_db
    get_db().collection("_oauth_states").document(state).set({
        **data,
        "created_at": datetime.now(timezone.utc),
    })


def _pop_oauth_state(state: str) -> dict[str, str] | None:
    """Atomically retrieve and delete an OAuth state from Firestore."""
    from shared.firestore_utils import get_db
    ref = get_db().collection("_oauth_states").document(state)
    doc = ref.get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    created = data.pop("created_at", None)
    if created:
        age = (datetime.now(timezone.utc) - created.replace(tzinfo=timezone.utc)).total_seconds()
        if age > _OAUTH_STATE_TTL_SECONDS:
            ref.delete()
            return None
    ref.delete()
    return data


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
    secret_name = f"kalilos-{env}-{prefix}-{_secret_safe_segment(client_id)}"
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


def _read_client_secret(secret_name: str) -> dict[str, str]:
    """Read a client-level secret from Secret Manager."""
    project = get_project()
    name = f"projects/{project}/secrets/{secret_name}/versions/latest"
    resp = _get_sm().access_secret_version(name=name)
    return json.loads(resp.payload.data.decode("utf-8"))


def _get_oauth_redirect_uri() -> str:
    return os.environ.get("OAUTH_REDIRECT_URI", "")


def _get_frontend_url() -> str:
    return os.environ.get("FRONTEND_URL", "http://localhost:5173")


@app.route("/oauth/sp-api/authorize", methods=["GET"])
def oauth_sp_api_authorize():
    """Step 1: Redirect seller to Seller Central authorization consent page.

    Per Amazon docs, the authorization URI only takes application_id, state,
    and optionally version=beta.  No redirect_uri here — Amazon uses the one
    registered in the Developer Application settings.
    """
    requested_client_id = flask.request.args.get("client_id")
    if not requested_client_id:
        return flask.jsonify({"error": "Missing client_id", "code": "INVALID_REQUEST"}), 400

    client = resolve_client(requested_client_id)
    if not client:
        return flask.jsonify({"error": "Client not found", "code": "NOT_FOUND"}), 404

    # Always proceed with the resolved client's canonical id so OAuth state and
    # the credential stored in the callback map to the correct client record.
    client_id = client["id"]

    app_creds = _read_app_secret("sp_api")
    application_id = app_creds.get("app_id", "")

    region = flask.request.args.get("region", "na")
    # Vendor (1P) accounts authorize from Vendor Central; sellers from Seller Central.
    account_type = (client.get("account_type") or DEFAULT_ACCOUNT_TYPE).lower()
    if account_type == "vendor":
        central_urls = SP_API_VENDOR_CENTRAL_URLS
    else:
        central_urls = SP_API_SELLER_CENTRAL_URLS
    central = central_urls.get(region, central_urls["na"])

    state = secrets.token_urlsafe(32)
    _save_oauth_state(state, {
        "client_id": client_id,
        "api_source": "sp_api",
        "account_type": account_type,
    })

    params: dict[str, str] = {
        "application_id": application_id,
        "state": state,
    }
    # Draft (unpublished) apps MUST add version=beta or Amazon rejects consent
    # with error MD5100 ("...add the version=beta parameter..."). A single SP-API
    # app publishes its Seller and Vendor surfaces independently: this app is
    # published in the Seller appstore (draft=false) but its Vendor authorization
    # is still in draft, so vendor consent needs version=beta even though seller
    # consent does not. Track the vendor draft state separately (defaulting to
    # draft) instead of inheriting the seller `draft` flag — otherwise vendors
    # silently get no version=beta and every Vendor Central connect fails.
    if account_type == "vendor":
        is_draft = app_creds.get("vendor_draft", True)
    else:
        is_draft = app_creds.get("draft", True)
    if is_draft:
        params["version"] = "beta"

    auth_url = f"{central}/apps/authorize/consent?{urlencode(params)}"
    logger.info("SP API OAuth authorize redirect", extra={
        "client_id": client_id,
        "requested_client_id": requested_client_id,
        "application_id": application_id,
        "account_type": account_type,
        "draft": is_draft,
        "central": central,
        "auth_url": auth_url,
    })
    return flask.redirect(auth_url)


@app.route("/oauth/sp-api/login", methods=["GET"])
def oauth_sp_api_login():
    """Login URI — Amazon redirects the seller here during the authorization workflow.

    Amazon sends: amazon_callback_uri, amazon_state, selling_partner_id, version (optional).
    We generate our own state, save it, and redirect the seller back to the
    amazon_callback_uri with our state + redirect_uri.
    """
    amazon_callback_uri = flask.request.args.get("amazon_callback_uri", "")
    amazon_state = flask.request.args.get("amazon_state", "")
    selling_partner_id = flask.request.args.get("selling_partner_id", "")
    version = flask.request.args.get("version", "")

    if not amazon_callback_uri or not amazon_state:
        logger.warning("SP API login URI missing required params", extra={
            "amazon_callback_uri": bool(amazon_callback_uri),
            "amazon_state": bool(amazon_state),
        })
        return flask.jsonify({"error": "Missing amazon_callback_uri or amazon_state"}), 400

    state = secrets.token_urlsafe(32)
    _save_oauth_state(state, {
        "api_source": "sp_api",
        "selling_partner_id": selling_partner_id,
        "client_id": "",
    })

    params: dict[str, str] = {
        "amazon_state": amazon_state,
        "state": state,
        "redirect_uri": _get_oauth_redirect_uri(),
    }
    if version:
        params["version"] = version

    callback_url = f"{amazon_callback_uri}?{urlencode(params)}"
    logger.info("SP API login URI → redirecting to Amazon callback", extra={
        "selling_partner_id": selling_partner_id,
        "redirect_uri": params["redirect_uri"],
    })
    return flask.redirect(callback_url)


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
    _save_oauth_state(state, {"client_id": client_id, "api_source": "ads_api"})

    redirect_uri = _get_oauth_redirect_uri()
    params = {
        "client_id": app_creds["client_id"],
        "scope": "advertising::campaign_management",
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "state": state,
    }
    auth_url = f"{ADS_API_AUTH_URL}?{urlencode(params)}"
    logger.info("Ads API OAuth authorize redirect", extra={
        "client_id": client_id,
        "redirect_uri": redirect_uri,
    })
    return flask.redirect(auth_url)


@app.route("/oauth/callback", methods=["GET"])
def oauth_callback():
    # NB: avoid the reserved LogRecord key "args" in extra (it raises KeyError in
    # logging.makeRecord and 500s the whole callback); use a safe field name.
    logger.info("OAuth callback received", extra={
        "query_args": dict(flask.request.args),
    })

    error = flask.request.args.get("error")
    error_description = flask.request.args.get("error_description", "")
    if error:
        logger.warning("OAuth error from Amazon", extra={"error": error, "description": error_description})
        msg = error_description or error
        return flask.redirect(f"{_get_frontend_url()}/clients?oauth=error&message={msg}")

    code = flask.request.args.get("spapi_oauth_code") or flask.request.args.get("code")
    state = flask.request.args.get("state", "")
    selling_partner_id = flask.request.args.get("selling_partner_id", "")

    if not code:
        logger.warning("OAuth callback missing authorization code", extra={"state": state})
        return flask.redirect(f"{_get_frontend_url()}/clients?oauth=error&message=missing_code")

    state_data = _pop_oauth_state(state)
    if not state_data:
        logger.warning("OAuth callback invalid or expired state", extra={"state": state[:16]})
        return flask.redirect(f"{_get_frontend_url()}/clients?oauth=error&message=invalid_state")

    client_id = state_data.get("client_id", "")
    api_source = state_data["api_source"]

    if not client_id and selling_partner_id:
        client_id = selling_partner_id
        logger.info("Using selling_partner_id as client_id for login-URI flow", extra={
            "selling_partner_id": selling_partner_id,
        })

    try:
        app_creds = _read_app_secret(api_source)

        token_resp = requests.post(LWA_TOKEN_URL, data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _get_oauth_redirect_uri(),
            "client_id": app_creds["client_id"],
            "client_secret": app_creds["client_secret"],
        }, timeout=15)

        if not token_resp.ok:
            logger.error("LWA token exchange failed", extra={
                "status": token_resp.status_code,
                "body": token_resp.text[:500],
                "client_id": client_id,
            })
            token_resp.raise_for_status()

        tokens = token_resp.json()
        refresh_token = tokens["refresh_token"]

        if api_source == "sp_api":
            secret_data: dict[str, str] = {"refresh_token": refresh_token}
            if selling_partner_id:
                secret_data["selling_partner_id"] = selling_partner_id
            secret_name = _store_client_secret(client_id, api_source, secret_data)
            update_fields_sp: dict[str, Any] = {"sp_api_secret_name": secret_name}
            if selling_partner_id:
                update_fields_sp["selling_partner_id"] = selling_partner_id
            upsert_client(client_id, update_fields_sp)

        elif api_source == "ads_api":
            access_token = tokens["access_token"]
            update_fields_ads: dict[str, Any] = {}
            try:
                profiles = _discover_ads_profiles(access_token, app_creds["client_id"])
                if profiles:
                    update_fields_ads["ads_profile_id"] = str(profiles[0]["profileId"])
                    logger.info("Ads profile discovered", extra={
                        "client_id": client_id,
                        "profile_count": len(profiles),
                        "profile_id": profiles[0]["profileId"],
                        "profile_region": profiles[0].get("_region"),
                        "regions": sorted({p.get("_region") for p in profiles}),
                    })
            except Exception as exc:
                logger.warning("Profile discovery failed during OAuth", extra={"error": str(exc)})
            upsert_client(client_id, update_fields_ads)

        logger.info("OAuth completed successfully", extra={
            "client_id": client_id,
            "api_source": api_source,
            "selling_partner_id": selling_partner_id,
        })
        return flask.redirect(f"{_get_frontend_url()}/clients?oauth=success&api_source={api_source}&client_id={client_id}")

    except Exception as exc:
        logger.exception("OAuth token exchange failed", extra={"client_id": client_id, "api_source": api_source})
        return flask.redirect(f"{_get_frontend_url()}/clients?oauth=error&message={str(exc)[:100]}")


@app.route("/oauth/debug", methods=["GET"])
def oauth_debug():
    """Return the OAuth configuration for verification (no secrets exposed)."""
    redirect_uri = _get_oauth_redirect_uri()
    frontend_url = _get_frontend_url()
    env = get_environment()
    api_url = redirect_uri.rsplit("/oauth/callback", 1)[0] if redirect_uri else ""
    return flask.jsonify({
        "environment": env,
        "redirect_uri": redirect_uri,
        "login_uri": f"{api_url}/oauth/sp-api/login" if api_url else "",
        "frontend_url": frontend_url,
        "sp_api_seller_central_urls": SP_API_SELLER_CENTRAL_URLS,
        "ads_api_authorize_base": ADS_API_AUTH_URL,
        "lwa_token_url": LWA_TOKEN_URL,
    }), 200


@app.route("/oauth/status/<client_id>", methods=["GET"])
def oauth_status(client_id: str):
    client = get_client(client_id)
    if not client:
        return flask.jsonify({"error": "Client not found", "code": "NOT_FOUND"}), 404

    return flask.jsonify({
        "client_id": client_id,
        "account_type": client.get("account_type", DEFAULT_ACCOUNT_TYPE),
        "sp_api_connected": bool(client.get("sp_api_secret_name")),
        "ads_api_connected": bool(client.get("ads_profile_id")),
    }), 200


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@app.route("/events", methods=["GET"])
def list_events_route():
    return flask.jsonify(_serialize(list_events())), 200


@app.route("/events/<event_id>", methods=["GET"])
def get_event_route(event_id: str):
    event = get_event(event_id)
    if not event:
        return flask.jsonify({"error": "Event not found", "code": "NOT_FOUND"}), 404
    return flask.jsonify(_serialize(event)), 200


def _normalize_manual_ads(raw: Any) -> dict[str, dict[str, dict[str, float]]]:
    """Validate and coerce operator-supplied prior-year ads.

    Expected shape: ``{marketplace: {"YYYY-MM-DD": {"spend": num, "ppc_sales": num}}}``.
    These figures back-fill the midnight recap's year-over-year ads metrics when
    Amazon Ads can no longer serve that history (its API retains only ~95 days).
    Raises ``ValueError`` with an operator-readable message on malformed input.
    """
    if not isinstance(raw, dict):
        raise ValueError("manual_ads must be an object keyed by marketplace")

    cleaned: dict[str, dict[str, dict[str, float]]] = {}
    for marketplace, by_date in raw.items():
        if not isinstance(by_date, dict):
            raise ValueError(f"manual_ads['{marketplace}'] must be an object keyed by date")
        cleaned_dates: dict[str, dict[str, float]] = {}
        for day, figures in by_date.items():
            try:
                date.fromisoformat(day)
            except (ValueError, TypeError):
                raise ValueError(f"manual_ads['{marketplace}'] date '{day}' must be YYYY-MM-DD")
            if not isinstance(figures, dict):
                raise ValueError(f"manual_ads['{marketplace}']['{day}'] must be an object")
            try:
                spend = float(figures.get("spend", 0) or 0)
                ppc_sales = float(figures.get("ppc_sales", 0) or 0)
            except (ValueError, TypeError):
                raise ValueError(
                    f"manual_ads['{marketplace}']['{day}'] spend/ppc_sales must be numbers"
                )
            if spend < 0 or ppc_sales < 0:
                raise ValueError(
                    f"manual_ads['{marketplace}']['{day}'] spend/ppc_sales must be non-negative"
                )
            cleaned_dates[day] = {"spend": spend, "ppc_sales": ppc_sales}
        if cleaned_dates:
            cleaned[marketplace] = cleaned_dates
    return cleaned


@app.route("/events", methods=["POST"])
def create_event_route():
    data = flask.request.get_json(silent=True) or {}
    if not data.get("name"):
        return flask.jsonify({"error": "Missing 'name'", "code": "INVALID_REQUEST"}), 400
    if not data.get("start_date"):
        return flask.jsonify({"error": "Missing 'start_date'", "code": "INVALID_REQUEST"}), 400
    if not data.get("end_date"):
        return flask.jsonify({"error": "Missing 'end_date'", "code": "INVALID_REQUEST"}), 400

    try:
        sd = date.fromisoformat(data["start_date"])
        ed = date.fromisoformat(data["end_date"])
    except ValueError:
        return flask.jsonify({"error": "start_date and end_date must be YYYY-MM-DD", "code": "INVALID_REQUEST"}), 400
    if sd > ed:
        return flask.jsonify({"error": "start_date must be on or before end_date", "code": "INVALID_REQUEST"}), 400

    if "manual_ads" in data:
        try:
            data["manual_ads"] = _normalize_manual_ads(data["manual_ads"])
        except ValueError as exc:
            return flask.jsonify({"error": str(exc), "code": "INVALID_REQUEST"}), 400

    event_id = create_event(data)
    return flask.jsonify({"id": event_id, "status": "created"}), 201


@app.route("/events/<event_id>", methods=["PUT"])
def update_event_route(event_id: str):
    if not get_event(event_id):
        return flask.jsonify({"error": "Event not found", "code": "NOT_FOUND"}), 404
    data = flask.request.get_json(silent=True) or {}
    data.pop("id", None)
    if "manual_ads" in data:
        try:
            data["manual_ads"] = _normalize_manual_ads(data["manual_ads"])
        except ValueError as exc:
            return flask.jsonify({"error": str(exc), "code": "INVALID_REQUEST"}), 400
    update_event(event_id, data)
    return flask.jsonify({"id": event_id, "status": "updated"}), 200


@app.route("/events/<event_id>", methods=["DELETE"])
def delete_event_route(event_id: str):
    fs_delete_event(event_id)
    return flask.jsonify({"id": event_id, "status": "deleted"}), 200


@app.route("/events/<event_id>/activate", methods=["POST"])
def activate_event_route(event_id: str):
    """Manually activate an event ('Go Live')."""
    event = get_event(event_id)
    if not event:
        return flask.jsonify({"error": "Event not found", "code": "NOT_FOUND"}), 404
    if event.get("status") == "completed":
        return flask.jsonify({"error": "Cannot activate a completed event", "code": "INVALID_STATE"}), 400
    update_event(event_id, {
        "status": "live",
        "manually_activated": True,
        "activated_at": datetime.now(timezone.utc),
    })
    return flask.jsonify({"id": event_id, "status": "live"}), 200


@app.route("/events/<event_id>/deactivate", methods=["POST"])
def deactivate_event_route(event_id: str):
    """Manually end an event ('End Event')."""
    event = get_event(event_id)
    if not event:
        return flask.jsonify({"error": "Event not found", "code": "NOT_FOUND"}), 404
    if event.get("status") != "live":
        return flask.jsonify({"error": "Event is not live", "code": "INVALID_STATE"}), 400
    update_event(event_id, {"status": "completed"})
    return flask.jsonify({"id": event_id, "status": "completed"}), 200


# ---------------------------------------------------------------------------
# Bot Configs
# ---------------------------------------------------------------------------

@app.route("/bot-configs", methods=["GET"])
def list_bot_configs_route():
    return flask.jsonify(_serialize(list_bot_configs())), 200


@app.route("/bot-configs/<client_id>", methods=["GET"])
def get_bot_config_route(client_id: str):
    config = get_bot_config(client_id)
    if not config:
        return flask.jsonify({"error": "Bot config not found", "code": "NOT_FOUND"}), 404
    return flask.jsonify(_serialize(config)), 200


@app.route("/bot-configs/<client_id>", methods=["PUT"])
def upsert_bot_config_route(client_id: str):
    if not get_client(client_id):
        return flask.jsonify({"error": "Client not found", "code": "NOT_FOUND"}), 404
    data = flask.request.get_json(silent=True) or {}
    data.pop("id", None)
    data["client_id"] = client_id
    upsert_bot_config(client_id, data)
    return flask.jsonify({"id": client_id, "status": "updated"}), 200


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
