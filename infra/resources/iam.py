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
        "roles/secretmanager.secretAccessor",  # Read secrets
        "roles/workflows.invoker",         # Start workflow executions
        "roles/logging.logWriter",         # Cloud Logging
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
    gcp.projects.IAMMember(
        f"kalilos-{env}-wf-logWriter",
        project=project,
        role="roles/logging.logWriter",
        member=pulumi.Output.concat("serviceAccount:", workflow_sa.email),
        opts=opts,
    )

    return {
        "functions": functions_sa,
        "workflow": workflow_sa,
        "scheduler": scheduler_sa,
    }


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
    for fn_name in ("auth", "create-report", "poll-status", "download-upload"):
        gcp.cloudrunv2.ServiceIamMember(
            f"kalilos-{env}-{fn_name}-wf-invoker",
            name=cloud_functions[fn_name].service_config.service,
            location=region,
            project=project,
            role="roles/run.invoker",
            member=pulumi.Output.concat("serviceAccount:", workflow_sa.email),
        )

    # Scheduler SA invokes the scheduler function
    gcp.cloudrunv2.ServiceIamMember(
        f"kalilos-{env}-scheduler-fn-invoker",
        name=cloud_functions["scheduler"].service_config.service,
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
