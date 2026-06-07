"""Structured Cloud Logging + correlation context (agent-friendly logs).

Emits one JSON object per log line to stdout in the shape Cloud Run / Cloud
Functions (Gen2) parse natively: ``severity`` and ``message`` are promoted and
everything else is placed in ``jsonPayload`` so the fields are queryable. This is
the "log contract" the log-reading agent depends on - the stable field names are
listed in ``CONTRACT_FIELDS`` and documented in AGENTS.md.

Stdlib only (no new dependency, fast cold start). Existing call sites that use
``logger.info("msg", extra={"job_id": ...})`` automatically get structured
fields - no rewrites required.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import uuid
from typing import Any

# Correlation fields bound for the lifetime of one request/execution. Every log
# line emitted while these are bound carries them, so a single filter (e.g.
# jsonPayload.job_id="...") returns the whole story for one job.
_log_context: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "kalilos_log_context", default={}
)

# The stable log contract. These jsonPayload keys have fixed names/meanings so
# the agent can filter precisely without guessing. Keep names stable; documented
# in AGENTS.md ("Log contract").
CONTRACT_FIELDS: tuple[str, ...] = (
    "job_id",
    "request_id",
    "client_id",
    "api_source",
    "report_type",
    "marketplace",
    "phase",
    "component",
    "error_code",
    "error_type",
)

# Standard LogRecord attributes; anything else attached to a record is treated as
# a user-supplied "extra" and flattened into the JSON payload.
_RESERVED_RECORD_KEYS: frozenset[str] = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message", "asctime",
    }
)

_LEVEL_TO_SEVERITY: dict[int, str] = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}

# Special Cloud Logging fields (entries auto-group by request in Logs Explorer).
_TRACE_FIELD = "logging.googleapis.com/trace"
_SPAN_FIELD = "logging.googleapis.com/spanId"


class StructuredLogFormatter(logging.Formatter):
    """Render a LogRecord as a single Cloud Logging structured JSON line."""

    def __init__(self, component: str) -> None:
        super().__init__()
        self._component = component

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": _LEVEL_TO_SEVERITY.get(record.levelno, record.levelname),
            "message": record.getMessage(),
            "component": self._component,
            "logger": record.name,
        }

        # Bound correlation context first, then per-call extras (extras win).
        payload.update(_log_context.get())
        for key, value in record.__dict__.items():
            if key in _RESERVED_RECORD_KEYS or key.startswith("_"):
                continue
            payload[key] = value

        # Promote trace/span to the special Cloud Logging fields when present.
        trace = payload.pop("trace", None)
        if trace:
            payload[_TRACE_FIELD] = trace
        span_id = payload.pop("span_id", None)
        if span_id:
            payload[_SPAN_FIELD] = span_id

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str)


def init_logging(component: str, *, level: str | None = None) -> None:
    """Install the structured handler on the root logger (idempotent).

    Call once at module import in each function's ``main.py``. ``component`` is
    the function name (e.g. ``"create-report"``) and is stamped on every line.
    Level comes from the ``LOG_LEVEL`` env var (default ``INFO``).
    """
    resolved_level = (level or os.environ.get("LOG_LEVEL") or "INFO").upper()

    handler = logging.StreamHandler()
    handler.setFormatter(StructuredLogFormatter(component))

    root = logging.getLogger()
    # Replace any pre-existing handlers so we emit exactly one structured line.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(resolved_level)


def bind_log_context(**fields: Any) -> None:
    """Add correlation fields to the current context (None values are ignored)."""
    current = dict(_log_context.get())
    for key, value in fields.items():
        if value is not None:
            current[key] = value
    _log_context.set(current)


def clear_log_context() -> None:
    """Reset correlation context.

    Call at the start of each invocation so a reused (warm) instance never leaks
    one request's ids into the next.
    """
    _log_context.set({})


def new_request_id() -> str:
    """Generate a short, unique id for correlating one API request."""
    return uuid.uuid4().hex


def trace_from_cloud_header(
    header: str | None, project: str | None = None
) -> tuple[str | None, str | None]:
    """Parse an ``X-Cloud-Trace-Context`` header into ``(trace, span_id)``.

    Header form: ``TRACE_ID/SPAN_ID;o=1``. Returns the fully-qualified trace
    resource name Cloud Logging expects, or ``(None, None)`` when unavailable.
    """
    if not header:
        return None, None
    trace_id = header.split("/", 1)[0].strip()
    if not trace_id:
        return None, None
    span_part = ""
    if "/" in header:
        span_part = header.split("/", 1)[1].split(";", 1)[0].strip()
    project = project or os.environ.get("GCP_PROJECT", "")
    trace = f"projects/{project}/traces/{trace_id}" if project else None
    return trace, (span_part or None)
