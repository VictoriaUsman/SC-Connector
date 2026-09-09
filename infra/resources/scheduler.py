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
                # Aligned to the :05 hourly Slack send: pull at :20 and :50 so a
                # fresh sync lands before the next :05 post. Orders run on both
                # slots (every 30 min); the heavier ads pull runs on the :20 slot
                # only (~45 min lead before :05). Mirrors the old 15 min (orders)
                # / 45 min (ads) buffers that the previous :30/:00-sync, :45-send
                # arrangement relied on.
                schedule="20,50 * * * *",
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
                # 5 minutes past the hour (PM request). The event data pull runs
                # at :20/:50 (see event_report_scheduler below) — :05 lands 45min
                # after the :20 ads+orders pull and 15min after the :50 orders-only
                # pull, so numbers reflect the most recent completed ingest.
                schedule="5 * * * *",
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

        daily_recap_fn = cloud_functions.get("daily-recap")
        if daily_recap_fn:
            daily_recap = gcp.cloudscheduler.Job(
                f"kalilos-{env}-daily-recap",
                name=f"kalilos-{env}-daily-recap",
                # Runs every 15 minutes; the function itself decides, per
                # client, whether "now" falls 1-3 hours past *that client's*
                # own local midnight (see functions/daily_recap/main.py's
                # `_is_due`) before doing any work. This replaced a single
                # fixed 23:00 UTC daily fire — correct for keeping Total Sales
                # from reading $0 before that day's orders report had
                # ingested, but it meant every client waited until 23:00 UTC
                # regardless of their own timezone (up to ~16h after a
                # Pacific client's own midnight). Overridable via the
                # kalilos:daily-recap-cron config.
                schedule=kalilos_config.get("daily-recap-cron") or "*/15 * * * *",
                time_zone="UTC",
                region=region,
                project=project,
                http_target=gcp.cloudscheduler.JobHttpTargetArgs(
                    uri=daily_recap_fn.url,
                    http_method="POST",
                    oidc_token=gcp.cloudscheduler.JobHttpTargetOidcTokenArgs(
                        service_account_email=scheduler_sa.email,
                    ),
                ),
                opts=opts,
            )
            jobs.append(daily_recap)

    return jobs
