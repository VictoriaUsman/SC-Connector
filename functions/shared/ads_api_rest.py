"""Generic signed-request transport for the Amazon Ads API (non-report endpoints).

The SDK wrapper in ``ads_api_client`` only covers the asynchronous Reports v3
API. This module is the reusable baseline for calling any other Ads API
endpoint synchronously: it resolves the regional host, attaches the Bearer
access token plus the required ``Amazon-Advertising-API-ClientId`` /
``Amazon-Advertising-API-Scope`` (profile) headers, and retries transient
failures (429/5xx).
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

from shared.config import get_ads_api_endpoint
from shared.credentials import get_ads_credentials
from shared.lwa_token import get_access_token

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 4
_BASE_BACKOFF_SECONDS = 1.0
_REQUEST_TIMEOUT_SECONDS = 30


class AdsAPIRequestError(Exception):
    """Raised when an Ads API REST request fails with a non-2xx status."""

    def __init__(self, status_code: int, message: str, body: Any = None) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"Ads-API {status_code}: {message}")


def ads_api_request(
    client_id: str,
    marketplace: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    content_type: str = "application/json",
    accept: str | None = None,
    timeout: int = _REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Call an Ads API REST endpoint and return the parsed JSON response.

    ``path`` is the resource path beginning with ``/`` (e.g. ``/v2/profiles``);
    the regional host is resolved from *marketplace*. Many Ads endpoints require
    a versioned vendor ``content_type``/``accept`` media type — pass those in.
    Transient errors (429, 5xx, network) are retried with exponential backoff.
    """
    creds = get_ads_credentials(client_id, marketplace)
    url = get_ads_api_endpoint(marketplace) + path
    method = method.upper()

    last_err: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        token = get_access_token(
            refresh_token=creds["refresh_token"],
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Amazon-Advertising-API-ClientId": creds["client_id"],
            "Content-Type": content_type,
            "Accept": accept or content_type,
        }
        if creds.get("profile_id"):
            headers["Amazon-Advertising-API-Scope"] = str(creds["profile_id"])

        try:
            resp = requests.request(
                method, url, headers=headers, params=params, json=body, timeout=timeout
            )
        except requests.RequestException as exc:
            last_err = exc
            logger.warning(
                "Ads-API request network error (attempt %d): %s", attempt + 1, exc
            )
        else:
            if 200 <= resp.status_code < 300:
                if not resp.content:
                    return {}
                try:
                    return resp.json()
                except ValueError:
                    return {"raw": resp.text}

            if resp.status_code != 429 and resp.status_code < 500:
                raise AdsAPIRequestError(
                    resp.status_code, resp.text[:500], body=_safe_json(resp)
                )
            last_err = AdsAPIRequestError(
                resp.status_code, resp.text[:500], body=_safe_json(resp)
            )

        if attempt < _MAX_ATTEMPTS - 1:
            backoff = _BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 0.5)
            logger.warning(
                "Ads-API request retrying in %.2fs (attempt %d/%d)",
                backoff,
                attempt + 1,
                _MAX_ATTEMPTS,
            )
            time.sleep(backoff)

    raise last_err  # type: ignore[misc]


def _safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None
