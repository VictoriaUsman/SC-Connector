---
name: google-drive-automation
description: Google Drive API v3 patterns for folder creation, file upload, and service account authentication. Use when building or modifying Google Drive upload logic, folder management, or when the user mentions Drive storage.
---

# Google Drive Automation

## Authentication on GCP

On Cloud Functions, use Application Default Credentials — no key file needed. The function's service account is used automatically.

```python
from google.auth import default
from googleapiclient.discovery import build

credentials, project = default(
    scopes=["https://www.googleapis.com/auth/drive"]
)
service = build("drive", "v3", credentials=credentials)
```

For local development, set `GOOGLE_APPLICATION_CREDENTIALS` to a service account key file, or use `gcloud auth application-default login`.

### With Explicit Service Account (if needed)

```python
from google.oauth2 import service_account
from googleapiclient.discovery import build

credentials = service_account.Credentials.from_service_account_file(
    "service-account-key.json",
    scopes=["https://www.googleapis.com/auth/drive"],
)
service = build("drive", "v3", credentials=credentials)
```

## Core Operations

### Create Folder

```python
def create_folder(service, name: str, parent_id: str) -> str:
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = service.files().create(
        body=metadata,
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return folder["id"]
```

### Upload File (from bytes)

```python
from googleapiclient.http import MediaInMemoryUpload

def upload_file(service, filename: str, content: bytes, folder_id: str, mime_type: str = "application/json") -> str:
    media = MediaInMemoryUpload(content, mimetype=mime_type)
    metadata = {
        "name": filename,
        "parents": [folder_id],
    }
    uploaded = service.files().create(
        body=metadata,
        media_body=media,
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return uploaded["id"]
```

### Find Folder by Name

```python
def find_folder(service, name: str, parent_id: str) -> str | None:
    query = (
        f"name='{name}' "
        f"and '{parent_id}' in parents "
        f"and mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false"
    )
    results = service.files().list(
        q=query,
        fields="files(id)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None
```

### Find or Create Folder (with Firestore Coordination)

Google Drive allows duplicate folder names and its `files.list` API is eventually consistent. When concurrent workflow executions race to create the same folder, duplicates appear. We solve this with Firestore's strongly-consistent `document.create()` as a distributed lock:

1. Check Drive (fast path — folder already exists).
2. Attempt to claim a Firestore lock document (`_drive_folder_locks` collection).
   - Winner creates the Drive folder and writes its ID to the lock.
   - Losers poll the lock until the folder ID appears.
3. If Firestore is unavailable, fall back to direct Drive create.

```python
_LOCK_COLLECTION = "_drive_folder_locks"

def find_or_create_folder(name: str, parent_id: str) -> str:
    existing = find_folder(name, parent_id)
    if existing:
        return existing

    try:
        return _create_folder_coordinated(name, parent_id)
    except Exception:
        return create_folder(name, parent_id)  # fallback

def _create_folder_coordinated(name: str, parent_id: str) -> str:
    from google.api_core.exceptions import AlreadyExists

    lock_key = f"{parent_id}__{name}".replace("/", "_")
    lock_ref = db.collection(_LOCK_COLLECTION).document(lock_key)

    try:
        lock_ref.create({"status": "creating", "name": name})
    except AlreadyExists:
        # Another execution is creating — poll for folder_id
        return _wait_for_folder_id(lock_ref, name, parent_id)

    # Double-check Drive (may have propagated)
    existing = find_folder(name, parent_id)
    if existing:
        lock_ref.delete()
        return existing

    folder_id = create_folder(name, parent_id)
    lock_ref.set({"status": "created", "folder_id": folder_id})
    return folder_id
```

**Important**: Never call `create_folder()` directly for shared paths. Always use `find_or_create_folder()` which handles concurrency.

## Our Folder Hierarchy

Reports support two folder layouts, configurable per schedule via `folder_name` and `subfolder_strategy`.

### Default Layout (date-first, when `folder_name` is empty)

```
{root_folder}/
  {YYYY-MM-DD}/
    {client_name}/
      {marketplace}/
        {report_type}/
          {report_type}_{date}_{client}_{marketplace}.tsv
```

Example:
```
Kalilos Reports/
  2026-03-19/
    acme-corp/
      US/
        GET_FLAT_FILE_OPEN_LISTINGS_DATA/
          GET_FLAT_FILE_OPEN_LISTINGS_DATA_2026-03-19_acme-corp_US.tsv
```

### Custom Folder Layout (when `folder_name` is set)

```
{root_folder}/
  {folder_name}/
    {YYYY-MM-DD}/         # only if subfolder_strategy == "date"
      {report_type}_{date}_{client}_{marketplace}.json
```

Example with `folder_name="WoW Weekly Reports"` and `subfolder_strategy="date"`:
```
Kalilos Reports/
  WoW Weekly Reports/
    2026-03-19/
      spCampaigns_2026-03-19_acme-corp_US.json
```

Example with `subfolder_strategy="none"` (flat):
```
Kalilos Reports/
  WoW Weekly Reports/
    spCampaigns_2026-03-19_acme-corp_US.json
```

### Building the Full Path

```python
from datetime import date

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
    """Returns (folder_id, human_readable_path)."""
    if folder_name:
        parts = [folder_name]
        if subfolder_strategy == "date":
            parts.append(report_date.isoformat())
    else:
        parts = [report_date.isoformat(), client_name, marketplace, report_type]

    current = root_folder_id
    for part in parts:
        current = find_or_create_folder(part, current)
    return current, "/".join(parts)
```

### Generating the Filename

```python
def build_filename(report_type: str, report_date: date, client_name: str, marketplace: str, ext: str) -> str:
    return f"{report_type}_{report_date.isoformat()}_{client_name}_{marketplace}{ext}"
```

## Complete Upload Flow

The `upload_report()` function in `functions/shared/drive_client.py` accepts `folder_name` and `subfolder_strategy` parameters that are passed through from the schedule configuration via the workflow.

## Shared Drive Support

If the root folder is on a Shared Drive (Team Drive), all API calls need these extra parameters:

```python
supportsAllDrives=True
includeItemsFromAllDrives=True
driveId="0APxxxxxxxxx"        # only for files().list() on root
corpora="drive"                # only for files().list() on root
```

All our helper functions above already include `supportsAllDrives=True`.

## Handling Duplicates

**Folders**: Prevented by the Firestore lock mechanism in `find_or_create_folder()`. The `_drive_folder_locks` collection coordinates concurrent executions so only one creates the folder.

**Files**: if a file with the same name already exists in the target folder, **overwrite it** (delete old, upload new). This prevents duplicate reports and handles reconciliation re-pulls cleanly.

```python
def upload_or_replace(service, filename: str, content: bytes, folder_id: str, mime_type: str = "application/json") -> str:
    query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
    existing = service.files().list(
        q=query,
        fields="files(id)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute().get("files", [])

    for f in existing:
        service.files().delete(
            fileId=f["id"],
            supportsAllDrives=True,
        ).execute()

    return upload_file(service, filename, content, folder_id, mime_type)
```

## Rate Limits & Quotas

| Quota | Limit |
|-------|-------|
| Queries per day | 1,000,000,000 |
| Queries per 100 seconds per user | 1,000 |
| File uploads per day | 750 GB |
| Max file size (upload) | 5 TB |

For our use case (report uploads), we are well within these limits.

## Error Handling

```python
from googleapiclient.errors import HttpError

try:
    upload_file(service, filename, content, folder_id)
except HttpError as e:
    if e.resp.status == 404:
        # Parent folder not found — recreate hierarchy
        pass
    elif e.resp.status == 403:
        # Insufficient permissions or quota exceeded
        pass
    elif e.resp.status == 429:
        # Rate limited — retry with backoff
        pass
    else:
        raise
```

## Environment Configuration

- `DRIVE_ROOT_FOLDER_ID`: Root folder ID (set via Pulumi stack config -> Cloud Function env var)
- The service account email must be granted Editor access to the root folder (or Shared Drive)
- IAM: The Cloud Function's service account needs the `roles/iam.serviceAccountTokenCreator` role only if impersonating another account; for direct use, no extra IAM is needed beyond Drive folder sharing.
