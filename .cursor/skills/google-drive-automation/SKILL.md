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

### Find or Create Folder (with Firestore Coordination + Dedup Safety Net)

Google Drive allows duplicate folder names and its `files.list` API is eventually consistent. When concurrent workflow executions race to create the same folder, duplicates appear. We solve this with a 4-layer defense:

**Layer 1 — Firestore distributed lock**: `find_or_create_folder()` uses Firestore's strongly-consistent `document.create()` as an atomic lock. Winner creates the Drive folder and writes its ID to the lock document. Losers poll until the folder ID appears.

**Layer 2 — Stale lock handling**: When a lock references a deleted Drive folder (e.g., user deleted it between runs), the stale lock is cleared and the caller falls through to the dedup path. **Never recursively retry lock acquisition after clearing a stale lock** — two concurrent callers can both clear the same stale lock and both re-acquire independently (TOCTOU race).

**Layer 3 — Dedup fallback** (`_create_folder_with_dedup`): When Firestore coordination fails or a stale lock is detected, this fallback sleeps 2s (widens the Drive consistency window), re-checks Drive, creates only if nothing found, then does a post-creation dedup pass: lists all matching folders sorted by `createdTime`, keeps the oldest, deletes extras.

**Layer 4 — Runtime guard** (`_assert_no_duplicates`): Runs after every folder segment in `build_folder_path()`. If duplicates exist at any level, the oldest is kept and extras are deleted before the file is uploaded. This is the last line of defense.

```python
def find_or_create_folder(name: str, parent_id: str) -> str:
    existing = find_folder(name, parent_id)
    if existing:
        return existing
    try:
        return _create_folder_coordinated(name, parent_id)
    except Exception:
        return _create_folder_with_dedup(name, parent_id)  # NOT create_folder()!
```

**Critical rules**:
- Never call `create_folder()` directly for shared paths — always use `find_or_create_folder()`
- Never recursively retry lock acquisition in stale lock handling — fall through to dedup path
- The fallback must ALWAYS be `_create_folder_with_dedup()`, never a bare `create_folder()`

## Our Folder Hierarchy

Reports support two folder layouts, configurable per schedule via `folder_name` and `subfolder_strategy`.

**Folder date = execution date** (when the report was created, i.e. marketplace today at launch time). This is computed once in `launch_for_marketplace()` and threaded through the workflow payload.
**Filename date = report data date** (what data the report covers). For date ranges: `{start}_to_{end}`.

### Default Layout (date-first, when `folder_name` is empty)

```
{root_folder}/
  {execution_date}/               # when the report was created
    {client_name}/
      {marketplace}/
        {report_type}/
          {report_type}_{data_date}_{client}_{marketplace}.tsv
```

Example (daily "yesterday" report run on Mar 20):
```
Kalilos Reports/
  2026-03-20/
    acme-corp/
      US/
        GET_FLAT_FILE_OPEN_LISTINGS_DATA/
          GET_FLAT_FILE_OPEN_LISTINGS_DATA_2026-03-19_acme-corp_US.tsv
```

Example (last calendar month report run on Mar 21, pulling Feb data):
```
Kalilos Reports/
  2026-03-21/
    acme-corp/
      US/
        GET_SALES_AND_TRAFFIC_REPORT/
          GET_SALES_AND_TRAFFIC_REPORT_2026-02-01_to_2026-02-28_acme-corp_US.tsv
```

### Custom Folder Layout (when `folder_name` is set)

```
{root_folder}/
  {folder_name}/
    {execution_date}/         # only if subfolder_strategy == "date"
      {report_type}_{data_date}_{client}_{marketplace}.json
```

### Building the Full Path

```python
def build_folder_path(..., report_date: date, ...) -> tuple[str, str]:
    # report_date here is actually the execution_date (folder organization)
    # passed from upload_report() which selects execution_date over report_date
    ...
    current = root_folder_id
    for part in parts:
        current = find_or_create_folder(part, current)
        _assert_no_duplicates(part, current, path_so_far)  # runtime dedup guard
    return current, "/".join(parts)
```

### Generating the Filename

Filename uses the **report data dates** (not execution date):

```python
# Single day:
f"{report_type}_{report_date}_{client_name}_{marketplace}{ext}"
# Date range:
f"{report_type}_{start}_to_{end}_{client_name}_{marketplace}{ext}"
```

## Complete Upload Flow

`upload_report()` accepts both `report_date` (for filename) and `execution_date` (for folder hierarchy). The `execution_date` is computed once in `launch_for_marketplace()` and threaded through the workflow payload → `download_upload` → `upload_report()`.

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

**Folders**: Prevented by the 4-layer defense in `find_or_create_folder()` (Firestore lock → stale lock → dedup fallback → runtime guard). The `_drive_folder_locks` collection coordinates concurrent executions. If users delete Drive folders between runs, stale locks are detected and handled safely. See the "Find or Create Folder" section above for the full defense strategy.

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
