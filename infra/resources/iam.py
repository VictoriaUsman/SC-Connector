"""Service accounts and IAM bindings."""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp


def create_service_accounts(
    env: str,
    project: str,
    depends_on: list[pulumi.Resource],
) -> dict[str, gcp.serviceaccount.Account]:
    """Create dedicated service accounts for functions, workflow, and scheduler."""
    opts = pulumi.ResourceOptions(depends_on=depends_on)

    functions_sa = gcp.serviceaccount.Account(
        f"kalilos-{env}-functions-sa",
        account_id=f"kalilos-{env}-functions",
        display_name=f"Kalilos {env} — Cloud Functions",
        project=project,
        opts=opts,
    )

    workflow_sa = gcp.serviceaccount.Account(
        f"kalilos-{env}-workflow-sa",
        account_id=f"kalilos-{env}-workflow",
        display_name=f"Kalilos {env} — Cloud Workflow",
        project=project,
        opts=opts,
    )

    scheduler_sa = gcp.serviceaccount.Account(
        f"kalilos-{env}-scheduler-sa",
        account_id=f"kalilos-{env}-scheduler",
        display_name=f"Kalilos {env} — Cloud Scheduler",
        project=project,
        opts=opts,
    )

    # --- Project-level roles for the functions SA ---
    for role in [
        "roles/datastore.user",           # Firestore read/write
        "roles/secretmanager.admin",       # Create/read/manage secrets (connect flow)
        "roles/workflows.invoker",         # Start workflow executions
        "roles/logging.logWriter",         # Cloud Logging
        "roles/bigquery.dataEditor",       # Insert/update BQ tables (ingestion)
        "roles/bigquery.jobUser",          # Run BQ load/query jobs
    ]:
        role_short = role.split("/")[-1]
        gcp.projects.IAMMember(
            f"kalilos-{env}-fn-{role_short}",
            project=project,
            role=role,
            member=pulumi.Output.concat("serviceAccount:", functions_sa.email),
            opts=opts,
        )

    # --- Project-level roles for the workflow SA ---
    # (roles/datastore.user removed — report_flow.yaml no longer touches
    # Firestore; see bind_workflow_secret_access below for its replacement,
    # a resource-scoped grant on just the Supabase service-role key.)
    for role in [
        "roles/logging.logWriter",
    ]:
        role_short = role.split("/")[-1]
        gcp.projects.IAMMember(
            f"kalilos-{env}-wf-{role_short}",
            project=project,
            role=role,
            member=pulumi.Output.concat("serviceAccount:", workflow_sa.email),
            opts=opts,
        )

    return {
        "functions": functions_sa,
        "workflow": workflow_sa,
        "scheduler": scheduler_sa,
    }


def bind_workflow_secret_access(
    env: str,
    project: str,
    workflow_sa: gcp.serviceaccount.Account,
    supabase_secret: gcp.secretmanager.Secret,
) -> None:
    """Grant the workflow SA read access to the Supabase service-role key —
    scoped to just this one secret, not project-wide (unlike the functions
    SA's roles/secretmanager.admin, which manages the connect-flow secrets
    across the whole project). Replaces the workflow SA's former
    roles/datastore.user now that report_flow.yaml no longer touches
    Firestore."""
    gcp.secretmanager.SecretIamMember(
        f"kalilos-{env}-workflow-supabase-key-accessor",
        secret_id=supabase_secret.secret_id,
        project=project,
        role="roles/secretmanager.secretAccessor",
        member=pulumi.Output.concat("serviceAccount:", workflow_sa.email),
    )


def bind_invokers(
    env: str,
    project: str,
    region: str,
    cloud_functions: dict[str, gcp.cloudfunctionsv2.Function],
    service_accounts: dict[str, gcp.serviceaccount.Account],
) -> None:
    """Grant invocation permissions: workflow -> pipeline fns, scheduler -> scheduler fn, public -> api fn."""
    workflow_sa = service_accounts["workflow"]
    scheduler_sa = service_accounts["scheduler"]

    # Workflow SA invokes pipeline functions (Cloud Functions v2 = Cloud Run under the hood)
    for fn_name in ("auth", "create-report", "poll-status", "download-upload", "ingest-bigquery", "fetch-api"):
        gcp.cloudrunv2.ServiceIamMember(
            f"kalilos-{env}-{fn_name}-wf-invoker",
            name=cloud_functions[fn_name].service_config.service,
            location=region,
            project=project,
            role="roles/run.invoker",
            member=pulumi.Output.concat("serviceAccount:", workflow_sa.email),
        )

    # Scheduler SA invokes the scheduler function and event-related functions
    for fn_name in ("scheduler", "event-report-scheduler", "slack-bot", "daily-recap"):
        gcp.cloudrunv2.ServiceIamMember(
            f"kalilos-{env}-{fn_name}-fn-invoker",
            name=cloud_functions[fn_name].service_config.service,
            location=region,
            project=project,
            role="roles/run.invoker",
            member=pulumi.Output.concat("serviceAccount:", scheduler_sa.email),
        )

    # API function is publicly accessible (auth handled in function code).
    # Cloud Functions v2 runs on Cloud Run — public access requires
    # roles/run.invoker on the underlying Cloud Run service, not
    # roles/cloudfunctions.invoker on the function resource.
    api_fn = cloud_functions["api"]
    gcp.cloudrunv2.ServiceIamMember(
        f"kalilos-{env}-api-public-invoker",
        name=api_fn.service_config.service,
        location=region,
        project=project,
        role="roles/run.invoker",
        member="allUsers",
    )
