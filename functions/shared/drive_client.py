"""Google Drive folder hierarchy builder and report uploader."""

from __future__ import annotations

import logging
import time
from datetime import date

from google.auth import default
from google.cloud import firestore as _firestore_module
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

from shared import db
from shared.vendor_reports import VENDOR_SP_REPORT_TYPES

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


def _create_folder_with_dedup(name: str, parent_id: str) -> str:
    """Create a folder with a safety net: sleep, re-check Drive, then deduplicate.

    Used as a last resort when Postgres coordination is unavailable.
    Because Drive's search is eventually consistent, a concurrent caller
    may have already created the folder but it isn't visible yet.  We
    sleep briefly to widen the consistency window, re-check, and if we
    still must create, do a post-creation dedup pass to clean up races.
    """
    time.sleep(2)
    existing = find_folder(name, parent_id)
    if existing:
        logger.info("[folder] Found '%s' on re-check after delay → %s", name, existing)
        return existing

    folder_id = create_folder(name, parent_id)
    logger.info("[folder] Created '%s' via fallback → %s, running dedup", name, folder_id)

    time.sleep(2)
    matches = _list_matching_folders(name, parent_id)
    if len(matches) > 1:
        winner_id = matches[0]["id"]
        from googleapiclient.errors import HttpError
        for dupe in matches[1:]:
            try:
                get_service().files().delete(fileId=dupe["id"], supportsAllDrives=True).execute()
                logger.info("[folder] Dedup: deleted duplicate %s for '%s'", dupe["id"], name)
            except HttpError as exc:
                if exc.resp.status != 404:
                    logger.warning("[folder] Dedup: could not delete %s: %s", dupe["id"], exc)
        logger.info("[folder] Dedup: kept oldest folder %s for '%s'", winner_id, name)
        return winner_id

    return folder_id


def _folder_exists(folder_id: str) -> bool:
    """Strongly-consistent check whether a Drive folder exists (not trashed).

    Uses ``files().get()`` which is consistent, unlike the search API used by
    ``find_folder``. Critical for stale-lock detection where the folder may
    have been created moments ago and isn't yet visible in search results.
    """
    from googleapiclient.errors import HttpError
    try:
        meta = get_service().files().get(
            fileId=folder_id,
            fields="id,trashed",
            supportsAllDrives=True,
        ).execute()
        return not meta.get("trashed", False)
    except HttpError as exc:
        if exc.resp.status == 404:
            return False
        raise


_LOCK_COLLECTION = "_drive_folder_locks"
_LOCK_POLL_INTERVAL = 1.0
_LOCK_TIMEOUT_SECS = 15


def find_or_create_folder(name: str, parent_id: str) -> str:
    """Find or create a folder, using Postgres to coordinate concurrent callers.

    Drive allows duplicate folder names and its search API is eventually
    consistent, so concurrent workflow executions can each create their own
    copy of the same folder.  We solve this with an atomic
    ``INSERT ... ON CONFLICT DO NOTHING`` on the ``drive_folder_locks`` table
    as a distributed lock:

      1. Check Drive (fast path — folder already exists).
      2. Attempt to claim a Postgres lock row.
         - Winner creates the Drive folder and writes its ID to the lock.
         - Losers poll the lock until the folder ID appears.
      3. If Postgres is unavailable, fall back with dedup safety net.
    """
    existing = find_folder(name, parent_id)
    if existing:
        logger.info("[folder] FOUND '%s' under %s → %s", name, parent_id[:12], existing)
        return existing

    logger.info("[folder] NOT FOUND '%s' under %s — acquiring lock", name, parent_id[:12])
    try:
        return _create_folder_coordinated(name, parent_id)
    except Exception as exc:
        logger.warning("[folder] Postgres coordination failed for '%s', falling back: %s", name, exc)
        return _create_folder_with_dedup(name, parent_id)


def _create_folder_coordinated(name: str, parent_id: str) -> str:
    lock_key = f"{parent_id}__{name}".replace("/", "_")

    logger.info("[folder] Lock key: %s", lock_key)

    claimed = db.try_claim_drive_folder_lock(lock_key)
    if claimed:
        logger.info("[folder] WON lock for '%s' — I will create it", name)
    else:
        existing_lock = db.get_drive_folder_lock(lock_key)
        if existing_lock:
            stale_id = existing_lock.get("folder_id")
            if stale_id:
                if _folder_exists(stale_id):
                    logger.info("[folder] Lock for '%s' has valid folder %s — reusing", name, stale_id)
                    return stale_id
                logger.warning("[folder] Stale lock for '%s' (folder %s deleted) — deleting lock, using dedup path", name, stale_id)
                db.delete_drive_folder_lock(lock_key)
                return _create_folder_with_dedup(name, parent_id)
        logger.info("[folder] LOST lock for '%s' — waiting for creator", name)
        return _wait_for_folder_id(lock_key, name, parent_id)

    existing = find_folder(name, parent_id)
    if existing:
        logger.info("[folder] Drive propagated '%s' → %s (after lock, before create)", name, existing)
        db.delete_drive_folder_lock(lock_key)
        return existing

    try:
        folder_id = create_folder(name, parent_id)
        db.set_drive_folder_lock_folder_id(lock_key, folder_id)
        logger.info("[folder] CREATED '%s' → %s, wrote to lock row", name, folder_id)
        return folder_id
    except Exception:
        db.delete_drive_folder_lock(lock_key)
        raise


def _wait_for_folder_id(lock_key: str, name: str, parent_id: str) -> str:
    """Poll the drive_folder_locks row until the creator writes the folder ID."""
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECS
    polls = 0
    while time.monotonic() < deadline:
        lock = db.get_drive_folder_lock(lock_key)
        polls += 1
        if lock is not None:
            folder_id = lock.get("folder_id")
            if folder_id:
                logger.info("[folder] Got folder_id from lock after %d polls: '%s' → %s", polls, name, folder_id)
                return folder_id
            logger.info("[folder] Poll %d for '%s': no folder_id yet", polls, name)
        else:
            logger.warning("[folder] Poll %d for '%s': lock row GONE (creator may have crashed)", polls, name)
        time.sleep(_LOCK_POLL_INTERVAL)

    logger.warning("[folder] TIMEOUT after %d polls for '%s' under %s", polls, name, parent_id[:12])
    existing = find_folder(name, parent_id)
    if existing:
        logger.info("[folder] Found '%s' in Drive after timeout → %s", name, existing)
        return existing

    logger.warning("[folder] Lock timed out for '%s' — clearing stale lock and creating with dedup", name)
    db.delete_drive_folder_lock(lock_key)
    return _create_folder_with_dedup(name, parent_id)


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

    The standard layout is always:
        {YYYY-MM-DD} / {client_name} / {marketplace} / {report_type}

    When folder_name is set it acts as a prefix:
        {folder_name} / {YYYY-MM-DD} / {client_name} / {marketplace} / {report_type}

    subfolder_strategy == "none" drops the date segment:
        {folder_name} / {client_name} / {marketplace} / {report_type}   (custom)
        {client_name} / {marketplace} / {report_type}                    (default)

    Returns (folder_id, human_readable_path).
    """
    # Normalize every segment by stripping surrounding whitespace. Drive treats
    # "MTD Ads KPIs" and "MTD Ads KPIs " as different names, which silently
    # creates two visually-identical folders (and two Postgres locks). Stray
    # whitespace in a folder name is never intentional, so we collapse it here.
    parts: list[str] = []
    if folder_name:
        parts.extend(seg.strip() for seg in folder_name.split("/") if seg.strip())
    if subfolder_strategy != "none":
        parts.append(report_date.isoformat())
    parts.extend(seg.strip() for seg in (client_name, marketplace, report_type) if seg.strip())

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

        _assert_no_duplicates(part, current, path_so_far)

    return current, "/".join(path_so_far)


def _assert_no_duplicates(name: str, folder_id: str, path_so_far: list[str]) -> None:
    """Final safety net: if duplicates of this folder exist, keep oldest and delete rest."""
    try:
        parent_of = get_service().files().get(
            fileId=folder_id, fields="parents", supportsAllDrives=True,
        ).execute().get("parents", [None])[0]
        if not parent_of:
            return
        matches = _list_matching_folders(name, parent_of)
        if len(matches) <= 1:
            return

        from googleapiclient.errors import HttpError
        winner = matches[0]["id"]
        for dupe in matches[1:]:
            try:
                get_service().files().delete(fileId=dupe["id"], supportsAllDrives=True).execute()
                logger.warning("[dedup-guard] Deleted duplicate folder %s for '%s' at %s", dupe["id"], name, "/".join(path_so_far))
            except HttpError as exc:
                if exc.resp.status != 404:
                    logger.warning("[dedup-guard] Could not delete %s: %s", dupe["id"], exc)

        if folder_id != winner:
            logger.warning("[dedup-guard] Switched from %s to winner %s for '%s'", folder_id, winner, name)
    except Exception as exc:
        # Best-effort guard: never fail the upload over dedup, but don't swallow
        # silently either - a recurring warning here is a real Drive/API problem.
        logger.warning(
            "[dedup-guard] Duplicate check failed (non-fatal)",
            extra={"folder_id": folder_id, "folder_name": name, "error": str(exc)[:200]},
        )


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------

_SHEETS_CONVERTIBLE_MIMES = {"text/tab-separated-values", "text/csv"}
_SHEETS_SIZE_LIMIT = 10 * 1024 * 1024  # 10 MB
_SHEETS_MIME = "application/vnd.google-apps.spreadsheet"

# Firestore collection recording the last file uploaded for each
# (folder_id, stored_name). See _delete_recorded_file for why this exists.
_FILE_INDEX_COLLECTION = "_drive_file_index"


def _strip_extension(filename: str) -> str:
    """Return ``filename`` without its trailing extension (if any)."""
    return filename.rsplit(".", 1)[0] if "." in filename else filename


def _file_index_key(folder_id: str, stored_name: str) -> str:
    """Deterministic Firestore doc id for a (folder, stored file name) pair."""
    return f"{folder_id}__{stored_name}".replace("/", "_")


def _delete_recorded_file(folder_id: str, stored_name: str) -> None:
    """Delete the exact file a prior upload recorded for this (folder, name).

    Drive's ``files.list`` name query is *eventually consistent*, so a rapid
    re-upload of the same report — a workflow retry after the download-upload
    ``http.post`` times out (the SB campaigns pull runs for minutes and the
    workflow re-invokes it), a duplicate trigger, or a concurrent run — often
    does not yet see the file the previous attempt just created (a freshly
    converted Google Sheet in particular). The name-based search then finds
    nothing to delete and each attempt stacks another duplicate in the folder.

    To replace reliably we record every uploaded file's id in a strongly-
    consistent Firestore doc keyed by (folder_id, stored_name). Before each
    upload we read that doc and delete the recorded file by *id*, which is
    immune to search propagation lag, so repeated uploads converge to exactly
    one file. Best-effort: never fail an upload over dedupe bookkeeping.
    """
    from googleapiclient.errors import HttpError

    try:
        ref = _get_db().collection(_FILE_INDEX_COLLECTION).document(
            _file_index_key(folder_id, stored_name)
        )
        snap = ref.get()
        if not snap.exists:
            return
        prior_id = (snap.to_dict() or {}).get("file_id")
        if not prior_id:
            return
        try:
            get_service().files().delete(
                fileId=prior_id, supportsAllDrives=True,
            ).execute()
            logger.info(
                "[file-dedupe] Deleted prior recorded file %s for '%s'",
                prior_id, stored_name,
            )
        except HttpError as exc:
            if exc.resp.status != 404:
                raise
    except Exception as exc:
        logger.warning(
            "[file-dedupe] Could not delete recorded file (non-fatal)",
            extra={"folder_id": folder_id, "stored_name": stored_name, "error": str(exc)[:200]},
        )


def _record_uploaded_file(folder_id: str, stored_name: str, file_id: str) -> None:
    """Record the just-uploaded file's id so the next upload can replace it
    deterministically (see _delete_recorded_file). Best-effort."""
    try:
        ref = _get_db().collection(_FILE_INDEX_COLLECTION).document(
            _file_index_key(folder_id, stored_name)
        )
        ref.set({
            "file_id": file_id,
            "folder_id": folder_id,
            "name": stored_name,
            "updated_at": _firestore_module.SERVER_TIMESTAMP,
        })
    except Exception as exc:
        logger.warning(
            "[file-dedupe] Could not record uploaded file (non-fatal)",
            extra={"folder_id": folder_id, "stored_name": stored_name, "error": str(exc)[:200]},
        )


def upload_or_replace(
    filename: str,
    content: bytes,
    folder_id: str,
    mime_type: str = "application/json",
) -> str:
    """Upload a file, replacing any existing file with the same name.

    TSV/CSV files under 10 MB are auto-converted to native Google Sheets
    so that Claude (and other tools) can read them directly via the
    Google Drive connector.

    When a TSV/CSV is converted to a native Google Sheet, Drive drops the file
    extension from the stored title (a Sheet has no extension) — so a file
    uploaded as ``report.tsv`` is stored and searchable only as ``report``. If
    the replace search looked only for the extension-qualified ``report.tsv``,
    it would never match the already-converted Sheet, so the stale copy would
    never be deleted and every re-upload (a re-run, workflow retry, or a
    duplicate trigger) would stack another duplicate Sheet in the folder. To
    replace reliably we (a) search for both the extension-qualified name and
    the extension-stripped stem for Sheets-convertible reports, and (b) store
    the converted Sheet under the stem so its title is deterministic and future
    searches always find it.

    Drive's search is only eventually consistent though, so a rapid re-upload
    (a workflow retry after the download-upload http call times out, a duplicate
    trigger, or a concurrent run) may not yet see the file the prior attempt
    created and would stack a duplicate. As a deterministic, search-lag-immune
    guard we also record each uploaded file's id in Firestore and delete that
    exact file by id before the new upload, so repeated uploads of the same
    report converge to a single file.
    """
    from googleapiclient.errors import HttpError

    service = get_service()

    convert_to_sheets = (
        mime_type in _SHEETS_CONVERTIBLE_MIMES
        and len(content) <= _SHEETS_SIZE_LIMIT
    )

    # The name the new file is stored under (converted Sheets drop the
    # extension). Also the key under which we track/replace it in Firestore.
    stem = _strip_extension(filename)
    stored_name = stem if convert_to_sheets else filename

    # Deterministic replace: delete the exact file a prior upload recorded for
    # this (folder, stored_name), regardless of Drive search propagation lag.
    _delete_recorded_file(folder_id, stored_name)

    # Match both the raw filename and, for Sheets-convertible report types, the
    # extension-stripped title Drive assigns to the converted Sheet. This cleans
    # up both legacy raw uploads and previously-converted Sheets regardless of
    # this run's size (a prior smaller run may already be an extension-less
    # Sheet even if this content skips conversion).
    names_to_replace = [filename]
    if mime_type in _SHEETS_CONVERTIBLE_MIMES and stem != filename:
        names_to_replace.append(stem)

    name_clause = " or ".join(f"name='{name}'" for name in names_to_replace)
    query = f"({name_clause}) and '{folder_id}' in parents and trashed=false"
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

    # Store the converted Sheet under the extension-stripped stem so its title
    # matches what Drive would assign anyway, keeping the replace search stable.
    body: dict = {"name": stored_name, "parents": [folder_id]}
    if convert_to_sheets:
        body["mimeType"] = _SHEETS_MIME
        logger.info(
            "Converting to Google Sheet (size=%d bytes)", len(content),
        )

    media = MediaInMemoryUpload(content, mimetype=mime_type)
    uploaded = service.files().create(
        body=body,
        media_body=media,
        fields="id",
        supportsAllDrives=True,
    ).execute()

    # Record this file so the next upload of the same report can delete it
    # deterministically even before Drive's search index catches up.
    _record_uploaded_file(folder_id, stored_name, uploaded["id"])
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
    execution_date: date | None = None,
) -> dict[str, str]:
    """Build full folder hierarchy and upload the report file. Returns file_id and path.

    The folder hierarchy uses ``execution_date`` (when the report was created)
    so all reports from the same run land in the same folder.  The filename
    uses ``report_date`` / ``report_end_date`` (what data the report covers)
    so employees can identify the data range at a glance.
    """
    verify_folder_access(root_folder_id)

    if not file_ext or not mime_type:
        ext, mt = infer_report_format(api_source, report_type)
        file_ext = file_ext or ext
        mime_type = mime_type or mt

    folder_date = execution_date if execution_date is not None else report_date
    folder_id, path_prefix = build_folder_path(
        root_folder_id, client_name, marketplace,
        api_source, report_type, folder_date,
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
    return {"file_id": file_id, "folder_id": folder_id, "path": path, "filename": filename}


_SP_API_JSON_REPORTS = {
    "GET_SALES_AND_TRAFFIC_REPORT",
    "GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT",
    "GET_BRAND_ANALYTICS_MARKET_BASKET_REPORT",
    "GET_BRAND_ANALYTICS_REPEAT_PURCHASE_REPORT",
    "GET_BRAND_ANALYTICS_ALTERNATE_PURCHASE_REPORT",
    "GET_BRAND_ANALYTICS_SEARCH_QUERY_PERFORMANCE_REPORT",
    "GET_BRAND_ANALYTICS_SEARCH_CATALOG_PERFORMANCE_REPORT",
    "GET_LEDGER_SUMMARY_VIEW_DATA",
    # Vendor (1P) reports also return JSON.
    *VENDOR_SP_REPORT_TYPES,
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
        return ".tsv", "text/tab-separated-values"

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
