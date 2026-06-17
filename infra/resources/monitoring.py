"""Cloud Monitoring — minimal, production-grade alerting.

Deliberately lean (no SLO platform): one log-based error metric + an alert on a
sustained error spike, plus an uptime check on the API ``/health`` + an alert
when it fails. Both policies auto-close and are rate-limited by requiring a
sustained condition, so an ongoing failure does not re-page every evaluation.

Notifications go to Slack via a Pub/Sub channel + the ``alert_notifier`` Cloud
Function (reusing the Slack bot token), and to email when ``kalilos:alert-email``
is set.
"""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp

from resources.functions import _build_source_archive


def create(
    env: str,
    project: str,
    region: str,
    source_bucket: gcp.storage.Bucket,
    functions_sa: gcp.serviceaccount.Account,
    api_function: gcp.cloudfunctionsv2.Function,
    kalilos_config: pulumi.Config,
    depends_on: list[pulumi.Resource],
) -> dict[str, pulumi.Resource]:
    opts = pulumi.ResourceOptions(depends_on=depends_on)

    # --- Slack routing: Pub/Sub topic + notifier function -------------------
    channels: list[pulumi.Input[str]] = []
    slack_channel = _create_slack_routing(
        env, project, region, source_bucket, functions_sa, kalilos_config, opts,
    )
    channels.append(slack_channel.id)

    # --- Notification channel: email (optional) -----------------------------
    alert_email = kalilos_config.get("alert-email")
    if alert_email:
        email_channel = gcp.monitoring.NotificationChannel(
            f"kalilos-{env}-email-channel",
            display_name=f"Kalilos {env} alerts",
            type="email",
            project=project,
            labels={"email_address": alert_email},
            opts=opts,
        )
        channels.append(email_channel.id)

    notification_channels = channels or None

    # --- Log-based metric: ERROR+ across our functions + the report workflow ---
    error_metric = gcp.logging.Metric(
        f"kalilos-{env}-error-logs",
        name=f"kalilos-{env}-error-logs",
        project=project,
        filter=(
            # Count the *source* error only. The workflow re-logs the best-effort
            # BigQuery ingestion step's failure (phase="ingest_bigquery"), which the
            # ingest function already logs — excluding it kills the 2x double-count.
            "severity>=ERROR AND ("
            f'(resource.type="cloud_run_revision" AND resource.labels.service_name:"kalilos-{env}-") '
            'OR (resource.type="workflows.googleapis.com/Workflow" '
            'AND NOT jsonPayload.phase="ingest_bigquery")'
            ")"
        ),
        metric_descriptor=gcp.logging.MetricMetricDescriptorArgs(
            metric_kind="DELTA",
            value_type="INT64",
            display_name=f"Kalilos {env} error logs",
        ),
        opts=opts,
    )

    # --- Alert: error-log spike --------------------------------------------
    # A log-based-metric alert filter must restrict resource.type, but our metric
    # spans two (Cloud Run functions + the workflow). Use one OR'd condition per
    # resource type so a spike in either source fires the alert.
    error_threshold = float(kalilos_config.get("alert-error-threshold") or "10")

    def _error_condition(resource_type: str, label: str):
        return gcp.monitoring.AlertPolicyConditionArgs(
            display_name=f"{label} ERROR logs > {error_threshold:g} sustained 5 min",
            condition_threshold=gcp.monitoring.AlertPolicyConditionConditionThresholdArgs(
                filter=error_metric.name.apply(
                    lambda n: (
                        f'metric.type="logging.googleapis.com/user/{n}" '
                        f'AND resource.type="{resource_type}"'
                    )
                ),
                comparison="COMPARISON_GT",
                threshold_value=error_threshold,
                # Require the spike to persist across a full alignment window so a
                # single noisy 5-min bucket does not page (the old duration="0s"
                # re-fired every evaluation).
                duration="300s",
                aggregations=[
                    gcp.monitoring.AlertPolicyConditionConditionThresholdAggregationArgs(
                        alignment_period="300s",
                        per_series_aligner="ALIGN_SUM",
                    )
                ],
            ),
        )

    error_alert = gcp.monitoring.AlertPolicy(
        f"kalilos-{env}-error-rate-alert",
        display_name=f"Kalilos {env} — error log spike",
        project=project,
        combiner="OR",
        notification_channels=notification_channels,
        conditions=[
            _error_condition("cloud_run_revision", "Functions"),
            _error_condition("workflows.googleapis.com/Workflow", "Workflow"),
        ],
        # Auto-close the incident after 30 min healthy so a transient spike does
        # not stay open and re-notify; without this the policy re-paged endlessly.
        alert_strategy=gcp.monitoring.AlertPolicyAlertStrategyArgs(
            auto_close="1800s",
        ),
        documentation=gcp.monitoring.AlertPolicyDocumentationArgs(
            content=(
                "Sustained spike of ERROR logs from Kalilos functions or workflow "
                "failures. Check Cloud Logging for the failing service. Excludes "
                "best-effort BigQuery ingestion re-logs and expected per-client "
                "conditions (Ads 3P unauthorized, data-not-yet-ingested), which are "
                "logged at WARNING."
            ),
            mime_type="text/markdown",
        ),
        opts=opts,
    )

    # --- Uptime check on the API /health -----------------------------------
    # Gen2 Cloud Functions on *.cloudfunctions.net are served under a path prefix
    # (the function name, e.g. /kalilos-staging-api), so derive BOTH the host and
    # that prefix from the function URL - probing /health on the bare host 404s.
    def _host(u: str) -> str:
        return u.split("://", 1)[-1].split("/", 1)[0]

    def _health_path(u: str) -> str:
        rest = u.split("://", 1)[-1]
        base = rest[len(_host(u)):].rstrip("/")  # path prefix, "" for run.app-style
        return f"{base}/health"

    api_host = api_function.url.apply(_host)
    health_uptime = gcp.monitoring.UptimeCheckConfig(
        f"kalilos-{env}-api-health",
        display_name=f"Kalilos {env} API /health",
        project=project,
        timeout="10s",
        period="300s",
        http_check=gcp.monitoring.UptimeCheckConfigHttpCheckArgs(
            path=api_function.url.apply(_health_path),
            port=443,
            use_ssl=True,
            request_method="GET",
        ),
        monitored_resource=gcp.monitoring.UptimeCheckConfigMonitoredResourceArgs(
            type="uptime_url",
            labels={"project_id": project, "host": api_host},
        ),
        opts=opts,
    )

    # --- Alert: API /health uptime failing ---------------------------------
    uptime_alert = gcp.monitoring.AlertPolicy(
        f"kalilos-{env}-api-down-alert",
        display_name=f"Kalilos {env} — API /health failing",
        project=project,
        combiner="OR",
        notification_channels=notification_channels,
        conditions=[
            gcp.monitoring.AlertPolicyConditionArgs(
                display_name="API /health uptime check failing",
                condition_threshold=gcp.monitoring.AlertPolicyConditionConditionThresholdArgs(
                    filter=health_uptime.uptime_check_id.apply(
                        lambda cid: (
                            'metric.type="monitoring.googleapis.com/uptime_check/check_passed" '
                            'AND resource.type="uptime_url" '
                            f'AND metric.label.check_id="{cid}"'
                        )
                    ),
                    comparison="COMPARISON_LT",
                    threshold_value=1,
                    duration="300s",
                    aggregations=[
                        gcp.monitoring.AlertPolicyConditionConditionThresholdAggregationArgs(
                            alignment_period="300s",
                            per_series_aligner="ALIGN_FRACTION_TRUE",
                        )
                    ],
                ),
            )
        ],
        alert_strategy=gcp.monitoring.AlertPolicyAlertStrategyArgs(
            auto_close="1800s",
        ),
        opts=opts,
    )

    return {
        "error_metric": error_metric,
        "error_alert": error_alert,
        "health_uptime": health_uptime,
        "uptime_alert": uptime_alert,
    }


def _create_slack_routing(
    env: str,
    project: str,
    region: str,
    source_bucket: gcp.storage.Bucket,
    functions_sa: gcp.serviceaccount.Account,
    kalilos_config: pulumi.Config,
    opts: pulumi.ResourceOptions,
) -> gcp.monitoring.NotificationChannel:
    """Create the Pub/Sub topic, alert_notifier function, and Slack channel.

    Monitoring publishes incidents to the topic; the Pub/Sub-triggered
    ``alert_notifier`` function formats and posts them to Slack using the shared
    Slack bot token. Returns the Pub/Sub notification channel.
    """
    project_info = gcp.organizations.get_project(project_id=project)
    project_number = project_info.number

    topic = gcp.pubsub.Topic(
        f"kalilos-{env}-alerts",
        name=f"kalilos-{env}-alerts",
        project=project,
        opts=opts,
    )

    gcp.pubsub.TopicIAMMember(
        f"kalilos-{env}-alerts-monitoring-publisher",
        topic=topic.name,
        project=project,
        role="roles/pubsub.publisher",
        member=(
            f"serviceAccount:service-{project_number}"
            "@gcp-sa-monitoring-notification.iam.gserviceaccount.com"
        ),
        opts=opts,
    )

    # Eventarc/Pub/Sub trigger IAM for the gen2 notifier function.
    gcp.projects.IAMMember(
        f"kalilos-{env}-pubsub-token-creator",
        project=project,
        role="roles/iam.serviceAccountTokenCreator",
        member=(
            f"serviceAccount:service-{project_number}"
            "@gcp-sa-pubsub.iam.gserviceaccount.com"
        ),
        opts=opts,
    )
    gcp.projects.IAMMember(
        f"kalilos-{env}-notifier-eventarc-receiver",
        project=project,
        role="roles/eventarc.eventReceiver",
        member=pulumi.Output.concat("serviceAccount:", functions_sa.email),
        opts=opts,
    )

    notifier_source = gcp.storage.BucketObject(
        f"kalilos-{env}-alert-notifier-source",
        bucket=source_bucket.name,
        source=_build_source_archive("alert_notifier"),
        opts=opts,
    )

    notifier_fn = gcp.cloudfunctionsv2.Function(
        f"kalilos-{env}-alert-notifier",
        name=f"kalilos-{env}-alert-notifier",
        location=region,
        project=project,
        build_config=gcp.cloudfunctionsv2.FunctionBuildConfigArgs(
            runtime="python312",
            entry_point="handler",
            source=gcp.cloudfunctionsv2.FunctionBuildConfigSourceArgs(
                storage_source=gcp.cloudfunctionsv2.FunctionBuildConfigSourceStorageSourceArgs(
                    bucket=source_bucket.name,
                    object=notifier_source.name,
                ),
            ),
        ),
        service_config=gcp.cloudfunctionsv2.FunctionServiceConfigArgs(
            available_memory="256Mi",
            timeout_seconds=60,
            max_instance_count=3,
            min_instance_count=0,
            service_account_email=functions_sa.email,
            ingress_settings="ALLOW_INTERNAL_ONLY",
            environment_variables={
                "GCP_PROJECT": project,
                "ENVIRONMENT": env,
                "ALERT_SLACK_CHANNEL": kalilos_config.get("alert-slack-channel") or "",
            },
        ),
        event_trigger=gcp.cloudfunctionsv2.FunctionEventTriggerArgs(
            trigger_region=region,
            event_type="google.cloud.pubsub.topic.v1.messagePublished",
            pubsub_topic=topic.id,
            retry_policy="RETRY_POLICY_RETRY",
            service_account_email=functions_sa.email,
        ),
        opts=opts,
    )

    gcp.cloudrunv2.ServiceIamMember(
        f"kalilos-{env}-alert-notifier-invoker",
        name=notifier_fn.service_config.service,
        location=region,
        project=project,
        role="roles/run.invoker",
        member=pulumi.Output.concat("serviceAccount:", functions_sa.email),
        opts=opts,
    )

    return gcp.monitoring.NotificationChannel(
        f"kalilos-{env}-slack-channel",
        display_name=f"Kalilos {env} alerts — Slack",
        type="pubsub",
        project=project,
        labels={"topic": topic.id},
        opts=opts,
    )
