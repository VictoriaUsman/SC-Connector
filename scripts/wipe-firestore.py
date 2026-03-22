#!/usr/bin/env python3
"""Delete all documents from specified Firestore collections.

Usage:
    python scripts/wipe-firestore.py [--project PROJECT] [--collections schedules jobs _drive_folder_locks]
    python scripts/wipe-firestore.py --all   # wipe schedules, jobs, _drive_folder_locks, and clients

Requires: google-cloud-firestore
"""

from __future__ import annotations

import argparse
import os
import sys

from google.cloud import firestore

DEFAULT_COLLECTIONS = ["schedules", "jobs", "_drive_folder_locks"]
ALL_COLLECTIONS = DEFAULT_COLLECTIONS + ["clients"]

BATCH_SIZE = 200


def delete_collection(db: firestore.Client, name: str) -> int:
    """Delete all documents in a collection. Returns count of deleted docs."""
    coll = db.collection(name)
    deleted = 0

    while True:
        docs = list(coll.limit(BATCH_SIZE).stream())
        if not docs:
            break

        batch = db.batch()
        for doc in docs:
            batch.delete(doc.reference)
        batch.commit()
        deleted += len(docs)

    return deleted


def main() -> None:
    parser = argparse.ArgumentParser(description="Wipe Firestore collections")
    parser.add_argument(
        "--project",
        default=os.environ.get("GCP_PROJECT", "kalilos-connector-staging"),
    )
    parser.add_argument(
        "--collections",
        nargs="*",
        default=None,
        help=f"Collections to wipe (default: {DEFAULT_COLLECTIONS})",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Wipe all collections including clients",
    )
    parser.add_argument("--yes", action="store_true", help="Skip confirmation")
    args = parser.parse_args()

    collections = ALL_COLLECTIONS if args.all else (args.collections or DEFAULT_COLLECTIONS)

    print(f"Project:     {args.project}")
    print(f"Collections: {', '.join(collections)}")
    print()

    if not args.yes:
        answer = input("Delete ALL documents in these collections? [y/N] ")
        if answer.lower() != "y":
            print("Aborted.")
            sys.exit(0)

    db = firestore.Client(project=args.project)

    for name in collections:
        count = delete_collection(db, name)
        print(f"  {name}: {count} document{'s' if count != 1 else ''} deleted")

    print("\nDone.")


if __name__ == "__main__":
    main()
