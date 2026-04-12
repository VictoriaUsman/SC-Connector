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
    Authentication is handled by a bearer token (MCP_API_KEY env var) loaded
    from Secret Manager.
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

    # -- Secret for the MCP API key ----------------------------------------

    mcp_api_key_secret = gcp.secretmanager.Secret(
        f"{resource_name}-api-key",
        secret_id=f"{resource_name}-api-key",
        replication=gcp.secretmanager.SecretReplicationArgs(
            auto=gcp.secretmanager.SecretReplicationAutoArgs(),
        ),
        project=project,
        opts=opts,
    )

    # -- Secret for the REST API key (used by MCP server to call API) ------

    api_key_secret = gcp.secretmanager.Secret(
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
                        value_source=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceArgs(
                            secret_key_ref=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                secret=api_key_secret.secret_id,
                                version="latest",
                            ),
                        ),
                    ),
                    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                        name="MCP_API_KEY",
                        value_source=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceArgs(
                            secret_key_ref=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceSecretKeyRefArgs(
                                secret=mcp_api_key_secret.secret_id,
                                version="latest",
                            ),
                        ),
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
