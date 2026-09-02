#!/usr/bin/env python3
"""Seed Supabase with a test client for e2e testing.

Usage:
    SUPABASE_DB_URL=postgresql://... python scripts/seed-test-client.py [--client-id ID] [--client-name NAME] [--env staging]

Requires: psycopg2-binary
    pip install psycopg2-binary
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

from shared.db import upsert_client


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Supabase with test client data")
    parser.add_argument("--client-id", default="test-client")
    parser.add_argument("--client-name", default="Test Client")
    parser.add_argument("--env", default=os.environ.get("ENVIRONMENT", "staging"))
    args = parser.parse_args()

    if not os.environ.get("SUPABASE_DB_URL"):
        print("SUPABASE_DB_URL is not set.", file=sys.stderr)
        sys.exit(1)

    env = args.env

    # --- Test client ---
    client_data = {
        "name": args.client_name,
        "marketplaces": ["US"],
        "is_active": True,
        "sp_api_secret_name": f"kalilos-{env}-sp-api-{args.client_id}",
        "ads_api_secret_name": f"kalilos-{env}-ads-api-{args.client_id}",
    }
    upsert_client(args.client_id, client_data)
    print(f"✓ Client '{args.client_id}' upserted in Supabase clients table")
    print(f"  SP API secret ref: kalilos-{env}-sp-api-{args.client_id}")
    print(f"  Ads API secret ref: kalilos-{env}-ads-api-{args.client_id}")

    # --- Print next steps ---
    print()
    print("Next steps — store Amazon credentials in Secret Manager:")
    print()
    print(f"  # 1. App-level SP API credentials (shared across all clients)")
    print(f"  make secret-set NAME=kalilos-{env}-sp-api-app-credentials \\")
    print(f"    VALUE='{{\"client_id\":\"amzn1.application-oa2-client.YOUR_APP_ID\",\"client_secret\":\"YOUR_APP_SECRET\"}}'")
    print()
    print(f"  # 2. Client-specific SP API refresh token")
    print(f"  make secret-set NAME=kalilos-{env}-sp-api-{args.client_id} \\")
    print(f"    VALUE='{{\"refresh_token\":\"Atzr|YOUR_REFRESH_TOKEN\"}}'")
    print()
    print(f"  # 3. (Optional) Ads API credentials")
    print(f"  make secret-set NAME=kalilos-{env}-ads-api-app-credentials \\")
    print(f"    VALUE='{{\"client_id\":\"YOUR_ADS_CLIENT_ID\",\"client_secret\":\"YOUR_ADS_SECRET\"}}'")
    print()
    print(f"  make secret-set NAME=kalilos-{env}-ads-api-{args.client_id} \\")
    print(f"    VALUE='{{\"refresh_token\":\"Atzr|YOUR_ADS_REFRESH_TOKEN\",\"profile_id\":\"YOUR_PROFILE_ID\"}}'")


if __name__ == "__main__":
    main()
