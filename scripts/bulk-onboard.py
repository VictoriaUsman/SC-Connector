#!/usr/bin/env python3
"""Bulk-onboard existing clients with their SP API refresh tokens.

Reads a CSV file and for each row:
  1. Creates (or updates) a Firestore client document
  2. Stores the refresh token in Secret Manager

CSV format (with header row):
  client_id,client_name,marketplaces,sp_api_refresh_token

  - client_id:              slug identifier, e.g. "glove-station"
  - client_name:            display name, e.g. "Glove Station"
  - marketplaces:           pipe-separated, e.g. "US" or "US|CA|MX"
  - sp_api_refresh_token:   the Atzr|... token

Usage:
    python scripts/bulk-onboard.py --csv clients.csv [--project kalilos-connector-staging] [--env staging] [--dry-run]

Requires:
    pip install google-cloud-firestore google-cloud-secret-manager
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud import firestore, secretmanager


def slugify(name: str) -> str:
    return name.lower().strip().replace(" ", "-").replace("_", "-")


def onboard_client(
    db: firestore.Client,
    sm: secretmanager.SecretManagerServiceClient,
    project: str,
    env: str,
    client_id: str,
    client_name: str,
    marketplaces: list[str],
    refresh_token: str,
    dry_run: bool = False,
) -> None:
    secret_name = f"kalilos-{env}-sp-api-{client_id}"
    now = datetime.now(timezone.utc)

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Processing: {client_name} ({client_id})")
    print(f"  Marketplaces: {marketplaces}")
    print(f"  Secret: {secret_name}")
    print(f"  Token: {refresh_token[:20]}...{refresh_token[-6:]}")

    if dry_run:
        return

    # --- Secret Manager ---
    parent = f"projects/{project}"
    full_name = f"{parent}/secrets/{secret_name}"

    try:
        sm.get_secret(request={"name": full_name})
        print(f"  Secret '{secret_name}' exists — adding new version...")
    except NotFound:
        sm.create_secret(request={
            "parent": parent,
            "secret_id": secret_name,
            "secret": {"replication": {"automatic": {}}},
        })
        print(f"  Created secret '{secret_name}'")

    payload = json.dumps({"refresh_token": refresh_token}).encode("utf-8")
    sm.add_secret_version(request={"parent": full_name, "payload": {"data": payload}})
    print(f"  Stored refresh token in Secret Manager")

    # --- Firestore ---
    client_data = {
        "name": client_name,
        "marketplaces": marketplaces,
        "is_active": True,
        "sp_api_secret_name": secret_name,
        "updated_at": now,
    }

    doc_ref = db.collection("clients").document(client_id)
    if not doc_ref.get().exists:
        client_data["created_at"] = now

    doc_ref.set(client_data, merge=True)
    print(f"  Upserted Firestore client document")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk onboard clients from CSV")
    parser.add_argument("--csv", required=True, help="Path to CSV file")
    parser.add_argument("--project", default=os.environ.get("GCP_PROJECT", "kalilos-connector-staging"))
    parser.add_argument("--env", default=os.environ.get("ENVIRONMENT", "staging"))
    parser.add_argument("--dry-run", action="store_true", help="Preview without making changes")
    args = parser.parse_args()

    if not os.path.isfile(args.csv):
        print(f"Error: CSV file not found: {args.csv}", file=sys.stderr)
        sys.exit(1)

    with open(args.csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        required = {"client_id", "client_name", "marketplaces", "sp_api_refresh_token"}
        if not required.issubset(set(reader.fieldnames or [])):
            missing = required - set(reader.fieldnames or [])
            print(f"Error: CSV missing columns: {missing}", file=sys.stderr)
            print(f"Required columns: {sorted(required)}", file=sys.stderr)
            sys.exit(1)

        rows = list(reader)

    print(f"Found {len(rows)} clients in {args.csv}")
    print(f"Target: project={args.project}, env={args.env}")

    if args.dry_run:
        print("\n*** DRY RUN — no changes will be made ***")

    db = firestore.Client(project=args.project)
    sm = secretmanager.SecretManagerServiceClient()

    success = 0
    errors = 0
    for row in rows:
        client_id = slugify(row["client_id"].strip())
        client_name = row["client_name"].strip()
        marketplaces = [m.strip().upper() for m in row["marketplaces"].split("|")]
        refresh_token = row["sp_api_refresh_token"].strip()

        if not refresh_token:
            print(f"\n  SKIP: {client_name} — no refresh token")
            errors += 1
            continue

        try:
            onboard_client(db, sm, args.project, args.env, client_id, client_name, marketplaces, refresh_token, args.dry_run)
            success += 1
        except Exception as exc:
            print(f"\n  ERROR: {client_name} — {exc}")
            errors += 1

    print(f"\n{'='*50}")
    print(f"Done! {success} succeeded, {errors} failed/skipped")


if __name__ == "__main__":
    main()
