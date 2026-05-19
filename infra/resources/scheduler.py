"""Cloud Scheduler jobs — triggers scheduler, event report scheduler, and hourly bot."""

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
    cloud_functions: dict[str, gcp.cloudfunctionsv2.Function] | None = None,
) -> list[gcp.cloudscheduler.Job]:
    """Create Cloud Scheduler jobs. Returns list of all scheduler jobs."""
    opts = pulumi.ResourceOptions(depends_on=depends_on)
    cron = kalilos_config.get("scheduler-cron") or "*/30 * * * *"
    timezone = kalilos_config.get("scheduler-timezone") or "America/New_York"

    jobs: list[gcp.cloudscheduler.Job] = []

    report_scheduler = gcp.cloudscheduler.Job(
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
        opts=opts,
    )
    jobs.append(report_scheduler)

    if cloud_functions:
        event_scheduler_fn = cloud_functions.get("event-report-scheduler")
        if event_scheduler_fn:
            event_report_scheduler = gcp.cloudscheduler.Job(
                f"kalilos-{env}-event-report-scheduler",
                name=f"kalilos-{env}-event-report-scheduler",
                schedule="*/30 * * * *",
                time_zone="UTC",
                region=region,
                project=project,
                http_target=gcp.cloudscheduler.JobHttpTargetArgs(
                    uri=event_scheduler_fn.url,
                    http_method="POST",
                    oidc_token=gcp.cloudscheduler.JobHttpTargetOidcTokenArgs(
                        service_account_email=scheduler_sa.email,
                    ),
                ),
                opts=opts,
            )
            jobs.append(event_report_scheduler)

        slack_bot_fn = cloud_functions.get("slack-bot")
        if slack_bot_fn:
            hourly_bot = gcp.cloudscheduler.Job(
                f"kalilos-{env}-hourly-bot",
                name=f"kalilos-{env}-hourly-bot",
                schedule="45 * * * *",
                time_zone="UTC",
                region=region,
                project=project,
                http_target=gcp.cloudscheduler.JobHttpTargetArgs(
                    uri=slack_bot_fn.url,
                    http_method="POST",
                    oidc_token=gcp.cloudscheduler.JobHttpTargetOidcTokenArgs(
                        service_account_email=scheduler_sa.email,
                    ),
                ),
                opts=opts,
            )
            jobs.append(hourly_bot)

    return jobs
