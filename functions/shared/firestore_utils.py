"""Firestore operations for clients, schedules, and jobs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from google.cloud import firestore

logger = logging.getLogger(__name__)

_db: firestore.Client | None = None


def get_db() -> firestore.Client:
    global _db
    if _db is None:
        _db = firestore.Client()
    return _db


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def get_client(client_id: str) -> dict[str, Any] | None:
    doc = get_db().collection("clients").document(client_id).get()
    if not doc.exists:
        return None
    return {"id": doc.id, **doc.to_dict()}


def resolve_client(identifier: str) -> dict[str, Any] | None:
    """Resolve a client from a selected identifier to its canonical record.

    The Connect SP API (OAuth) flow passes a client identifier that must map to
    exactly one client document. A plain ``get_client`` only matches the exact
    Firestore document id, which is brittle: an identifier that differs from the
    stored id by casing/whitespace resolves to nothing ("Client not found"),
    and any looser matching risks connecting the wrong account.

    Resolution order (first match wins):
      1. Exact document-id match.
      2. Case-insensitive document-id match.
      3. Trimmed, case-insensitive display-name match.

    The fallbacks (2 and 3) only resolve when **exactly one** client matches.
    Ambiguous matches return ``None`` so the caller never silently authorizes
    the wrong client.
    """
    if not identifier:
        return None

    exact = get_client(identifier)
    if exact:
        return exact

    needle = identifier.strip().casefold()
    if not needle:
        return None

    clients = list_clients()

    id_matches = [c for c in clients if str(c.get("id", "")).strip().casefold() == needle]
    if len(id_matches) == 1:
        return id_matches[0]

    name_matches = [c for c in clients if str(c.get("name", "")).strip().casefold() == needle]
    if len(name_matches) == 1:
        return name_matches[0]

    return None


def list_clients(active_only: bool = False) -> list[dict[str, Any]]:
    ref = get_db().collection("clients")
    if active_only:
        ref = ref.where("is_active", "==", True)
    return [{"id": doc.id, **doc.to_dict()} for doc in ref.stream()]


def upsert_client(client_id: str, data: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    data["updated_at"] = now
    doc_ref = get_db().collection("clients").document(client_id)
    if not doc_ref.get().exists:
        data.setdefault("created_at", now)
        data.setdefault("is_active", True)
    doc_ref.set(data, merge=True)


def delete_client(client_id: str) -> None:
    get_db().collection("clients").document(client_id).delete()


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------

def get_schedule(schedule_id: str) -> dict[str, Any] | None:
    doc = get_db().collection("schedules").document(schedule_id).get()
    if not doc.exists:
        return None
    return {"id": doc.id, **doc.to_dict()}


def list_schedules(
    client_id: str | None = None,
    active_only: bool = False,
) -> list[dict[str, Any]]:
    ref = get_db().collection("schedules")
    if client_id:
        ref = ref.where("client_ids", "array_contains", client_id)
    if active_only:
        ref = ref.where("is_active", "==", True)
    return [{"id": doc.id, **doc.to_dict()} for doc in ref.stream()]


def list_due_schedules(now: datetime | None = None) -> list[dict[str, Any]]:
    """Active schedules whose next_run_at <= now."""
    if now is None:
        now = datetime.now(timezone.utc)
    return [
        {"id": doc.id, **doc.to_dict()}
        for doc in (
            get_db()
            .collection("schedules")
            .where("is_active", "==", True)
            .where("next_run_at", "<=", now)
            .stream()
        )
    ]


def create_schedule(data: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    data.setdefault("is_active", True)
    data.setdefault("created_at", now)
    doc_ref = get_db().collection("schedules").document()
    doc_ref.set(data)
    return doc_ref.id


def update_schedule(schedule_id: str, data: dict[str, Any]) -> None:
    get_db().collection("schedules").document(schedule_id).update(data)


def update_schedule_run_times(
    schedule_id: str,
    last_run_at: datetime,
    next_run_at: datetime,
) -> None:
    get_db().collection("schedules").document(schedule_id).update({
        "last_run_at": last_run_at,
        "next_run_at": next_run_at,
    })


def _schedule_is_due(data: dict[str, Any] | None, now: datetime) -> bool:
    """Whether a schedule doc is still due (``next_run_at`` <= ``now``).

    A missing ``next_run_at`` is treated as due (it matched the due query and
    should be claimed). Firestore returns timezone-aware datetimes, but we
    defensively coerce naive values to UTC so the comparison never raises.
    """
    if data is None:
        return False
    next_run_at = data.get("next_run_at")
    if next_run_at is None:
        return True
    if isinstance(next_run_at, datetime) and next_run_at.tzinfo is None:
        next_run_at = next_run_at.replace(tzinfo=timezone.utc)
    return next_run_at <= now


def claim_due_schedule(
    schedule_id: str,
    now: datetime,
    next_run_at: datetime,
) -> bool:
    """Atomically claim a due schedule for this run, advancing its run times.

    The scheduler reads all schedules whose ``next_run_at <= now`` and fans out
    workflows for each. Because Cloud Scheduler delivers at-least-once and a
    slow run can overlap the next cron tick, two scheduler invocations could
    both observe the same schedule as due and fan out duplicate workflows —
    producing duplicate report files in Drive.

    To make the claim exactly-once, we advance ``next_run_at`` inside a
    Firestore transaction that first re-reads the document and only proceeds if
    it is *still* due. The first invocation wins and fans out; any concurrent or
    retried invocation sees the advanced ``next_run_at`` and returns ``False``,
    so it skips the schedule.

    Returns ``True`` if this caller claimed the schedule, ``False`` otherwise.
    """
    db = get_db()
    ref = db.collection("schedules").document(schedule_id)

    # Read ``firestore.transactional`` at call time so tests can patch it.
    transactional = firestore.transactional

    @transactional
    def _claim(transaction) -> bool:
        snapshot = ref.get(transaction=transaction)
        data = snapshot.to_dict() if snapshot.exists else None
        if not _schedule_is_due(data, now):
            return False
        transaction.update(ref, {"last_run_at": now, "next_run_at": next_run_at})
        return True

    try:
        return bool(_claim(db.transaction()))
    except Exception:
        # A transaction contention loss (or transient Firestore error) means we
        # could not safely claim the schedule; treat it as "someone else has
        # it" so we never double-fan-out on error.
        logger.warning(
            "Failed to claim schedule — skipping to avoid duplicate fan-out",
            extra={"schedule_id": schedule_id},
        )
        return False


def delete_schedule(schedule_id: str) -> None:
    get_db().collection("schedules").document(schedule_id).delete()


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def create_job(job_data: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    job_data.setdefault("status", "pending")
    job_data.setdefault("started_at", now)
    job_data.setdefault("retry_count", 0)
    job_data.setdefault("poll_count", 0)
    doc_ref = get_db().collection("jobs").document()
    doc_ref.set(job_data)
    return doc_ref.id


_JOB_DEDUPE_COLLECTION = "_job_launch_dedupe"


def _job_dedupe_doc_id(dedupe_key: str) -> str:
    """Deterministic, Firestore-safe document id for a launch dedupe key.

    Keys can be long and contain characters Firestore forbids in ids (e.g.
    slashes), so we hash them. Collisions are astronomically unlikely with
    SHA-256 and would only ever suppress a genuinely-identical launch.
    """
    import hashlib

    return hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()


def try_claim_job_launch(dedupe_key: str, metadata: dict[str, Any] | None = None) -> bool:
    """Atomically claim a launch dedupe key. Returns ``True`` if newly claimed.

    Idempotency guard for workflow launches: a single logical pull (identified
    by schedule, execution date, client, marketplace, report type and date
    range) should only ever be launched once. We record the claim with
    Firestore's atomic ``document.create()``; a duplicate launch attempt for the
    same key fails with ``AlreadyExists`` and returns ``False``, so the caller
    can skip it and avoid producing a duplicate Drive file.

    Fails open: if Firestore is unavailable the launch proceeds (returns
    ``True``) rather than silently dropping a report.
    """
    from google.api_core.exceptions import AlreadyExists

    ref = (
        get_db()
        .collection(_JOB_DEDUPE_COLLECTION)
        .document(_job_dedupe_doc_id(dedupe_key))
    )
    payload: dict[str, Any] = {
        "dedupe_key": dedupe_key,
        "created_at": datetime.now(timezone.utc),
    }
    if metadata:
        payload.update(metadata)
    try:
        ref.create(payload)
        return True
    except AlreadyExists:
        logger.info(
            "Skipping duplicate workflow launch",
            extra={"dedupe_key": dedupe_key, "phase": "launch_dedupe"},
        )
        return False
    except Exception:
        logger.warning(
            "Launch dedupe check failed (non-fatal) — proceeding with launch",
            extra={"dedupe_key": dedupe_key, "phase": "launch_dedupe"},
        )
        return True


def get_job(job_id: str) -> dict[str, Any] | None:
    doc = get_db().collection("jobs").document(job_id).get()
    if not doc.exists:
        return None
    return {"id": doc.id, **doc.to_dict()}


def update_job(job_id: str, updates: dict[str, Any]) -> None:
    get_db().collection("jobs").document(job_id).update(updates)


def update_job_status(job_id: str, status: str, **extra: Any) -> None:
    updates: dict[str, Any] = {"status": status, **extra}
    if status in ("completed", "failed"):
        updates["completed_at"] = datetime.now(timezone.utc)
    update_job(job_id, updates)

    if status in ("completed", "failed"):
        _maybe_update_schedule_run_status(job_id)


_TERMINAL_STATUSES = {"completed", "failed"}


def _maybe_update_schedule_run_status(job_id: str) -> None:
    """When all sibling jobs (same schedule + execution_date) are terminal,
    compute an aggregate status and write it back on the schedule document.
    Also stores the Drive folder ID from the first completed sibling for 2B."""
    job = get_job(job_id)
    if not job:
        return

    schedule_id = job.get("schedule_id")
    execution_date = job.get("execution_date")
    if not schedule_id or not execution_date:
        return

    siblings = list(
        get_db()
        .collection("jobs")
        .where("schedule_id", "==", schedule_id)
        .where("execution_date", "==", execution_date)
        .stream()
    )
    if not siblings:
        return

    sibling_dicts = [doc.to_dict() for doc in siblings]
    if not all(d.get("status") in _TERMINAL_STATUSES for d in sibling_dicts):
        return

    completed = sum(1 for d in sibling_dicts if d["status"] == "completed")
    failed = sum(1 for d in sibling_dicts if d["status"] == "failed")
    total = len(sibling_dicts)

    if failed == total:
        agg = "failed"
    elif failed > 0:
        agg = "partial"
    else:
        agg = "success"

    folder_id = next(
        (d["gdrive_folder_id"] for d in sibling_dicts
         if d.get("status") == "completed" and d.get("gdrive_folder_id")),
        None,
    )

    patch: dict[str, Any] = {
        "last_run_status": agg,
        "last_run_job_count": {"completed": completed, "failed": failed, "total": total},
    }
    if folder_id:
        patch["last_drive_folder_id"] = folder_id

    try:
        update_schedule(schedule_id, patch)
    except Exception:
        logger.warning(
            "Failed to update schedule run status",
            extra={"schedule_id": schedule_id, "job_id": job_id},
        )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def get_event(event_id: str) -> dict[str, Any] | None:
    doc = get_db().collection("events").document(event_id).get()
    if not doc.exists:
        return None
    return {"id": doc.id, **doc.to_dict()}


def list_events() -> list[dict[str, Any]]:
    return [
        {"id": doc.id, **doc.to_dict()}
        for doc in get_db().collection("events").order_by("start_date").stream()
    ]


def create_event(data: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    data.setdefault("status", "upcoming")
    data.setdefault("manually_activated", False)
    data.setdefault("activated_at", None)
    data.setdefault("created_at", now)
    doc_ref = get_db().collection("events").document()
    doc_ref.set(data)
    return doc_ref.id


def update_event(event_id: str, data: dict[str, Any]) -> None:
    data["updated_at"] = datetime.now(timezone.utc)
    get_db().collection("events").document(event_id).update(data)


def delete_event(event_id: str) -> None:
    get_db().collection("events").document(event_id).delete()


def get_live_event() -> dict[str, Any] | None:
    """Return the single live event, or None.

    Only one event is expected to be ``status == 'live'`` at a time. If several
    are live simultaneously (e.g. overlapping test events, or a new event
    activated before the prior one auto-completed), the bots that consume this —
    the hourly Slack bot and the event report scheduler — would otherwise pick a
    nondeterministic one and can stamp anchors/reports for the wrong event. We
    therefore pick deterministically (earliest start_date, then event id) and
    log a WARNING listing every live event so the overlap is visible in logs.
    """
    events = [
        {"id": doc.id, **doc.to_dict()}
        for doc in get_db().collection("events").where("status", "==", "live").stream()
    ]
    if not events:
        return None

    events.sort(key=lambda e: (str(e.get("start_date") or ""), e["id"]))
    if len(events) > 1:
        logger.warning(
            "Multiple live events found — picking deterministically; only one "
            "event should be live at a time",
            extra={
                "phase": "live_event_resolution",
                "error_code": "MULTIPLE_LIVE_EVENTS",
                "live_event_count": len(events),
                "selected_event_id": events[0]["id"],
                "live_event_ids": [e["id"] for e in events],
                "live_event_names": [e.get("name") for e in events],
            },
        )
    return events[0]


# ---------------------------------------------------------------------------
# Bot Configs
# ---------------------------------------------------------------------------

def get_bot_config(client_id: str) -> dict[str, Any] | None:
    doc = get_db().collection("bot_configs").document(client_id).get()
    if not doc.exists:
        return None
    return {"id": doc.id, **doc.to_dict()}


def list_bot_configs() -> list[dict[str, Any]]:
    return [
        {"id": doc.id, **doc.to_dict()}
        for doc in get_db().collection("bot_configs").stream()
    ]


def upsert_bot_config(client_id: str, data: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    data["updated_at"] = now
    doc_ref = get_db().collection("bot_configs").document(client_id)
    if not doc_ref.get().exists:
        data.setdefault("created_at", now)
    doc_ref.set(data, merge=True)


# ---------------------------------------------------------------------------
# Bot Activity
# ---------------------------------------------------------------------------

def log_bot_activity(data: dict[str, Any]) -> str:
    data.setdefault("timestamp", datetime.now(timezone.utc))
    doc_ref = get_db().collection("bot_activity").document()
    doc_ref.set(data)
    return doc_ref.id


# ---------------------------------------------------------------------------
# Slack Thread Anchors
# ---------------------------------------------------------------------------
# Per-event-day parent ("anchor") message that the hourly event bot threads its
# updates under. Keyed deterministically on (event, channel, local date) — one
# anchor per channel per day. Crucially the key does NOT include the client, so
# when several accounts/marketplaces post to the SAME channel (e.g. Skylight's
# per-marketplace accounts, or a shared test channel) they all thread under one
# daily parent instead of each spawning its own look-alike anchor. Each reply
# already names its own account/marketplace. Keying on the channel also means a
# mid-day restart — or a config switch between test and production channels —
# reuses the right parent ts instead of creating a duplicate.

_THREAD_ANCHORS_COLLECTION = "slack_thread_anchors"


def _thread_anchor_doc_id(
    event_id: str, channel_id: str, event_date: str
) -> str:
    """Deterministic document id for a per-channel, per-day thread anchor.

    Slashes are replaced because Firestore document ids cannot contain them.
    """
    raw = f"{event_id}__{channel_id}__{event_date}"
    return raw.replace("/", "_")


def get_thread_anchor_ts(
    event_id: str, channel_id: str, event_date: str
) -> str | None:
    """Return the stored parent message ts for an (event, channel, day), or None."""
    doc = (
        get_db()
        .collection(_THREAD_ANCHORS_COLLECTION)
        .document(_thread_anchor_doc_id(event_id, channel_id, event_date))
        .get()
    )
    if not doc.exists:
        return None
    return doc.to_dict().get("parent_ts")


def set_thread_anchor_ts(
    event_id: str,
    channel_id: str,
    event_date: str,
    parent_ts: str,
    *,
    created_by_client_id: str | None = None,
) -> None:
    """Persist the parent message ts for an (event, channel, day) (create-if-absent).

    Uses ``create()`` so a concurrent run that already wrote the anchor wins and
    a second writer fails loudly rather than silently overwriting the ts.
    ``created_by_client_id`` records which account first posted the day's anchor
    (purely for debugging — the anchor itself is shared across all accounts in
    the channel).
    """
    doc_ref = (
        get_db()
        .collection(_THREAD_ANCHORS_COLLECTION)
        .document(_thread_anchor_doc_id(event_id, channel_id, event_date))
    )
    doc_ref.create({
        "event_id": event_id,
        "channel_id": channel_id,
        "event_date": event_date,
        "parent_ts": parent_ts,
        "created_by_client_id": created_by_client_id,
        "created_at": datetime.now(timezone.utc),
    })


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def list_jobs(
    client_id: str | None = None,
    status: str | None = None,
    schedule_id: str | None = None,
    execution_date: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    ref = get_db().collection("jobs")
    if schedule_id:
        ref = ref.where("schedule_id", "==", schedule_id)
    if execution_date:
        ref = ref.where("execution_date", "==", execution_date)
    if client_id:
        ref = ref.where("client_id", "==", client_id)
    if status:
        ref = ref.where("status", "==", status)
    ref = ref.order_by("started_at", direction=firestore.Query.DESCENDING).limit(limit)
    return [{"id": doc.id, **doc.to_dict()} for doc in ref.stream()]
