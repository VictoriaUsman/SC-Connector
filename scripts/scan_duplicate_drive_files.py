#!/usr/bin/env python3
"""Scan Google Drive for duplicate report files and flag them for Ops cleanup.

Read-only diagnostic for the "duplicate report pulls within the same run" bug
(CU-868k7evq5). It walks the Drive report hierarchy and reports every folder
that contains more than one file with the *same name* — i.e. a client /
report-type / date-range that was pulled more than once. It NEVER deletes
anything; cleanup is a deliberate Ops action.

Usage:
    # Scan the whole staging root (uses GDRIVE_ROOT_FOLDER_ID by default):
    python scripts/scan_duplicate_drive_files.py

    # Scan a specific folder and only flag files for a given date range:
    python scripts/scan_duplicate_drive_files.py \
        --root-folder-id 1AbCdEf... \
        --contains 2026-06-16_to_2026-06-29

Requires: google-api-python-client + Application Default Credentials with Drive
access (the same service account the connector uses). Run after `make
env-staging` / `make env-prod` so GDRIVE_ROOT_FOLDER_ID is set.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

_FOLDER_MIME = "application/vnd.google-apps.folder"


def find_duplicate_files(files: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group a folder's files by name and return only the names with duplicates.

    ``files`` is a list of Drive file dicts (each with at least a ``name``).
    Folders are ignored. Returns ``{name: [file, ...]}`` for every name that
    appears more than once, so the caller can flag it.
    """
    by_name: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        if f.get("mimeType") == _FOLDER_MIME:
            continue
        by_name.setdefault(f.get("name", ""), []).append(f)
    return {name: group for name, group in by_name.items() if len(group) > 1}


def _list_children(service, folder_id: str) -> list[dict[str, Any]]:
    """List all non-trashed children (files and folders) of a Drive folder."""
    children: list[dict[str, Any]] = []
    page_token: str | None = None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id,name,mimeType,size,createdTime)",
            orderBy="createdTime",
            corpora="allDrives",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
            pageSize=1000,
            pageToken=page_token,
        ).execute()
        children.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return children


def scan_folder(
    service,
    folder_id: str,
    path: str,
    contains: str | None,
    findings: list[dict[str, Any]],
) -> None:
    """Recursively walk ``folder_id``, appending duplicate groups to ``findings``."""
    children = _list_children(service, folder_id)

    files = [c for c in children if c.get("mimeType") != _FOLDER_MIME]
    duplicates = find_duplicate_files(files)
    for name, group in duplicates.items():
        if contains and contains not in name:
            continue
        findings.append({"path": path, "name": name, "files": group})

    for child in children:
        if child.get("mimeType") == _FOLDER_MIME:
            scan_folder(
                service,
                child["id"],
                f"{path}/{child['name']}",
                contains,
                findings,
            )


def _print_report(findings: list[dict[str, Any]]) -> None:
    if not findings:
        print("No duplicate files found. ✅")
        return

    total_extra = 0
    print(f"Found {len(findings)} duplicated file name(s):\n")
    for finding in findings:
        group = finding["files"]
        total_extra += len(group) - 1
        print(f"  {finding['path']}/")
        print(f"    {finding['name']}  ({len(group)} copies)")
        for f in group:
            size = f.get("size", "?")
            created = f.get("createdTime", "?")
            print(f"      - id={f.get('id')}  size={size}  created={created}")
        print()

    print(
        f"Summary: {len(findings)} duplicated name(s), "
        f"{total_extra} redundant file(s) to review.\n"
        "This tool only FLAGS duplicates — delete the redundant copies "
        "manually (keep the oldest/most-complete)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Flag duplicate Drive report files")
    parser.add_argument(
        "--root-folder-id",
        default=os.environ.get("GDRIVE_ROOT_FOLDER_ID", ""),
        help="Drive folder id to scan (default: GDRIVE_ROOT_FOLDER_ID env var)",
    )
    parser.add_argument(
        "--contains",
        default=None,
        help="Only flag files whose name contains this substring "
        "(e.g. a date range like 2026-06-16_to_2026-06-29)",
    )
    args = parser.parse_args()

    if not args.root_folder_id:
        print(
            "ERROR: no root folder id. Set GDRIVE_ROOT_FOLDER_ID (run "
            "`make env-staging`/`make env-prod`) or pass --root-folder-id.",
            file=sys.stderr,
        )
        sys.exit(2)

    # Imported lazily so the pure helpers above stay importable (and testable)
    # without Drive credentials or the Google client libraries installed.
    from google.auth import default
    from googleapiclient.discovery import build

    credentials, _ = default(scopes=["https://www.googleapis.com/auth/drive"])
    service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    print(f"Scanning Drive folder {args.root_folder_id} for duplicates...")
    if args.contains:
        print(f"Filtering to names containing: {args.contains!r}")
    print()

    findings: list[dict[str, Any]] = []
    scan_folder(service, args.root_folder_id, "(root)", args.contains, findings)
    _print_report(findings)


if __name__ == "__main__":
    main()
