"""Alert notifier — forward GCP Monitoring alerts to Slack.

Cloud Monitoring alert policies notify a Pub/Sub notification channel, which
delivers the incident JSON to this function. We format it into a Block Kit
message and post it to the ops Slack channel using the shared Slack client
(same bot token as the report bots).

This keeps alerting fully in code: policies, channel, and delivery all live in
Pulumi + this function, instead of hand-created console policies emailing an
inbox.
"""

from __future__ import annotations

import base64
import json
import logging
import os

import functions_framework

from shared.logging_setup import init_logging
from shared.slack_client import post_message

logger = logging.getLogger(__name__)
init_logging("alert-notifier")

_STATE_EMOJI = {
    "open": ":rotating_light:",
    "OPEN": ":rotating_light:",
    "closed": ":white_check_mark:",
    "CLOSED": ":white_check_mark:",
}


@functions_framework.cloud_event
def handler(cloud_event) -> None:
    """Receive a Monitoring incident via Pub/Sub and post it to Slack."""
    channel_id = os.environ.get("ALERT_SLACK_CHANNEL", "").strip()
    if not channel_id:
        logger.warning("ALERT_SLACK_CHANNEL not configured — dropping alert")
        return

    incident = _parse_incident(cloud_event)
    if incident is None:
        logger.warning("Alert payload had no incident — skipping")
        return

    blocks, fallback = _build_blocks(incident)
    try:
        post_message(channel_id, blocks, fallback)
        logger.info(
            "Alert posted to Slack",
            extra={"state": incident.get("state"), "policy": incident.get("policy_name")},
        )
    except Exception:
        logger.exception("Failed to post alert to Slack")
        raise  # let Pub/Sub retry delivery


def _parse_incident(cloud_event) -> dict | None:
    """Extract the ``incident`` dict from the Pub/Sub-wrapped CloudEvent."""
    try:
        data = cloud_event.data or {}
        message = data.get("message", {})
        raw = message.get("data", "")
        if not raw:
            return None
        decoded = base64.b64decode(raw).decode("utf-8")
        payload = json.loads(decoded)
        return payload.get("incident")
    except Exception:
        logger.exception("Could not parse alert Pub/Sub payload")
        return None


def _build_blocks(incident: dict) -> tuple[list[dict], str]:
    """Build a Slack Block Kit message from a Monitoring incident."""
    state = str(incident.get("state", "open"))
    emoji = _STATE_EMOJI.get(state, ":warning:")
    policy = incident.get("policy_name") or incident.get("condition_name") or "Alert"
    summary = incident.get("summary", "")
    url = incident.get("url", "")
    env = os.environ.get("ENVIRONMENT", "")

    header = f"{emoji} *{policy}* — {state.upper()}"
    if env:
        header += f"  _({env})_"

    lines = [header]
    if summary:
        lines.append(summary)

    resource = incident.get("resource", {})
    labels = resource.get("labels", {}) if isinstance(resource, dict) else {}
    service = labels.get("service_name") or labels.get("revision_name")
    if service:
        lines.append(f"Resource: `{service}`")

    blocks: list[dict] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
    ]
    if url:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"<{url}|View incident in Cloud Monitoring>"},
        })

    return blocks, f"{policy} — {state.upper()}"
