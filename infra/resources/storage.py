"""GCS bucket for Cloud Function source archives."""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp


def create_source_bucket(
    env: str,
    project: str,
    region: str,
    depends_on: list[pulumi.Resource],
) -> gcp.storage.Bucket:
    """Single bucket for all function source zips. Named by project for global uniqueness."""
    return gcp.storage.Bucket(
        f"kalilos-{env}-fn-source",
        name=f"{project}-fn-source",
        location=region,
        project=project,
        uniform_bucket_level_access=True,
        force_destroy=True,
        opts=pulumi.ResourceOptions(depends_on=depends_on),
    )
