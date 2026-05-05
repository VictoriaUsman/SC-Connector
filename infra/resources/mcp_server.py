"""Cloud Run service for the Kalilos MCP server."""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp


def create(
    env: str,
    project: str,
    region: str,
    api_fn: gcp.cloudfunctionsv2.Function,
    depends_on: list[pulumi.Resource],
) -> gcp.cloudrunv2.Service:
    """Deploy the MCP server as a Cloud Run service.

    The service proxies requests to the existing REST API Cloud Function.
    Authentication is handled by a bearer token (MCP_API_KEY env var).

    Secret env vars are read from Pulumi config (encrypted in state) rather
    than Cloud Run secret references to work around a pulumi-gcp provider bug
    where value_source env vars also send an empty value field, causing a
    "oneof field 'values' is already set" 400 error from the Cloud Run API.
    """
    opts = pulumi.ResourceOptions(depends_on=depends_on)
    config = pulumi.Config("kalilos")

    resource_name = f"kalilos-{env}-mcp"

    # -- Artifact Registry repository for MCP server images ----------------

    repo = gcp.artifactregistry.Repository(
        f"{resource_name}-repo",
        repository_id=f"{resource_name}-repo",
        location=region,
        project=project,
        format="DOCKER",
        description="Container images for the Kalilos MCP server",
        opts=opts,
    )

    # -- Secrets (kept for storage, not referenced from Cloud Run env) -----
    # NOTE: Cloud Run env vars read values from Pulumi config instead of
    # secret references due to a pulumi-gcp provider bug with value_source.

    gcp.secretmanager.Secret(
        f"{resource_name}-api-key",
        secret_id=f"{resource_name}-api-key",
        replication=gcp.secretmanager.SecretReplicationArgs(
            auto=gcp.secretmanager.SecretReplicationAutoArgs(),
        ),
        project=project,
        opts=opts,
    )

    gcp.secretmanager.Secret(
        f"kalilos-{env}-api-key",
        secret_id=f"kalilos-{env}-api-key",
        replication=gcp.secretmanager.SecretReplicationArgs(
            auto=gcp.secretmanager.SecretReplicationAutoArgs(),
        ),
        project=project,
        opts=opts,
    )

    # -- Cloud Run service -------------------------------------------------

    image = config.get("mcp-image") or f"{region}-docker.pkg.dev/{project}/{resource_name}-repo/{resource_name}:latest"

    service = gcp.cloudrunv2.Service(
        resource_name,
        name=resource_name,
        location=region,
        project=project,
        ingress="INGRESS_TRAFFIC_ALL",
        template=gcp.cloudrunv2.ServiceTemplateArgs(
            scaling=gcp.cloudrunv2.ServiceTemplateScalingArgs(
                min_instance_count=0,
                max_instance_count=5,
            ),
            timeout="300s",
            containers=[gcp.cloudrunv2.ServiceTemplateContainerArgs(
                image=image,
                ports=gcp.cloudrunv2.ServiceTemplateContainerPortsArgs(
                    container_port=8080,
                ),
                resources=gcp.cloudrunv2.ServiceTemplateContainerResourcesArgs(
                    limits={"memory": "512Mi", "cpu": "1"},
                    cpu_idle=True,
                ),
                envs=[
                    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                        name="KALILOS_API_URL",
                        value=api_fn.url,
                    ),
                    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                        name="KALILOS_API_KEY",
                        value=config.require_secret("api-key"),
                    ),
                    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                        name="MCP_API_KEY",
                        value=config.require_secret("mcp-api-key"),
                    ),
                ],
            )],
        ),
        opts=opts,
    )

    # -- IAM: allow allUsers to invoke (auth is in-app via bearer token) ---

    gcp.cloudrunv2.ServiceIamMember(
        f"{resource_name}-public-invoker",
        name=service.name,
        location=region,
        project=project,
        role="roles/run.invoker",
        member="allUsers",
    )

    # -- Exports -----------------------------------------------------------

    pulumi.export("mcp_server_url", service.uri)
    pulumi.export("mcp_repo", repo.name)

    return service
