"""Live FX rates for converting per-marketplace metrics into one display currency.

Rates are USD-based and fetched from a free, no-key endpoint
(``open.er-api.com``). They are cached in two layers so the hourly bot does not
hit the network on every run:

  1. An in-process cache for the lifetime of the warm function instance.
  2. A Firestore document (``app_config/currency_rates``) shared across
     instances, holding ``{rates, base, fetched_at}``.

Both layers honour a ~24h TTL and refresh lazily on read. On any fetch failure
the last cached rates are reused (logged as a WARNING); if no cache exists at
all, an empty mapping is returned so callers skip the converted Total rather
than assuming a 1:1 rate.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from shared.firestore_utils import get_db

logger = logging.getLogger(__name__)

_RATES_API_URL = "https://open.er-api.com/v6/latest/USD"
_BASE_CURRENCY = "USD"
_TTL_SECONDS = 24 * 60 * 60
_FETCH_TIMEOUT_SECONDS = 10

_CONFIG_COLLECTION = "app_config"
_RATES_DOC_ID = "currency_rates"

# Process-wide cache: {"rates": {...}, "base": "USD", "fetched_at": <epoch>}.
_cache: dict[str, Any] | None = None


def _now() -> float:
    return time.time()


def _is_fresh(entry: dict[str, Any] | None) -> bool:
    if not entry or not entry.get("rates"):
        return False
    fetched_at = entry.get("fetched_at")
    if not isinstance(fetched_at, (int, float)):
        return False
    return (_now() - float(fetched_at)) < _TTL_SECONDS


def _read_firestore() -> dict[str, Any] | None:
    try:
        doc = (
            get_db()
            .collection(_CONFIG_COLLECTION)
            .document(_RATES_DOC_ID)
            .get()
        )
    except Exception:
        logger.warning("Failed to read cached FX rates from Firestore", extra={"phase": "currency"})
        return None
    if not doc.exists:
        return None
    return doc.to_dict()


def _write_firestore(entry: dict[str, Any]) -> None:
    try:
        get_db().collection(_CONFIG_COLLECTION).document(_RATES_DOC_ID).set(entry)
    except Exception:
        logger.warning("Failed to persist FX rates to Firestore", extra={"phase": "currency"})


def _fetch_live() -> dict[str, float] | None:
    """Fetch fresh USD-based rates, or None on any failure."""
    try:
        resp = requests.get(_RATES_API_URL, timeout=_FETCH_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.warning("FX rate fetch failed", extra={"phase": "currency", "error_code": "FX_FETCH_FAILED"})
        return None

    if data.get("result") != "success":
        logger.warning(
            "FX rate API returned a non-success result",
            extra={"phase": "currency", "error_code": "FX_FETCH_FAILED"},
        )
        return None

    rates = data.get("rates")
    if not isinstance(rates, dict) or not rates:
        return None
    return {str(k): float(v) for k, v in rates.items() if isinstance(v, (int, float))}


def get_rates() -> dict[str, float]:
    """Return USD-based FX rates, refreshing lazily when stale.

    Resolution order: fresh in-process cache -> fresh Firestore cache -> live
    fetch. On a failed live fetch, falls back to any stale cache. Returns an
    empty mapping only when no rates are available anywhere (callers then skip
    the converted Total).
    """
    global _cache

    if _is_fresh(_cache):
        return _cache["rates"]  # type: ignore[index]

    stored = _read_firestore()
    if _is_fresh(stored):
        _cache = stored
        return stored["rates"]  # type: ignore[index]

    live = _fetch_live()
    if live is not None:
        _cache = {"rates": live, "base": _BASE_CURRENCY, "fetched_at": _now()}
        _write_firestore(_cache)
        return live

    # Live fetch failed — reuse the most recent stale cache we can find.
    fallback = _cache or stored
    if fallback and fallback.get("rates"):
        logger.warning(
            "Using stale FX rates after a failed refresh",
            extra={"phase": "currency", "error_code": "FX_STALE_CACHE"},
        )
        _cache = fallback
        return fallback["rates"]

    logger.warning(
        "No FX rates available — converted Totals will be skipped",
        extra={"phase": "currency", "error_code": "FX_UNAVAILABLE"},
    )
    return {}


def convert(
    amount: float,
    from_ccy: str,
    to_ccy: str,
    rates: dict[str, float],
) -> float | None:
    """Convert ``amount`` from one currency to another via the USD base.

    Returns ``None`` when either currency is missing from ``rates`` (or its rate
    is non-positive), so callers never silently assume a 1:1 conversion. An
    identity conversion (same currency) returns the amount unchanged without
    needing a rate.
    """
    if from_ccy == to_ccy:
        return amount

    from_rate = rates.get(from_ccy)
    to_rate = rates.get(to_ccy)
    if not from_rate or not to_rate or from_rate <= 0 or to_rate <= 0:
        return None

    usd = amount / from_rate
    return usd * to_rate
