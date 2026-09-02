"""Cloud Workflow definition — unified report pipeline."""

from __future__ import annotations

import pathlib

import pulumi
import pulumi_gcp as gcp

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


def create(
    env: str,
    project: str,
    region: str,
    workflow_sa: gcp.serviceaccount.Account,
    cloud_functions: dict[str, gcp.cloudfunctionsv2.Function],
    supabase_secret: gcp.secretmanager.Secret,
    depends_on: list[pulumi.Resource],
) -> gcp.workflows.Workflow:
    """Deploy the report flow workflow with function URLs baked in."""
    template = (PROJECT_ROOT / "workflows" / "report_flow.yaml").read_text()
    config = pulumi.Config("kalilos")

    # Replace placeholders with actual Cloud Function URLs (and the
    # Supabase secret's id) at deploy time. The workflow YAML uses
    # __PLACEHOLDER__ syntax to avoid collision with Cloud Workflows' own
    # ${expression} syntax.
    source = pulumi.Output.all(
        auth_url=cloud_functions["auth"].url,
        create_url=cloud_functions["create-report"].url,
        poll_url=cloud_functions["poll-status"].url,
        download_url=cloud_functions["download-upload"].url,
        ingest_url=cloud_functions["ingest-bigquery"].url,
        fetch_api_url=cloud_functions["fetch-api"].url,
        secret_name=supabase_secret.secret_id,
    ).apply(
        lambda urls: (
            template
            .replace("__AUTH_FUNCTION_URL__", urls["auth_url"])
            .replace("__CREATE_REPORT_FUNCTION_URL__", urls["create_url"])
            .replace("__POLL_STATUS_FUNCTION_URL__", urls["poll_url"])
            .replace("__DOWNLOAD_UPLOAD_FUNCTION_URL__", urls["download_url"])
            .replace("__INGEST_BIGQUERY_FUNCTION_URL__", urls["ingest_url"])
            .replace("__FETCH_API_FUNCTION_URL__", urls["fetch_api_url"])
            .replace("__SUPABASE_SECRET_NAME__", urls["secret_name"])
        )
    )

    return gcp.workflows.Workflow(
        f"kalilos-{env}-report-flow",
        name=f"kalilos-{env}-report-flow",
        region=region,
        project=project,
        source_contents=source,
        service_account=workflow_sa.email,
        # SUPABASE_URL is a plain (non-secret) value reaching the workflow
        # the same way GOOGLE_CLOUD_PROJECT_ID does, except SUPABASE_URL
        # isn't a GCP-provided built-in — it has to be configured
        # explicitly. Set it with: pulumi config set kalilos:supabase-url <url>
        user_env_vars={
            "SUPABASE_URL": config.require("supabase-url"),
        },
        opts=pulumi.ResourceOptions(depends_on=depends_on),
    )
