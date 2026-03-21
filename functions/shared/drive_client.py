"""Google Drive folder hierarchy builder and report uploader."""

from __future__ import annotations

import logging
import time
from datetime import date

from google.auth import default
from google.cloud import firestore as _firestore_module
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

logger = logging.getLogger(__name__)

_service = None
_db = None


def get_service():
    """Cached Drive v3 service using Application Default Credentials."""
    global _service
    if _service is None:
        credentials, _ = default(scopes=["https://www.googleapis.com/auth/drive"])
        _service = build("drive", "v3", credentials=credentials, cache_discovery=False)
    return _service


def _get_db() -> _firestore_module.Client:
    global _db
    if _db is None:
        _db = _firestore_module.Client()
    return _db


def verify_folder_access(folder_id: str) -> None:
    """Verify the service account can access the given folder. Raises with a clear message on failure."""
    from googleapiclient.errors import HttpError

    try:
        meta = get_service().files().get(
            fileId=folder_id,
            fields="id,name,mimeType,trashed",
            supportsAllDrives=True,
        ).execute()

        if meta.get("trashed"):
            raise PermissionError(
                f"Drive folder '{meta.get('name', folder_id)}' ({folder_id}) is in the trash. "
                "Restore it or update GDRIVE_ROOT_FOLDER_ID."
            )
        if meta.get("mimeType") != "application/vnd.google-apps.folder":
            raise ValueError(
                f"GDRIVE_ROOT_FOLDER_ID ({folder_id}) points to a file, not a folder."
            )
    except HttpError as exc:
        if exc.resp.status == 404:
            raise PermissionError(
                f"Drive folder {folder_id} not found. Either the folder was deleted "
                "or the service account does not have access. Share the folder with "
                "the service account email and retry."
            ) from exc
        raise


# ---------------------------------------------------------------------------
# Folder operations
# ---------------------------------------------------------------------------

def _list_matching_folders(name: str, parent_id: str) -> list[dict]:
    """Return all non-trashed folders with the given name under parent, ordered by createdTime."""
    query = (
        f"name='{name}' "
        f"and '{parent_id}' in parents "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false"
    )
    results = get_service().files().list(
        q=query,
        fields="files(id,createdTime)",
        orderBy="createdTime",
        corpora="allDrives",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    return results.get("files", [])


def find_folder(name: str, parent_id: str) -> str | None:
    matches = _list_matching_folders(name, parent_id)
    return matches[0]["id"] if matches else None


def create_folder(name: str, parent_id: str) -> str:
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = get_service().files().create(
        body=metadata,
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return folder["id"]


_LOCK_COLLECTION = "_drive_folder_locks"
_LOCK_POLL_INTERVAL = 1.0
_LOCK_TIMEOUT_SECS = 15


def find_or_create_folder(name: str, parent_id: str) -> str:
    """Find or create a folder, using Firestore to coordinate concurrent callers.

    Drive allows duplicate folder names and its search API is eventually
    consistent, so concurrent workflow executions can each create their own
    copy of the same folder.  We solve this with Firestore's strongly-
    consistent, atomic ``document.create()`` as a distributed lock:

      1. Check Drive (fast path — folder already exists).
      2. Attempt to claim a Firestore lock document.
         - Winner creates the Drive folder and writes its ID to the lock.
         - Losers poll the lock until the folder ID appears.
      3. If Firestore is unavailable, fall back to a direct Drive create.
    """
    existing = find_folder(name, parent_id)
    if existing:
        logger.info("[folder] FOUND '%s' under %s → %s", name, parent_id[:12], existing)
        return existing

    logger.info("[folder] NOT FOUND '%s' under %s — acquiring lock", name, parent_id[:12])
    try:
        return _create_folder_coordinated(name, parent_id)
    except Exception as exc:
        logger.warning("[folder] Firestore coordination failed for '%s', falling back to direct create: %s", name, exc)
        return create_folder(name, parent_id)


def _create_folder_coordinated(name: str, parent_id: str, _is_retry: bool = False) -> str:
    from google.api_core.exceptions import AlreadyExists

    lock_key = f"{parent_id}__{name}".replace("/", "_")
    lock_ref = _get_db().collection(_LOCK_COLLECTION).document(lock_key)

    logger.info("[folder] Lock key: %s", lock_key)

    try:
        lock_ref.create({"status": "creating", "name": name, "parent_id": parent_id})
        logger.info("[folder] WON lock for '%s' — I will create it", name)
    except AlreadyExists:
        if not _is_retry:
            existing_lock = lock_ref.get()
            if existing_lock.exists:
                data = existing_lock.to_dict() or {}
                stale_id = data.get("folder_id")
                if stale_id and not find_folder(name, parent_id):
                    logger.warning("[folder] Stale lock for '%s' (folder %s deleted) — clearing", name, stale_id)
                    lock_ref.delete()
                    return _create_folder_coordinated(name, parent_id, _is_retry=True)
        logger.info("[folder] LOST lock for '%s' — waiting for creator", name)
        return _wait_for_folder_id(lock_ref, name, parent_id)

    existing = find_folder(name, parent_id)
    if existing:
        logger.info("[folder] Drive propagated '%s' → %s (after lock, before create)", name, existing)
        lock_ref.delete()
        return existing

    try:
        folder_id = create_folder(name, parent_id)
        lock_ref.set({"status": "created", "folder_id": folder_id, "name": name, "parent_id": parent_id})
        logger.info("[folder] CREATED '%s' → %s, wrote to lock doc", name, folder_id)
        return folder_id
    except Exception:
        lock_ref.delete()
        raise


def _wait_for_folder_id(lock_ref, name: str, parent_id: str) -> str:
    """Poll the Firestore lock document until the creator writes the folder ID."""
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECS
    polls = 0
    while time.monotonic() < deadline:
        doc = lock_ref.get()
        polls += 1
        if doc.exists:
            data = doc.to_dict() or {}
            folder_id = data.get("folder_id")
            if folder_id:
                logger.info("[folder] Got folder_id from lock after %d polls: '%s' → %s", polls, name, folder_id)
                return folder_id
            logger.info("[folder] Poll %d for '%s': status=%s, no folder_id yet", polls, name, data.get("status"))
        else:
            logger.warning("[folder] Poll %d for '%s': lock doc GONE (creator may have crashed)", polls, name)
        time.sleep(_LOCK_POLL_INTERVAL)

    logger.warning("[folder] TIMEOUT after %d polls for '%s' under %s", polls, name, parent_id[:12])
    existing = find_folder(name, parent_id)
    if existing:
        logger.info("[folder] Found '%s' in Drive after timeout → %s", name, existing)
        return existing

    logger.warning("[folder] Lock timed out for '%s' — clearing stale lock and creating directly", name)
    lock_ref.delete()
    return create_folder(name, parent_id)


def build_folder_path(
    root_folder_id: str,
    client_name: str,
    marketplace: str,
    api_source: str,
    report_type: str,
    report_date: date,
    folder_name: str = "",
    subfolder_strategy: str = "date",
) -> tuple[str, str]:
    """Walk/create the folder hierarchy in Google Drive.

    When folder_name is set, the path is:
        {root} / {folder_name} / {YYYY-MM-DD}  (if subfolder_strategy == "date")
        {root} / {folder_name}                  (if subfolder_strategy == "none")

    When folder_name is empty (default), uses date-first layout:
        {root} / {YYYY-MM-DD} / {client_name} / {marketplace} / {report_type}

    Returns (folder_id, human_readable_path).
    """
    if folder_name:
        parts = [folder_name]
        if subfolder_strategy == "date":
            parts.append(report_date.isoformat())
    else:
        parts = [
            report_date.isoformat(),
            client_name,
            marketplace,
            report_type,
        ]

    current = root_folder_id
    path_so_far: list[str] = []
    for part in parts:
        path_so_far.append(part)
        try:
            current = find_or_create_folder(part, current)
        except Exception:
            logger.error(
                "Failed at Drive path step: %s (parent=%s)",
                "/".join(path_so_far),
                current,
            )
            raise
    return current, "/".join(path_so_far)


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------

def upload_or_replace(
    filename: str,
    content: bytes,
    folder_id: str,
    mime_type: str = "application/json",
) -> str:
    """Upload a file, replacing any existing file with the same name."""
    from googleapiclient.errors import HttpError

    service = get_service()

    query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
    existing = service.files().list(
        q=query,
        fields="files(id)",
        corpora="allDrives",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute().get("files", [])

    for f in existing:
        try:
            service.files().delete(fileId=f["id"], supportsAllDrives=True).execute()
        except HttpError as exc:
            if exc.resp.status == 404:
                logger.warning("File %s already deleted, skipping", f["id"])
            else:
                raise

    media = MediaInMemoryUpload(content, mimetype=mime_type)
    uploaded = service.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return uploaded["id"]


def upload_report(
    root_folder_id: str,
    client_name: str,
    marketplace: str,
    api_source: str,
    report_type: str,
    report_date: date,
    frequency: str,
    content: bytes,
    file_ext: str | None = None,
    mime_type: str | None = None,
    folder_name: str = "",
    subfolder_strategy: str = "date",
    report_end_date: date | None = None,
) -> dict[str, str]:
    """Build full folder hierarchy and upload the report file. Returns file_id and path."""
    verify_folder_access(root_folder_id)

    if not file_ext or not mime_type:
        ext, mt = infer_report_format(api_source, report_type)
        file_ext = file_ext or ext
        mime_type = mime_type or mt

    folder_id, path_prefix = build_folder_path(
        root_folder_id, client_name, marketplace,
        api_source, report_type, report_date,
        folder_name=folder_name,
        subfolder_strategy=subfolder_strategy,
    )

    date_part = report_date.isoformat()
    if report_end_date and report_end_date != report_date:
        date_part = f"{report_date.isoformat()}_to_{report_end_date.isoformat()}"
    filename = f"{report_type}_{date_part}_{client_name}_{marketplace}{file_ext}"

    file_id = upload_or_replace(filename, content, folder_id, mime_type=mime_type)

    path = f"{path_prefix}/{filename}"

    logger.info("Report uploaded to Drive", extra={"file_id": file_id, "path": path})
    return {"file_id": file_id, "path": path, "filename": filename}


_SP_API_JSON_REPORTS = {
    "GET_SALES_AND_TRAFFIC_REPORT",
    "GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT",
    "GET_BRAND_ANALYTICS_MARKET_BASKET_REPORT",
    "GET_BRAND_ANALYTICS_REPEAT_PURCHASE_REPORT",
    "GET_BRAND_ANALYTICS_ALTERNATE_PURCHASE_REPORT",
    "GET_LEDGER_SUMMARY_VIEW_DATA",
}


def infer_report_format(api_source: str, report_type: str) -> tuple[str, str]:
    """Infer (file_extension, mime_type) from the api_source and report type.

    SP API name-based heuristic: GET_FLAT_FILE_* → TSV, GET_XML_* → XML,
    GET_CSV_* → CSV, GET_JSON_* → JSON.  Some reports (Sales & Traffic,
    Brand Analytics, Ledger) return JSON despite having no format hint
    in their name — these are handled by an explicit set.
    Ads API v3 is always JSON.
    """
    if api_source == "ads_api":
        return ".json", "application/json"

    rt = report_type.upper()
    if rt in _SP_API_JSON_REPORTS:
        return ".json", "application/json"
    if "XML" in rt:
        return ".xml", "application/xml"
    if "CSV" in rt:
        return ".csv", "text/csv"
    if "JSON" in rt:
        return ".json", "application/json"
    return ".tsv", "text/tab-separated-values"
