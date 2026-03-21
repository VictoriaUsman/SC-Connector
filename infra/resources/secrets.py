"""Secret Manager secret shells (actual values added via CLI or OAuth flow)."""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp

SECRET_SHELLS = [
    "sp-api-app-credentials",
    "ads-api-app-credentials",
]


def create(
    env: str,
    project: str,
    depends_on: list[pulumi.Resource],
) -> dict[str, gcp.secretmanager.Secret]:
    """Create empty secret shells. Values are populated via `make secret-set`."""
    opts = pulumi.ResourceOptions(depends_on=depends_on)
    secrets: dict[str, gcp.secretmanager.Secret] = {}

    for name in SECRET_SHELLS:
        full_name = f"kalilos-{env}-{name}"
        secrets[name] = gcp.secretmanager.Secret(
            full_name,
            secret_id=full_name,
            replication=gcp.secretmanager.SecretReplicationArgs(
                auto=gcp.secretmanager.SecretReplicationAutoArgs(),
            ),
            project=project,
            opts=opts,
        )

    return secrets
