"""Ads API error classification for unauthorized 3P profile access failures.

Amazon Ads API raises an "Unauthorized exception while handling 3P Request"
when the Kalilos Ads application (customer id AVCDL6XIHIB1E) no longer has access
to an advertiser profile. This typically happens when the advertiser revokes the
third-party app authorization in their Amazon Ads console
("Manage Your Account" -> "Third-Party Applications"), or the OAuth grant
expired. Retrying does not help — the advertiser must re-grant access.

We detect this specific error, attach diagnostic context (client, profile id,
ad type), and surface it as a non-retryable failure so the job fails fast with a
clear, actionable message instead of a generic workflow_error.
"""

from __future__ import annotations

import re

from ad_api.base.exceptions import AdvertisingApiException

from shared.ads_report_config import ADS_REPORT_TYPES

# adProduct -> short ad-type label, used for fast log triage (SD / SB / SP).
_AD_PRODUCT_LABELS: dict[str, str] = {
    "SPONSORED_PRODUCTS": "SP (Sponsored Products)",
    "SPONSORED_BRANDS": "SB (Sponsored Brands)",
    "SPONSORED_DISPLAY": "SD (Sponsored Display)",
}

_PROFILE_ID_RE = re.compile(
    r"does not have access to profile:\s*([0-9]+)", re.IGNORECASE
)


def ad_type_for_report(report_type: str | None) -> str | None:
    """Resolve a human-readable ad type (SP / SB / SD) from an Ads report type."""
    if not report_type:
        return None
    config = ADS_REPORT_TYPES.get(report_type) or {}
    ad_product = config.get("adProduct")
    if not ad_product:
        return None
    return _AD_PRODUCT_LABELS.get(ad_product, ad_product)


def _exc_text(exc: Exception) -> str:
    """Collect all human-readable text from an exception for pattern matching.

    AdvertisingApiException does not call ``super().__init__``, so ``str(exc)`` is
    empty — the useful text lives on ``.message`` and inside the ``.error`` dict.
    """
    parts: list[str] = [str(exc)]
    message = getattr(exc, "message", None)
    if message:
        parts.append(str(message))
    error = getattr(exc, "error", None)
    if isinstance(error, dict):
        parts.append(str(error.get("details", "")))
        parts.append(str(error.get("detail", "")))
        parts.append(str(error.get("message", "")))
    elif error:
        parts.append(str(error))
    return " ".join(p for p in parts if p)


def is_ads_profile_unauthorized(exc: Exception) -> bool:
    """Return True if the exception indicates an unauthorized 3P profile request."""
    text = _exc_text(exc).lower()
    if "does not have access to profile" in text:
        return True
    if "unauthorized exception while handling 3p request" in text:
        return True

    code = getattr(exc, "code", None)
    amzn_code = str(getattr(exc, "amzn_code", "") or "").lower()
    if isinstance(exc, AdvertisingApiException) and code in (401, 403):
        if "unauthorized" in text or "unauthorized" in amzn_code:
            return True
    return False


def extract_profile_id(exc: Exception) -> str | None:
    """Best-effort extraction of the profile id from the error message."""
    match = _PROFILE_ID_RE.search(_exc_text(exc))
    if match:
        return match.group(1)
    return None


def unauthorized_message(
    *,
    client_id: str,
    profile_id: str | None = None,
    ad_type: str | None = None,
) -> str:
    """Build an actionable unauthorized-access message."""
    profile_hint = f" (profile {profile_id})" if profile_id else ""
    ad_hint = f" for {ad_type}" if ad_type else ""
    return (
        f"Amazon Ads API access is unauthorized for client '{client_id}'{profile_hint}"
        f"{ad_hint}. The Kalilos Ads application no longer has access to this "
        "advertiser profile — the advertiser likely revoked the third-party app "
        "authorization in their Amazon Ads console (Manage Your Account -> "
        "Third-Party Applications) or the OAuth grant expired. Re-authorization "
        "requires the advertiser to re-grant access; retrying will not help. "
        "Coordinate with the account manager to have the client restore Kalilos "
        "app access."
    )


class AdsProfileUnauthorizedError(Exception):
    """Raised when the Ads app lacks access to a 3P advertiser profile.

    This is a terminal, non-retryable failure: retrying will not restore access.
    """

    def __init__(
        self,
        message: str,
        *,
        client_id: str,
        marketplace: str | None = None,
        report_type: str | None = None,
        profile_id: str | None = None,
        ad_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.client_id = client_id
        self.marketplace = marketplace
        self.report_type = report_type
        self.profile_id = profile_id
        self.ad_type = ad_type

    def log_context(self) -> dict[str, str]:
        ctx: dict[str, str] = {"client_id": self.client_id}
        if self.profile_id:
            ctx["profile_id"] = self.profile_id
        if self.ad_type:
            ctx["ad_type"] = self.ad_type
        if self.marketplace:
            ctx["marketplace"] = self.marketplace
        if self.report_type:
            ctx["report_type"] = self.report_type
        return ctx


def raise_if_ads_profile_unauthorized(
    exc: Exception,
    *,
    client_id: str,
    marketplace: str | None = None,
    report_type: str | None = None,
    profile_id: str | None = None,
) -> None:
    """Re-raise unauthorized 3P profile failures as AdsProfileUnauthorizedError.

    If ``exc`` is not an unauthorized-profile error, the original exception is
    re-raised unchanged so the caller's normal error handling applies.
    """
    if not is_ads_profile_unauthorized(exc):
        raise exc

    resolved_profile = profile_id or extract_profile_id(exc)
    ad_type = ad_type_for_report(report_type)
    raise AdsProfileUnauthorizedError(
        unauthorized_message(
            client_id=client_id,
            profile_id=resolved_profile,
            ad_type=ad_type,
        ),
        client_id=client_id,
        marketplace=marketplace,
        report_type=report_type,
        profile_id=resolved_profile,
        ad_type=ad_type,
    ) from exc
