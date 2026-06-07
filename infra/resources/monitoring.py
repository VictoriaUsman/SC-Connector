"""Cloud Monitoring — minimal, production-grade alerting.

Deliberately lean (no SLO platform): one log-based error metric + an alert on an
error spike, plus an uptime check on the API ``/health`` + an alert when it
fails. Notifications go to an email channel when ``kalilos:alert-email`` is set;
without it the policies still exist (visible in the console) but stay silent.
"""

from __future__ import annotations

import pulumi
import pulumi_gcp as gcp


def create(
    env: str,
    project: str,
    api_function: gcp.cloudfunctionsv2.Function,
    kalilos_config: pulumi.Config,
    depends_on: list[pulumi.Resource],
) -> dict[str, pulumi.Resource]:
    opts = pulumi.ResourceOptions(depends_on=depends_on)

    # --- Notification channel: email (optional) -----------------------------
    channels: list[pulumi.Input[str]] = []
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
            "severity>=ERROR AND ("
            f'(resource.type="cloud_run_revision" AND resource.labels.service_name:"kalilos-{env}-") '
            'OR resource.type="workflows.googleapis.com/Workflow")'
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
    error_threshold = float(kalilos_config.get("alert-error-threshold") or "5")

    def _error_condition(resource_type: str, label: str):
        return gcp.monitoring.AlertPolicyConditionArgs(
            display_name=f"{label} ERROR logs > {error_threshold:g} in 5 min",
            condition_threshold=gcp.monitoring.AlertPolicyConditionConditionThresholdArgs(
                filter=error_metric.name.apply(
                    lambda n: (
                        f'metric.type="logging.googleapis.com/user/{n}" '
                        f'AND resource.type="{resource_type}"'
                    )
                ),
                comparison="COMPARISON_GT",
                threshold_value=error_threshold,
                duration="0s",
                aggregations=[
                    gcp.monitoring.AlertPolicyConditionConditionThresholdAggregationArgs(
                        alignment_period="300s",
                        per_series_aligner="ALIGN_SUM",
                    )
                ],
                trigger=gcp.monitoring.AlertPolicyConditionConditionThresholdTriggerArgs(
                    count=1,
                ),
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
                    trigger=gcp.monitoring.AlertPolicyConditionConditionThresholdTriggerArgs(
                        count=1,
                    ),
                ),
            )
        ],
        opts=opts,
    )

    return {
        "error_metric": error_metric,
        "error_alert": error_alert,
        "health_uptime": health_uptime,
        "uptime_alert": uptime_alert,
    }
