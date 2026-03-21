"""Amazon SP API client for reports — wraps the python-amazon-sp-api SDK.

The SDK handles LWA token exchange, endpoint routing, request signing,
and rate-limit headers automatically. We add status normalization and
a raw-bytes download helper for Drive upload.
"""

from __future__ import annotations

import gzip
import logging

import requests
from sp_api.api import Reports
from sp_api.base import Marketplaces

from shared.config import get_marketplace_id

logger = logging.getLogger(__name__)

SP_API_STATUS_MAP = {
    "IN_QUEUE": "pending",
    "IN_PROGRESS": "pending",
    "DONE": "ready",
    "CANCELLED": "failed",
    "FATAL": "failed",
}

_MARKETPLACE_ENUM: dict[str, Marketplaces] = {
    "US": Marketplaces.US, "CA": Marketplaces.CA, "MX": Marketplaces.MX, "BR": Marketplaces.BR,
    "UK": Marketplaces.UK, "DE": Marketplaces.DE, "FR": Marketplaces.FR, "IT": Marketplaces.IT,
    "ES": Marketplaces.ES, "NL": Marketplaces.NL,
    "JP": Marketplaces.JP, "AU": Marketplaces.AU, "IN": Marketplaces.IN, "SG": Marketplaces.SG,
}


def _client(credentials: dict, marketplace: str) -> Reports:
    return Reports(credentials=credentials, marketplace=_MARKETPLACE_ENUM[marketplace])


def create_report(
    credentials: dict,
    marketplace: str,
    report_type: str,
    report_params: dict | None = None,
) -> str:
    """Request a new report. Returns the SP API reportId."""
    kwargs: dict = {
        "reportType": report_type,
        "marketplaceIds": [get_marketplace_id(marketplace)],
    }
    if report_params:
        for key in ("dataStartTime", "dataEndTime", "reportOptions"):
            if key in report_params:
                kwargs[key] = report_params[key]

    resp = _client(credentials, marketplace).create_report(**kwargs)
    report_id = resp.payload["reportId"]
    logger.info("SP API report created", extra={"report_id": report_id, "report_type": report_type})
    return report_id


def get_report(credentials: dict, marketplace: str, report_id: str) -> dict:
    """Poll report status. Returns normalized status + reportDocumentId when ready."""
    resp = _client(credentials, marketplace).get_report(report_id)
    raw_status = resp.payload["processingStatus"]
    return {
        "raw_status": raw_status,
        "status": SP_API_STATUS_MAP.get(raw_status, "unknown"),
        "report_document_id": resp.payload.get("reportDocumentId"),
    }


def get_report_document(credentials: dict, marketplace: str, document_id: str) -> dict:
    """Get the pre-signed download URL for a completed report."""
    resp = _client(credentials, marketplace).get_report_document(document_id)
    return {
        "url": resp.payload["url"],
        "compression": resp.payload.get("compressionAlgorithm"),
    }


def download_report(url: str, compression: str | None = None) -> bytes:
    """Download from pre-signed URL. Decompresses GZIP if needed."""
    resp = requests.get(url, timeout=300)
    resp.raise_for_status()
    content = resp.content
    if compression == "GZIP":
        content = gzip.decompress(content)
    return content
