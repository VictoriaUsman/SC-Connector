"""Login with Amazon (LWA) access-token exchange with in-process caching + retry.

Every workflow execution authenticates before requesting a report. Without
caching, a fan-out of many concurrent workflows hammers the LWA token endpoint,
which then throttles — surfacing as ``AUTH_FAILED: Token exchange failed`` and
contributing to the SP-API gateway returning transient 403s under load.

This module exchanges a refresh token for an access token and caches the result
in process for its validity window (LWA tokens last ~1h). A warm function
instance serving a burst reuses one token per client instead of re-exchanging on
every request. Transient failures (network errors, 429, 5xx) are retried with
exponential backoff; permanent errors (e.g. a revoked token) fail fast.
"""

from __future__ import annotations

import hashlib
import logging
import random
import threading
import time

import requests

from shared.config import LWA_TOKEN_URL

logger = logging.getLogger(__name__)

# Refresh slightly before the token actually expires to avoid races near the edge.
_EXPIRY_SAFETY_SECONDS = 300
_DEFAULT_EXPIRES_IN = 3600
_MAX_ATTEMPTS = 3
_BASE_BACKOFF_SECONDS = 0.5
_REQUEST_TIMEOUT_SECONDS = 10

_lock = threading.Lock()
# cache key -> (access_token, expires_at_epoch_seconds)
_cache: dict[str, tuple[str, float]] = {}


def _cache_key(refresh_token: str, client_id: str, client_secret: str) -> str:
    digest = hashlib.sha256(f"{client_id}|{client_secret}|{refresh_token}".encode()).hexdigest()
    return digest


def get_access_token(
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    force_refresh: bool = False,
) -> str:
    """Return a valid LWA access token, reusing a cached one when possible."""
    key = _cache_key(refresh_token, client_id, client_secret)

    if not force_refresh:
        with _lock:
            cached = _cache.get(key)
            if cached and cached[1] > time.time():
                return cached[0]

    access_token, expires_in = _exchange_token(refresh_token, client_id, client_secret)
    expires_at = time.time() + max(expires_in - _EXPIRY_SAFETY_SECONDS, 0)
    with _lock:
        _cache[key] = (access_token, expires_at)
    return access_token


def _exchange_token(refresh_token: str, client_id: str, client_secret: str) -> tuple[str, int]:
    """Call the LWA token endpoint with bounded retry on transient failures."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }

    last_error: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            resp = requests.post(LWA_TOKEN_URL, data=data, timeout=_REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            last_error = exc  # network/timeout — transient, retry
        else:
            if resp.status_code == 200:
                body = resp.json()
                return body["access_token"], int(body.get("expires_in", _DEFAULT_EXPIRES_IN))
            if resp.status_code != 429 and resp.status_code < 500:
                # Permanent client error (e.g. invalid_grant on a revoked token).
                resp.raise_for_status()
                raise requests.HTTPError(
                    f"LWA token exchange failed: {resp.status_code}", response=resp
                )
            last_error = requests.HTTPError(
                f"LWA token endpoint transient error: {resp.status_code}", response=resp
            )

        if attempt < _MAX_ATTEMPTS - 1:
            backoff = _BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 0.25)
            logger.warning(
                "LWA token exchange attempt %d failed, retrying in %.2fs",
                attempt + 1,
                backoff,
            )
            time.sleep(backoff)

    raise last_error  # type: ignore[misc]
