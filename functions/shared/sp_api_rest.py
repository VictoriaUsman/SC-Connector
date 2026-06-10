"""Generic signed-request transport for the SP-API (non-report endpoints).

The SDK wrappers in ``sp_api_client`` only cover the asynchronous Reports API.
Many SP-API resources (e.g. the Replenishment API for Subscribe & Save) are
plain synchronous JSON REST endpoints with no report job. This module is the
reusable baseline for calling any of them: it resolves the regional endpoint,
attaches a cached LWA access token, retries transient failures (429/5xx), and
offers a ``paginate`` helper that follows ``nextToken``.

Authentication is the modern SP-API model: only the LWA access token in the
``x-amz-access-token`` header is required (AWS SigV4 signing was dropped by
Amazon in 2023).
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, Iterator

import requests

from shared.config import get_sp_api_endpoint
from shared.credentials import get_sp_credentials
from shared.lwa_token import get_access_token

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 4
_BASE_BACKOFF_SECONDS = 1.0
_REQUEST_TIMEOUT_SECONDS = 30


class SPAPIRequestError(Exception):
    """Raised when an SP-API REST request fails with a non-2xx status.

    The status code is preserved on the exception and included in the message
    so throttle detection (``shared.throttle.is_throttled``) can classify 429s.
    """

    def __init__(self, status_code: int, message: str, body: Any = None) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"SP-API {status_code}: {message}")


def _access_token(client_id: str) -> str:
    creds = get_sp_credentials(client_id)
    return get_access_token(
        refresh_token=creds["refresh_token"],
        client_id=creds["lwa_app_id"],
        client_secret=creds["lwa_client_secret"],
    )


def sp_api_request(
    client_id: str,
    marketplace: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    timeout: int = _REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Call an SP-API REST endpoint and return the parsed JSON response.

    ``path`` is the resource path beginning with ``/`` (e.g.
    ``/replenishment/2022-11-07/offers``); the regional host is resolved from
    *marketplace*. Transient errors (429, 5xx, network) are retried with
    exponential backoff; other non-2xx responses raise ``SPAPIRequestError``.
    """
    url = get_sp_api_endpoint(marketplace) + path
    method = method.upper()

    last_err: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        token = _access_token(client_id)
        headers = {
            "x-amz-access-token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            resp = requests.request(
                method, url, headers=headers, params=params, json=body, timeout=timeout
            )
        except requests.RequestException as exc:
            last_err = exc
            logger.warning(
                "SP-API request network error (attempt %d): %s", attempt + 1, exc
            )
        else:
            if 200 <= resp.status_code < 300:
                if not resp.content:
                    return {}
                try:
                    return resp.json()
                except ValueError:
                    return {"raw": resp.text}

            # Retry transient throttling / server errors; fail fast on the rest.
            if resp.status_code != 429 and resp.status_code < 500:
                raise SPAPIRequestError(
                    resp.status_code, resp.text[:500], body=_safe_json(resp)
                )
            last_err = SPAPIRequestError(
                resp.status_code, resp.text[:500], body=_safe_json(resp)
            )

        if attempt < _MAX_ATTEMPTS - 1:
            backoff = _BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 0.5)
            logger.warning(
                "SP-API request retrying in %.2fs (attempt %d/%d)",
                backoff,
                attempt + 1,
                _MAX_ATTEMPTS,
            )
            time.sleep(backoff)

    raise last_err  # type: ignore[misc]


def paginate(
    client_id: str,
    marketplace: str,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
    next_token_field: str = "nextToken",
    in_body: bool = True,
    max_pages: int = 100,
) -> Iterator[dict[str, Any]]:
    """Yield successive JSON response pages, following ``nextToken``.

    The token is read from ``response["pagination"]["nextToken"]`` (falling back
    to a top-level ``nextToken``) and fed back into the next request — into the
    request body when *in_body* is True (POST search endpoints), otherwise into
    the query string. Stops at ``max_pages`` to bound runaway pagination.
    """
    next_body = dict(body or {})
    next_params = dict(params or {})

    for _ in range(max_pages):
        resp = sp_api_request(
            client_id, marketplace, method, path,
            params=next_params, body=next_body,
        )
        yield resp

        token = (resp.get("pagination") or {}).get("nextToken") or resp.get("nextToken")
        if not token:
            return
        if in_body:
            next_body[next_token_field] = token
        else:
            next_params[next_token_field] = token


def _safe_json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return None
