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


def list_jobs(
    client_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    ref = get_db().collection("jobs")
    if client_id:
        ref = ref.where("client_id", "==", client_id)
    if status:
        ref = ref.where("status", "==", status)
    ref = ref.order_by("started_at", direction=firestore.Query.DESCENDING).limit(limit)
    return [{"id": doc.id, **doc.to_dict()} for doc in ref.stream()]
