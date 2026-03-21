"""Cloud Scheduler job — triggers the scheduler function on a cron."""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp


def create(
    env: str,
    project: str,
    region: str,
    scheduler_fn: gcp.cloudfunctionsv2.Function,
    scheduler_sa: gcp.serviceaccount.Account,
    kalilos_config: pulumi.Config,
    depends_on: list[pulumi.Resource],
) -> gcp.cloudscheduler.Job:
    """Create Cloud Scheduler cron that triggers the scheduler function via OIDC-authenticated HTTP."""
    cron = kalilos_config.get("scheduler-cron") or "*/30 * * * *"
    timezone = kalilos_config.get("scheduler-timezone") or "America/New_York"

    return gcp.cloudscheduler.Job(
        f"kalilos-{env}-report-scheduler",
        name=f"kalilos-{env}-report-scheduler",
        schedule=cron,
        time_zone=timezone,
        region=region,
        project=project,
        http_target=gcp.cloudscheduler.JobHttpTargetArgs(
            uri=scheduler_fn.url,
            http_method="POST",
            oidc_token=gcp.cloudscheduler.JobHttpTargetOidcTokenArgs(
                service_account_email=scheduler_sa.email,
            ),
        ),
        opts=pulumi.ResourceOptions(depends_on=depends_on),
    )
