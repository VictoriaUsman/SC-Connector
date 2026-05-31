"""Throttle detection for Amazon API rate-limit errors.

Used by Cloud Functions to distinguish throttled requests (HTTP 429)
from real failures (HTTP 500), enabling the Cloud Workflow to retry
with longer backoff instead of marking the job as failed immediately.
"""

from __future__ import annotations

import re

_THROTTLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"throttl", re.IGNORECASE),
    re.compile(r"quota.?exceeded", re.IGNORECASE),
    re.compile(r"too.?many.?request", re.IGNORECASE),
    re.compile(r"\b429\b"),
    re.compile(r"rate.?limit", re.IGNORECASE),
    re.compile(r"request.?limit", re.IGNORECASE),
]


def is_throttled(exc: Exception) -> bool:
    """Return True if the exception message indicates Amazon rate limiting."""
    msg = str(exc)
    return any(p.search(msg) for p in _THROTTLE_PATTERNS)
