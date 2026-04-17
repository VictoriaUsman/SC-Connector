#!/usr/bin/env python3
"""Import clients from a Studio JSON export into the Kalilos connector.

Reads studio_results_*.json, maps each entry to the Kalilos Client model,
creates or validates existing clients in Firestore, and stores SP API
refresh tokens in Secret Manager.

Usage:
    python3 scripts/import_studio_clients.py --dry-run
    python3 scripts/import_studio_clients.py --project kalilos-connector-staging
    python3 scripts/import_studio_clients.py --project kalilos-connector-prod
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

os.environ.setdefault("GCP_PROJECT", "kalilos-connector-staging")
os.environ.setdefault("ENVIRONMENT", "staging")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

import subprocess
import google.auth.credentials
import google.oauth2.credentials
from google.cloud import firestore, secretmanager

SOURCE_FILE = os.path.join(
    os.path.dirname(__file__), "..", "studio_results_20260414_0951.json"
)

GCP_ACCOUNT = "nivbraz90@gmail.com"

_credentials: google.auth.credentials.Credentials | None = None
_db: firestore.Client | None = None
_sm: secretmanager.SecretManagerServiceClient | None = None


def _get_credentials() -> google.auth.credentials.Credentials:
    """Get OAuth2 credentials from a specific gcloud account."""
    global _credentials
    if _credentials is None:
        token = subprocess.check_output(
            ["gcloud", "auth", "print-access-token", f"--account={GCP_ACCOUNT}"],
            text=True,
        ).strip()
        _credentials = google.oauth2.credentials.Credentials(token=token)
    return _credentials


def get_db(project: str) -> firestore.Client:
    global _db
    if _db is None:
        _db = firestore.Client(project=project, credentials=_get_credentials())
    return _db


def get_client(db: firestore.Client, client_id: str) -> dict[str, Any] | None:
    doc = db.collection("clients").document(client_id).get()
    if not doc.exists:
        return None
    return {"id": doc.id, **doc.to_dict()}


def upsert_client(db: firestore.Client, client_id: str, data: dict[str, Any]) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    data["updated_at"] = now
    doc_ref = db.collection("clients").document(client_id)
    if not doc_ref.get().exists:
        data.setdefault("created_at", now)
        data.setdefault("is_active", True)
    doc_ref.set(data, merge=True)


def _get_sm() -> secretmanager.SecretManagerServiceClient:
    global _sm
    if _sm is None:
        _sm = secretmanager.SecretManagerServiceClient(credentials=_get_credentials())
    return _sm


def slugify(name: str) -> str:
    """Convert a display name to a URL-safe kebab-case slug."""
    s = name.strip().lower()
    s = re.sub(r"[''`]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s


def is_valid_refresh_token(key: str) -> bool:
    return bool(key) and key.startswith("Atzr|")


def is_valid_ads_profile(profile_id: str) -> bool:
    return bool(profile_id) and profile_id.strip().isdigit()


def store_sp_secret(project: str, env: str, client_id: str, refresh_token: str) -> str:
    """Create or update an SP API secret in Secret Manager. Returns the secret name."""
    secret_name = f"kalilos-{env}-sp-api-{client_id}"
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
        "payload": {"data": json.dumps({"refresh_token": refresh_token}).encode("utf-8")},
    })

    return secret_name


def diff_fields(existing: dict[str, Any], expected: dict[str, Any]) -> dict[str, tuple]:
    """Compare expected fields against existing client. Returns {field: (old, new)} for mismatches."""
    diffs = {}
    for key, new_val in expected.items():
        old_val = existing.get(key)
        if old_val != new_val:
            diffs[key] = (old_val, new_val)
    return diffs


def process_entry(
    entry: dict[str, Any],
    db: firestore.Client,
    project: str,
    env: str,
    dry_run: bool,
    update: bool,
) -> dict[str, str]:
    """Process one studio JSON entry. Returns a summary dict for reporting."""
    name = entry["name"].strip()
    client_id = slugify(name)
    marketplace = entry.get("marketplace", "US").strip()
    raw_key = entry.get("key", "").strip()
    raw_ads = entry.get("adsProfileId", "").strip()

    summary: dict[str, str] = {
        "client_id": client_id,
        "source_id": entry.get("id", "?"),
        "name": name,
        "marketplace": marketplace,
        "sp_status": "",
        "ads_status": "",
        "action": "",
    }

    client_data: dict[str, Any] = {
        "name": name,
        "marketplaces": [marketplace],
    }

    has_sp = is_valid_refresh_token(raw_key)
    has_ads = is_valid_ads_profile(raw_ads)

    if has_ads:
        client_data["ads_profile_id"] = raw_ads

    if not has_sp:
        summary["sp_status"] = "skip:invalid" if raw_key else "skip:empty"
    if not has_ads:
        summary["ads_status"] = "skip:invalid" if raw_ads else "skip:empty"

    existing = get_client(db, client_id)

    if existing:
        diffs = diff_fields(existing, client_data)
        if diffs:
            summary["action"] = "DIFF"
            for field, (old, new) in diffs.items():
                print(f"  {client_id}: {field} differs: {old!r} -> {new!r}")
            if update and not dry_run:
                upsert_client(db, client_id, client_data)
                summary["action"] = "UPDATED"
        else:
            summary["action"] = "EXISTS"

        if has_sp:
            if existing.get("sp_api_secret_name"):
                summary["sp_status"] = "exists"
            else:
                summary["sp_status"] = "new"
                if not dry_run:
                    sec = store_sp_secret(project, env, client_id, raw_key)
                    upsert_client(db, client_id, {"sp_api_secret_name": sec})
                    summary["sp_status"] = "stored"

        if has_ads:
            old_ads = existing.get("ads_profile_id", "")
            if old_ads == raw_ads:
                summary["ads_status"] = "match"
            else:
                summary["ads_status"] = f"update({old_ads}->{raw_ads})"
                if not dry_run:
                    upsert_client(db, client_id, {"ads_profile_id": raw_ads})

    else:
        summary["action"] = "NEW"
        if not dry_run:
            upsert_client(db, client_id, client_data)

            if has_sp:
                sec = store_sp_secret(project, env, client_id, raw_key)
                upsert_client(db, client_id, {"sp_api_secret_name": sec})
                summary["sp_status"] = "stored"
            if has_ads:
                summary["ads_status"] = "stored"
        else:
            if has_sp:
                summary["sp_status"] = "will_store"
            if has_ads:
                summary["ads_status"] = "will_store"

    return summary


def print_summary(results: list[dict[str, str]]) -> None:
    header = f"{'ACTION':<10} {'CLIENT_ID':<35} {'MKT':<4} {'SP API':<18} {'ADS API':<25} {'NAME'}"
    print("\n" + "=" * len(header))
    print(header)
    print("=" * len(header))
    for r in results:
        print(
            f"{r['action']:<10} {r['client_id']:<35} {r['marketplace']:<4} "
            f"{r['sp_status']:<18} {r['ads_status']:<25} {r['name']}"
        )
    print("=" * len(header))

    actions = {}
    for r in results:
        actions[r["action"]] = actions.get(r["action"], 0) + 1
    print(f"\nTotal: {len(results)} clients — " + ", ".join(f"{k}: {v}" for k, v in sorted(actions.items())))


def main() -> None:
    parser = argparse.ArgumentParser(description="Import studio clients into Kalilos connector")
    parser.add_argument("--project", default="kalilos-connector-staging", help="GCP project ID")
    parser.add_argument("--source", default=SOURCE_FILE, help="Path to studio JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Preview only, no writes")
    parser.add_argument("--update", action="store_true", help="Update existing clients with diffs")
    args = parser.parse_args()

    env = "staging" if "staging" in args.project else "prod"
    os.environ["GCP_PROJECT"] = args.project
    os.environ["ENVIRONMENT"] = env

    if env == "prod" and not args.dry_run:
        confirm = input(f"⚠ You are about to write to PRODUCTION ({args.project}). Type 'yes' to confirm: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            sys.exit(1)

    with open(args.source) as f:
        entries: list[dict[str, Any]] = json.load(f)

    print(f"Source: {args.source} ({len(entries)} entries)")
    print(f"Target: {args.project} (env={env})")
    print(f"Mode:   {'DRY RUN' if args.dry_run else 'LIVE'}")
    if args.update:
        print("Update: will apply diffs to existing clients")
    print()

    db = get_db(args.project)

    results: list[dict[str, str]] = []
    for entry in entries:
        try:
            r = process_entry(entry, db, args.project, env, args.dry_run, args.update)
            results.append(r)
        except Exception as exc:
            name = entry.get("name", "???")
            print(f"  ERROR processing {name}: {exc}")
            results.append({
                "client_id": slugify(name),
                "source_id": entry.get("id", "?"),
                "name": name,
                "marketplace": entry.get("marketplace", "?"),
                "sp_status": "error",
                "ads_status": "error",
                "action": "ERROR",
            })

    print_summary(results)


if __name__ == "__main__":
    main()
