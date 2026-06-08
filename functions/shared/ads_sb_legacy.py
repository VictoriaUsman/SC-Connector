"""Sponsored Brands legacy-campaign data-completeness fallback.

Amazon Ads API **v3 reporting** (``POST /reporting/reports``) only returns
Sponsored Brands **v4** (multi-ad-group) campaigns.  Legacy SB campaigns
(``isMultiAdGroupsEnabled = false``) are silently omitted, so their cost/sales
never reach the export and the total under-counts the Ads console.

This module re-includes those legacy campaigns by:

1. Listing campaigns via ``POST /sb/v4/campaigns/list`` and selecting the ones
   with ``isMultiAdGroupsEnabled`` falsy (the legacy set).
2. Pulling their performance from the deprecated **v2** reporting endpoints
   (``POST /v2/hsa/campaigns/report`` with ``creativeType: "all"``), one report
   per day in the requested range (v2 reports are single-day).
3. Mapping the v2 metric names / date format onto the v3 ``sbCampaigns`` column
   schema and merging the rows into the v3 report.

The v3 and v2 row sets are **disjoint by definition** (v3 returns only
multi-ad-group campaigns; we keep only non-multi-ad-group campaigns from v2), so
concatenating them never double-counts.

``augment_sb_campaigns_content`` is the single integration point used by the
``download_upload`` function. It is best-effort: any failure pulling the legacy
data is logged and the original (v3-only) content is returned unchanged, so a
v2 outage never breaks the existing pipeline.
"""

from __future__ import annotations

import gzip
import json
import logging
import random
import time
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import requests
from ad_api.api.sb import CampaignsV4
from ad_api.api.sb import Reports as SbV2Reports
from ad_api.base.exceptions import AdvertisingApiException

from shared.ads_api_client import ADS_API_STATUS_MAP, marketplace_enum

logger = logging.getLogger(__name__)

# The legacy v2 reporting calls share one host (advertising-api*.amazon.com) with
# the rest of the pipeline. During the scheduler's concurrent fan-out, many
# download_upload functions open fresh (keep-alive-less) TLS connections to that
# host at once, and Amazon intermittently resets some of them
# (``ConnectionResetError: [Errno 104] Connection reset by peer``). Without
# retries a single reset aborts the whole augmentation and the legacy SB
# campaigns silently drop out of the export, so the total under-counts the Ads
# console. Retrying transient network / 429 / 5xx failures with backoff lets the
# fetch complete so the merged total matches the console.
_RETRYABLE_API_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# ``requests.exceptions.ConnectionError`` wraps the urllib3 connection-reset; the
# builtin ``ConnectionError`` (parent of ``ConnectionResetError``) covers the
# rare un-wrapped case. ``Timeout`` / ``ChunkedEncodingError`` are likewise
# transient.
_RETRYABLE_NETWORK_ERRORS: tuple[type[Exception], ...] = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
    ConnectionError,
)

_MAX_RETRY_ATTEMPTS = 5
_RETRY_BASE_SECONDS = 2.0
_RETRY_MAX_SECONDS = 30.0

# Comma-separated metrics requested from the v2 hsa campaigns report. We rely on
# the v4 campaign list for dimensions (name/status/budget) and only need the
# performance metrics here, but campaignId is always required to join.
_V2_CAMPAIGN_METRICS = [
    "campaignId",
    "campaignName",
    "campaignStatus",
    "campaignBudget",
    "impressions",
    "clicks",
    "cost",
    "attributedSales14d",
    "attributedConversions14d",
    "attributedUnitsOrdered14d",
    "attributedDetailPageViewsClicks14d",
    "attributedOrdersNewToBrand14d",
    "attributedSalesNewToBrand14d",
]

# Maps v2 hsa report metric names onto the v3 ``sbCampaigns`` column names.
# v3 columns (see ads_report_config.sbCampaigns):
#   cost, purchases, sales, unitsSoldClicks, detailPageViewsClicks,
#   newToBrandPurchases, newToBrandSales
_V2_TO_V3_METRIC: dict[str, str] = {
    "cost": "cost",
    "impressions": "impressions",
    "clicks": "clicks",
    "attributedSales14d": "sales",
    "attributedConversions14d": "purchases",
    "attributedUnitsOrdered14d": "unitsSoldClicks",
    "attributedDetailPageViewsClicks14d": "detailPageViewsClicks",
    "attributedOrdersNewToBrand14d": "newToBrandPurchases",
    "attributedSalesNewToBrand14d": "newToBrandSales",
}

# Default poll budget for a single v2 report (they typically generate quickly).
_MAX_POLLS = 30
_POLL_INTERVAL_SECONDS = 4


# ----------------------------------------------------------------------------
# Pure helpers (no network) — these carry the merge/aggregation logic the
# acceptance criteria require tests for.
# ----------------------------------------------------------------------------

def date_range(start: date, end: date) -> list[date]:
    """Inclusive list of dates from *start* to *end* (ascending)."""
    if end < start:
        return []
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def to_yyyymmdd(d: date) -> str:
    """Format a date as the v2 reporting ``YYYYMMDD`` string."""
    return d.strftime("%Y%m%d")


def select_legacy_campaign_ids(campaigns: list[dict]) -> dict[str, dict]:
    """From a v4 campaign list, return ``{campaignId: metadata}`` for the
    **legacy** (non-multi-ad-group) campaigns only.

    Metadata carries the dimensions we surface in the export so we do not have
    to trust the v2 report to echo them back: ``campaignName``,
    ``campaignStatus``, ``campaignBudgetAmount``.
    """
    legacy: dict[str, dict] = {}
    for c in campaigns:
        if not isinstance(c, dict):
            continue
        if c.get("isMultiAdGroupsEnabled"):
            continue
        campaign_id = c.get("campaignId")
        if campaign_id is None:
            continue
        cid = str(campaign_id)
        legacy[cid] = {
            "campaignId": cid,
            "campaignName": c.get("name", ""),
            "campaignStatus": _normalize_status(c.get("state", "")),
            "campaignBudgetAmount": _extract_budget(c),
        }
    return legacy


def map_v2_campaign_row(
    v2_row: dict,
    *,
    report_date: date,
    metadata: dict | None = None,
) -> dict:
    """Map one v2 hsa campaign report row onto the v3 ``sbCampaigns`` schema.

    *report_date* is injected as the ``date`` column (``YYYY-MM-DD``) because v2
    reports are single-day and do not carry a date field. *metadata* (from the
    v4 campaign list) supplies dimensions; the v2 row supplies metrics.
    """
    out: dict[str, Any] = {"date": report_date.isoformat()}

    meta = metadata or {}
    out["campaignId"] = str(v2_row.get("campaignId", meta.get("campaignId", "")))
    out["campaignName"] = meta.get("campaignName") or v2_row.get("campaignName", "")
    out["campaignStatus"] = meta.get("campaignStatus") or _normalize_status(
        v2_row.get("campaignStatus", "")
    )
    budget = meta.get("campaignBudgetAmount")
    if budget in (None, ""):
        budget = v2_row.get("campaignBudget", "")
    out["campaignBudgetAmount"] = budget

    for v2_key, v3_key in _V2_TO_V3_METRIC.items():
        if v2_key in ("impressions", "clicks", "cost"):
            continue  # handled below to guarantee presence
        if v2_key in v2_row:
            out[v3_key] = v2_row[v2_key]

    # Core metrics: always present (default 0) so totals are well-defined.
    out["impressions"] = v2_row.get("impressions", 0)
    out["clicks"] = v2_row.get("clicks", 0)
    out["cost"] = v2_row.get("cost", 0)
    return out


def merge_sb_rows(v3_rows: list[dict], legacy_rows: list[dict]) -> list[dict]:
    """Concatenate v3 and legacy rows.

    The two sets are disjoint by construction (v3 returns only multi-ad-group
    campaigns; *legacy_rows* are non-multi-ad-group only), so a plain
    concatenation is correct and never double-counts. As a defensive guard we
    drop any legacy row whose ``campaignId`` already appears in the v3 rows.
    """
    v3_campaign_ids = {
        str(r.get("campaignId")) for r in v3_rows if r.get("campaignId") is not None
    }
    merged = list(v3_rows)
    for row in legacy_rows:
        if str(row.get("campaignId")) in v3_campaign_ids:
            logger.warning(
                "Skipping legacy SB row already present in v3 report",
                extra={"campaign_id": row.get("campaignId")},
            )
            continue
        merged.append(row)
    return merged


def _normalize_status(state: str) -> str:
    """v4 uses lowercase states (enabled/paused/archived); v3 reports use
    uppercase campaignStatus (ENABLED/PAUSED/ARCHIVED)."""
    return str(state).upper() if state else ""


def _extract_budget(campaign: dict) -> Any:
    """Pull the budget amount from a v4 campaign object's nested budget."""
    budget = campaign.get("budget")
    if isinstance(budget, dict):
        return budget.get("budget", budget.get("amount", ""))
    if budget is not None:
        return budget
    return ""


# ----------------------------------------------------------------------------
# Network orchestration
# ----------------------------------------------------------------------------

def _is_retryable(exc: Exception) -> bool:
    """True for transient Ads API failures worth retrying (network resets,
    timeouts, 429, and 5xx)."""
    if isinstance(exc, _RETRYABLE_NETWORK_ERRORS):
        return True
    if isinstance(exc, AdvertisingApiException):
        return getattr(exc, "code", None) in _RETRYABLE_API_CODES
    return False


def _call_with_retry(
    fn: Callable[[], Any],
    *,
    description: str,
    sleep_fn: Callable[[float], None],
    max_attempts: int = _MAX_RETRY_ATTEMPTS,
) -> Any:
    """Call *fn*, retrying transient failures with exponential backoff + jitter.

    Non-retryable errors (auth, 4xx other than 429, programming errors) are
    re-raised immediately. The jitter spreads concurrent retries out so the
    scheduler's fan-out does not re-collide on the same backoff schedule.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — classified by _is_retryable
            if not _is_retryable(exc) or attempt == max_attempts:
                raise
            last_exc = exc
            backoff = min(_RETRY_BASE_SECONDS * (2 ** (attempt - 1)), _RETRY_MAX_SECONDS)
            backoff += random.uniform(0, backoff / 2)
            logger.warning(
                "Transient Ads v2 error; retrying",
                extra={
                    "operation": description,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "error": str(exc)[:200],
                },
            )
            sleep_fn(backoff)
    # Loop always returns or raises; this satisfies type checkers.
    raise last_exc  # type: ignore[misc]


def collect_legacy_sb_campaigns(
    credentials: dict,
    marketplace: str,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, dict]:
    """List SB campaigns for the profile and return legacy ones keyed by id.

    Paginates ``POST /sb/v4/campaigns/list`` over enabled + paused campaigns
    (matching the Ads console "All but archived" view) and returns
    ``{campaignId: metadata}`` for campaigns with ``isMultiAdGroupsEnabled``
    falsy.
    """
    client = CampaignsV4(credentials=credentials, marketplace=marketplace_enum(marketplace))
    legacy: dict[str, dict] = {}
    next_token: str | None = None

    while True:
        body: dict[str, Any] = {
            "maxResults": 100,
            "stateFilter": {"include": ["ENABLED", "PAUSED"]},
        }
        if next_token:
            body["nextToken"] = next_token

        resp = _call_with_retry(
            lambda body=body: client.list_campaigns(body=body),
            description="sb_v4_list_campaigns",
            sleep_fn=sleep_fn,
        )
        payload = resp.payload if hasattr(resp, "payload") else resp
        if isinstance(payload, (bytes, str)):
            payload = json.loads(payload)
        campaigns = (payload or {}).get("campaigns", []) if isinstance(payload, dict) else []
        legacy.update(select_legacy_campaign_ids(campaigns))

        next_token = (payload or {}).get("nextToken") if isinstance(payload, dict) else None
        if not next_token:
            break

    logger.info(
        "Legacy SB campaign scan complete",
        extra={"marketplace": marketplace, "legacy_campaign_count": len(legacy)},
    )
    return legacy


def fetch_legacy_v2_rows(
    credentials: dict,
    marketplace: str,
    start_date: date,
    end_date: date,
    legacy: dict[str, dict],
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_polls: int = _MAX_POLLS,
) -> list[dict]:
    """Pull v2 hsa campaign reports for each day in range and return v3-shaped
    rows for the *legacy* campaigns only."""
    if not legacy:
        return []

    client = SbV2Reports(credentials=credentials, marketplace=marketplace_enum(marketplace))
    rows: list[dict] = []

    for day in date_range(start_date, end_date):
        v2_records = _request_and_download_v2_report(
            client, day, sleep_fn=sleep_fn, max_polls=max_polls
        )
        for rec in v2_records:
            cid = str(rec.get("campaignId", ""))
            if cid not in legacy:
                continue  # not legacy → already covered by v3, skip
            rows.append(map_v2_campaign_row(rec, report_date=day, metadata=legacy.get(cid)))

    logger.info(
        "Legacy SB v2 rows fetched",
        extra={
            "marketplace": marketplace,
            "row_count": len(rows),
            "days": (end_date - start_date).days + 1,
        },
    )
    return rows


def augment_sb_campaigns_content(
    content: bytes,
    *,
    credentials: dict,
    marketplace: str,
    start_date: date,
    end_date: date,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> bytes:
    """Merge legacy SB campaign rows into a v3 ``sbCampaigns`` report.

    *content* is the decompressed v3 report (a JSON array of rows). Returns the
    re-serialized JSON array with legacy rows appended, or the original
    *content* unchanged if there are no legacy campaigns or if the legacy pull
    fails (best-effort — never break the existing v3 export).
    """
    try:
        v3_data = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("sbCampaigns content is not JSON; skipping legacy augmentation")
        return content

    if not isinstance(v3_data, list):
        logger.warning("sbCampaigns content is not a JSON array; skipping legacy augmentation")
        return content

    try:
        legacy = collect_legacy_sb_campaigns(credentials, marketplace, sleep_fn=sleep_fn)
        if not legacy:
            logger.info("No legacy SB campaigns found; v3 report is complete")
            return content

        legacy_rows = fetch_legacy_v2_rows(
            credentials, marketplace, start_date, end_date, legacy, sleep_fn=sleep_fn
        )
        if not legacy_rows:
            logger.info("No legacy SB performance rows returned for the date range")
            return content

        merged = merge_sb_rows(v3_data, legacy_rows)
        logger.info(
            "Merged legacy SB campaigns into v3 report",
            extra={
                "v3_rows": len(v3_data),
                "legacy_rows": len(legacy_rows),
                "merged_rows": len(merged),
            },
        )
        return json.dumps(merged).encode("utf-8")
    except Exception:  # noqa: BLE001 — best-effort, must not break the v3 export
        logger.exception(
            "Legacy SB augmentation failed; uploading v3-only report",
            extra={"marketplace": marketplace},
        )
        return content


def _request_and_download_v2_report(
    client: SbV2Reports,
    report_date: date,
    *,
    sleep_fn: Callable[[float], None],
    max_polls: int,
) -> list[dict]:
    """Create, poll, and download a single-day v2 hsa campaigns report."""
    body = {
        "reportDate": to_yyyymmdd(report_date),
        "creativeType": "all",
        "metrics": ",".join(_V2_CAMPAIGN_METRICS),
    }
    create_resp = _call_with_retry(
        lambda: client.post_report(recordType="campaigns", body=body),
        description="sb_v2_post_report",
        sleep_fn=sleep_fn,
    )
    create_payload = _payload(create_resp)
    report_id = create_payload.get("reportId")
    if not report_id:
        logger.warning("v2 SB report creation returned no reportId", extra={"date": report_date.isoformat()})
        return []

    download_url: str | None = None
    for _ in range(max_polls):
        status_payload = _payload(
            _call_with_retry(
                lambda: client.get_report(reportId=report_id),
                description="sb_v2_get_report",
                sleep_fn=sleep_fn,
            )
        )
        raw_status = status_payload.get("status", "")
        normalized = ADS_API_STATUS_MAP.get(raw_status, "unknown")
        if normalized == "ready":
            download_url = status_payload.get("location") or status_payload.get("url")
            break
        if normalized == "failed":
            logger.warning(
                "v2 SB report failed",
                extra={"date": report_date.isoformat(), "raw_status": raw_status},
            )
            return []
        sleep_fn(_POLL_INTERVAL_SECONDS)

    if not download_url:
        logger.warning("v2 SB report did not complete in time", extra={"date": report_date.isoformat()})
        return []

    raw = _download_v2(client, download_url, sleep_fn=sleep_fn)
    return _parse_v2_records(raw)


def _payload(resp: Any) -> dict:
    payload = resp.payload if hasattr(resp, "payload") else resp
    if isinstance(payload, (bytes, str)):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
    return payload if isinstance(payload, dict) else {}


def _download_v2(
    client: SbV2Reports,
    url: str,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> bytes:
    def _do() -> Any:
        resp = client.download_report(url=url, format="raw")
        payload = resp.payload if hasattr(resp, "payload") else resp
        # The SDK's _download swallows network errors into an error ApiResponse
        # ({"success": False, "code": 503, ...}) instead of raising. Re-raise the
        # transient ones so _call_with_retry can retry the download.
        if isinstance(payload, dict) and payload.get("success") is False:
            code = payload.get("code")
            if code in _RETRYABLE_API_CODES:
                raise requests.exceptions.ConnectionError(
                    f"v2 SB report download failed (transient, code={code})"
                )
            raise RuntimeError(f"v2 SB report download failed (code={code})")
        return payload

    payload = _call_with_retry(_do, description="sb_v2_download", sleep_fn=sleep_fn)
    if isinstance(payload, bytes):
        if payload[:2] == b"\x1f\x8b":
            return gzip.decompress(payload)
        return payload
    if isinstance(payload, str):
        return payload.encode("utf-8")
    # SDK may already decode JSON into a list/dict.
    return json.dumps(payload).encode("utf-8")


def _parse_v2_records(raw: bytes) -> list[dict]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Failed to parse v2 SB report JSON")
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    return []
