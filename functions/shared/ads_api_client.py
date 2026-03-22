"""Amazon Ads API v3 client for reports — wraps the python-amazon-ad-api SDK.

The SDK handles LWA token exchange, endpoint routing, and required headers
(Authorization, ClientId, Scope) automatically. We add status normalization
and a raw-bytes download helper for Drive upload.
"""

from __future__ import annotations

import gzip
import logging
import re

from ad_api.api import Reports
from ad_api.base import Marketplaces
from ad_api.base.exceptions import AdvertisingApiException

logger = logging.getLogger(__name__)

ADS_API_STATUS_MAP = {
    "PENDING": "pending",
    "PROCESSING": "pending",
    "IN_PROGRESS": "pending",
    "COMPLETED": "ready",
    "SUCCESS": "ready",
    "FAILURE": "failed",
    "FAILED": "failed",
}

_MARKETPLACE_ENUM: dict[str, Marketplaces] = {
    "US": Marketplaces.US, "CA": Marketplaces.CA, "MX": Marketplaces.MX, "BR": Marketplaces.BR,
    "UK": Marketplaces.UK, "DE": Marketplaces.DE, "FR": Marketplaces.FR, "IT": Marketplaces.IT,
    "ES": Marketplaces.ES,
    "JP": Marketplaces.JP, "AU": Marketplaces.AU, "IN": Marketplaces.IN, "SG": Marketplaces.SG,
}


def _client(credentials: dict, marketplace: str) -> Reports:
    return Reports(credentials=credentials, marketplace=_MARKETPLACE_ENUM[marketplace])


_DUPLICATE_RE = re.compile(r"duplicate of\s*:\s*([0-9a-f-]{36})", re.IGNORECASE)


def create_report(
    credentials: dict,
    marketplace: str,
    report_config: dict,
) -> str:
    """Create an async v3 report. Returns the Ads API reportId.

    Handles HTTP 425 (duplicate request) by extracting the existing report ID
    from Amazon's error response and returning it for polling.
    """
    try:
        resp = _client(credentials, marketplace).post_report(body=report_config)
        report_id = resp.payload["reportId"]
        logger.info("Ads API report created", extra={"report_id": report_id})
        return report_id
    except AdvertisingApiException as exc:
        if exc.code == 425:
            detail = (exc.error or {}).get("detail", "")
            match = _DUPLICATE_RE.search(detail)
            if match:
                existing_id = match.group(1)
                logger.info(
                    "Ads API 425 duplicate — reusing existing report",
                    extra={"existing_report_id": existing_id},
                )
                return existing_id
            logger.warning(
                "Ads API 425 but could not parse existing report ID",
                extra={"detail": detail},
            )
        raise


def get_report(credentials: dict, marketplace: str, report_id: str) -> dict:
    """Poll report status. Returns normalized status + download_url when ready."""
    resp = _client(credentials, marketplace).get_report(reportId=report_id)
    raw_status = resp.payload["status"]
    return {
        "raw_status": raw_status,
        "status": ADS_API_STATUS_MAP.get(raw_status, "unknown"),
        "download_url": resp.payload.get("url"),
    }


def download_report(credentials: dict, marketplace: str, download_url: str) -> bytes:
    """Download and decompress a completed Ads report (always GZIP JSON)."""
    resp = _client(credentials, marketplace).download_report(url=download_url, format="raw")
    payload = resp.payload if hasattr(resp, "payload") else resp
    if isinstance(payload, bytes):
        if payload[:2] == b"\x1f\x8b":
            return gzip.decompress(payload)
        return payload
    return gzip.decompress(payload.encode("utf-8"))
