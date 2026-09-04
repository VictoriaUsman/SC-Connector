"""Slack API client — post messages with currency-aware formatting.

Token is loaded from Secret Manager on first use and cached for the
process lifetime (same pattern as SP/Ads credentials).
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal, ROUND_HALF_UP

import requests
from google.cloud import secretmanager

from shared.config import get_environment, get_project
from shared.local_secrets import is_local_mode, resolve_secret

logger = logging.getLogger(__name__)

_slack_token: str | None = None
_sm: secretmanager.SecretManagerServiceClient | None = None

SLACK_API_BASE = "https://slack.com/api"

# Slack error codes that mean "the message can never be delivered to this
# channel until a human fixes the channel config" (the Kalilos app is not a
# member, the channel was deleted, or it is archived). These are operational
# misconfigurations, not bot defects, so callers should surface them as
# actionable WARNINGs (with a re-invite hint) rather than paging ERRORs.
CHANNEL_CONFIG_ERRORS = frozenset({
    "not_in_channel",
    "channel_not_found",
    "is_archived",
})


class SlackApiError(RuntimeError):
    """Raised when the Slack API returns ``ok: false``.

    Subclasses ``RuntimeError`` so existing broad ``except Exception`` handlers
    keep working, while ``code`` lets callers branch on the specific Slack error
    (e.g. ``not_in_channel``). ``is_channel_config_error`` flags the subset that
    a human must fix by inviting the app / unarchiving the channel.
    """

    def __init__(self, code: str, channel_id: str | None = None) -> None:
        super().__init__(f"Slack API error: {code}")
        self.code = code
        self.channel_id = channel_id

    @property
    def is_channel_config_error(self) -> bool:
        return self.code in CHANNEL_CONFIG_ERRORS


# ---------------------------------------------------------------------------
# Channel resolution
# ---------------------------------------------------------------------------

def resolve_target_channels(config: dict) -> list[str]:
    """Resolve which Slack channel(s) a bot config should post to.

    Test Mode (``use_test_channel``) overrides the whole channel list with a
    single test channel, so a misconfigured multi-channel broadcast can't leak
    into production channels while testing. Otherwise every channel in
    ``channels`` (the multi-channel shape) is a target, falling back to the
    legacy single ``slack_channel_id`` field for configs saved before
    multi-channel support existed.
    """
    if config.get("use_test_channel"):
        test_channel = config.get("test_channel_id")
        return [test_channel] if test_channel else []

    channels = config.get("channels")
    if channels:
        return [c["id"] for c in channels if c.get("id")]

    legacy_channel = config.get("slack_channel_id")
    return [legacy_channel] if legacy_channel else []


def get_channel_tag_block(config: dict, channel_id: str) -> dict | None:
    """Build a leading mention block for one channel's configured tag list.

    Bot notification routing is scoped per channel — a client's channels can
    belong to different pods, and posting to a channel alone doesn't
    guarantee anyone actually sees it (channel notification settings vary
    per person). ``tag_user_ids`` on a channel entry names the Slack user
    IDs who should be @-mentioned, which pings them regardless of their own
    channel notification settings. Only ``channel_id``'s own tag list is
    used — other channels in the same broadcast are unaffected. Returns
    None when the channel isn't found or has no tags configured (including
    the legacy single-channel shape, which predates per-channel tagging).
    """
    for channel in config.get("channels") or []:
        if channel.get("id") != channel_id:
            continue
        user_ids = channel.get("tag_user_ids") or []
        if not user_ids:
            return None
        mentions = " ".join(f"<@{uid}>" for uid in user_ids)
        return {"type": "section", "text": {"type": "mrkdwn", "text": mentions}}
    return None


# ---------------------------------------------------------------------------
# Currency formatting
# ---------------------------------------------------------------------------

_CURRENCY_SYMBOLS: dict[str, str] = {
    "USD": "$",
    "CAD": "CA$",
    "MXN": "MX$",
    "GBP": "£",
    "EUR": "€",
    "AUD": "A$",
    "SGD": "S$",
    "SEK": "kr",
    "PLN": "zł",
    "TRY": "₺",
}

MARKETPLACE_CURRENCIES: dict[str, str] = {
    "US": "USD",
    "CA": "CAD",
    "MX": "MXN",
    "UK": "GBP",
    "DE": "EUR",
    "FR": "EUR",
    "IT": "EUR",
    "ES": "EUR",
    "NL": "EUR",
    "SE": "SEK",
    "PL": "PLN",
    "TR": "TRY",
    "AU": "AUD",
    "SG": "SGD",
}


def format_currency(amount: float | Decimal, currency_code: str) -> str:
    """Format a monetary amount with locale-aware currency symbol.

    Examples: $1,720.88  CA$319.07  £6.03  €2.16
    """
    symbol = _CURRENCY_SYMBOLS.get(currency_code, currency_code + " ")
    d = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    negative = d < 0
    d = abs(d)

    int_part = int(d)
    frac_part = str(d - int_part)[2:]
    frac_part = frac_part.ljust(2, "0")[:2]

    int_str = f"{int_part:,}"
    formatted = f"{symbol}{int_str}.{frac_part}"
    if negative:
        formatted = f"-{formatted}"
    return formatted


def format_percentage(value: float | Decimal) -> str:
    """Format a decimal ratio as a percentage string. Example: 27.56%"""
    d = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{d}%"


def format_delta(current: float, previous: float) -> str:
    """Format a change indicator. Examples: [+14%]  [-39%]  [new]

    For percentage-point differences (ACoS, TACoS), use format_delta_bps instead.
    """
    if previous == 0:
        if current == 0:
            return "[—]"
        return "[new]"
    pct_change = ((current - previous) / abs(previous)) * 100
    sign = "+" if pct_change >= 0 else ""
    return f"[{sign}{pct_change:.0f}%]"


def format_delta_bps(current_pct: float, previous_pct: float) -> str:
    """Format a percentage-point change. Example: [+37 bps]  [-12 bps]"""
    diff_bps = (current_pct - previous_pct) * 100
    sign = "+" if diff_bps >= 0 else ""
    return f"[{sign}{diff_bps:.0f} bps]"


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

def _get_sm() -> secretmanager.SecretManagerServiceClient:
    global _sm
    if _sm is None:
        _sm = secretmanager.SecretManagerServiceClient()
    return _sm


def _get_slack_token() -> str:
    """Lazily load the Slack bot token from Secret Manager (or
    scripts/local-secrets.json when LOCAL_MODE=true)."""
    global _slack_token
    if _slack_token is not None:
        return _slack_token

    env = get_environment()
    short_name = f"kalilos-{env}-slack-bot-token"

    if is_local_mode():
        _slack_token = resolve_secret(short_name).strip()
        return _slack_token

    project = get_project()
    full_name = f"projects/{project}/secrets/{short_name}/versions/latest"
    resp = _get_sm().access_secret_version(name=full_name)
    _slack_token = resp.payload.data.decode("utf-8").strip()
    return _slack_token


# ---------------------------------------------------------------------------
# Slack API
# ---------------------------------------------------------------------------

def post_message(
    channel_id: str,
    blocks: list[dict],
    text_fallback: str = "",
    thread_ts: str | None = None,
) -> dict:
    """Post a Block Kit message to a Slack channel.

    Returns the Slack API response dict (includes 'ts' for threading).
    Raises on HTTP or Slack API errors.
    """
    token = _get_slack_token()
    payload: dict = {
        "channel": channel_id,
        "blocks": blocks,
        "text": text_fallback or "Kalilos Report Update",
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts

    resp = requests.post(
        f"{SLACK_API_BASE}/chat.postMessage",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload),
        timeout=10,
    )
    resp.raise_for_status()

    data = resp.json()
    if not data.get("ok"):
        error = data.get("error", "unknown_error")
        # Channel-config errors are logged by the caller as actionable WARNINGs
        # (with the client/channel context it has); avoid double-logging them as
        # ERRORs here. Genuine API errors still get an ERROR line.
        if error not in CHANNEL_CONFIG_ERRORS:
            logger.error("Slack API error: %s", error, extra={"channel": channel_id})
        raise SlackApiError(error, channel_id=channel_id)

    logger.info(
        "Slack message posted",
        extra={"channel": channel_id, "ts": data.get("ts")},
    )
    return data
