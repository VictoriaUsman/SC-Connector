"""Firestore database and composite indexes."""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp


def create(
    env: str,
    project: str,
    depends_on: list[pulumi.Resource],
) -> gcp.firestore.Database:
    """Create Firestore native-mode database with key composite indexes."""
    opts = pulumi.ResourceOptions(depends_on=depends_on)

    db = gcp.firestore.Database(
        f"kalilos-{env}-firestore",
        name="(default)",
        location_id="nam5",
        type="FIRESTORE_NATIVE",
        project=project,
        opts=opts,
    )

    # Jobs: query by client_id ordered by started_at
    gcp.firestore.Index(
        f"kalilos-{env}-idx-jobs-client-started",
        database=db.name,
        collection="jobs",
        fields=[
            gcp.firestore.IndexFieldArgs(field_path="client_id", order="ASCENDING"),
            gcp.firestore.IndexFieldArgs(field_path="started_at", order="DESCENDING"),
        ],
        project=project,
    )

    # Jobs: query by status ordered by started_at
    gcp.firestore.Index(
        f"kalilos-{env}-idx-jobs-status-started",
        database=db.name,
        collection="jobs",
        fields=[
            gcp.firestore.IndexFieldArgs(field_path="status", order="ASCENDING"),
            gcp.firestore.IndexFieldArgs(field_path="started_at", order="DESCENDING"),
        ],
        project=project,
    )

    # Schedules: query active schedules by next_run_at (for scheduler fan-out)
    gcp.firestore.Index(
        f"kalilos-{env}-idx-schedules-active-nextrun",
        database=db.name,
        collection="schedules",
        fields=[
            gcp.firestore.IndexFieldArgs(field_path="is_active", order="ASCENDING"),
            gcp.firestore.IndexFieldArgs(field_path="next_run_at", order="ASCENDING"),
        ],
        project=project,
    )

    return db
