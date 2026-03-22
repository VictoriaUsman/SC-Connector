"""Convert JSON reports to flat TSV for spreadsheet-friendly output.

Handles both SP API reports (nested dicts with known array keys) and
Ads API v3 reports (top-level JSON arrays of flat objects).  The single
public entry point is ``maybe_convert_to_tsv()``.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_SP_API_ARRAY_KEYS: dict[str, list[str]] = {
    "GET_SALES_AND_TRAFFIC_REPORT": ["salesAndTrafficByDate", "salesAndTrafficByAsin"],
    "GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT": ["dataByDepartmentAndSearchTerm"],
    "GET_BRAND_ANALYTICS_MARKET_BASKET_REPORT": ["dataByDepartmentAndAsin"],
    "GET_BRAND_ANALYTICS_REPEAT_PURCHASE_REPORT": ["dataByDepartmentAndAsin"],
    "GET_BRAND_ANALYTICS_ALTERNATE_PURCHASE_REPORT": ["dataByDepartmentAndAsin"],
    "GET_LEDGER_SUMMARY_VIEW_DATA": ["ledgerSummaryViewData"],
}


def maybe_convert_to_tsv(
    raw: bytes,
    api_source: str,
    report_type: str,
) -> tuple[bytes, bool]:
    """Convert a JSON report to TSV if applicable.

    Returns ``(content, converted)`` — the caller can use *converted* to
    override file extension and MIME type.

    Conversion rules:
    - **Ads API**: always converted (top-level JSON array).
    - **SP API**: converted only for known JSON report types.
    """
    if api_source == "ads_api":
        result = _convert_ads(raw)
        return (result, result is not raw)

    if report_type in _SP_API_ARRAY_KEYS:
        result = _convert_sp(raw, report_type)
        return (result, result is not raw)

    return raw, False


# ------------------------------------------------------------------
# Internal converters
# ------------------------------------------------------------------

def _convert_sp(raw: bytes, report_type: str) -> bytes:
    """SP API: locate known array key(s) inside a dict and flatten each section."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Failed to parse JSON for %s, returning raw content", report_type)
        return raw

    if not isinstance(data, dict):
        return raw

    array_keys = _SP_API_ARRAY_KEYS.get(report_type, [])
    if not array_keys:
        return _extract_and_flatten(data)

    sections: list[tuple[str, list[dict[str, Any]]]] = []
    for key in array_keys:
        arr = data.get(key)
        if isinstance(arr, list) and arr:
            sections.append((key, arr))

    if not sections:
        return _extract_and_flatten(data)

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", lineterminator="\n")

    for i, (section_name, rows) in enumerate(sections):
        if i > 0:
            writer.writerow([])
            writer.writerow([])
        if len(sections) > 1:
            writer.writerow([f"# {section_name}"])
        _write_rows(writer, rows)

    return buf.getvalue().encode("utf-8")


def _convert_ads(raw: bytes) -> bytes:
    """Ads API: parse a top-level JSON array (or dict-wrapped array) and flatten."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Failed to parse Ads API JSON, returning raw content")
        return raw

    rows = _extract_row_list(data)
    if rows is None:
        return raw

    return _rows_to_tsv(rows)


# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------

def _extract_row_list(data: Any) -> list[dict[str, Any]] | None:
    """Return the primary list-of-dicts from *data*, or None."""
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return None


def _extract_and_flatten(data: dict) -> bytes:
    """Best-effort flattening when we don't know the report structure."""
    rows = _extract_row_list(data)
    if rows is not None:
        return _rows_to_tsv(rows)

    flat = _flatten_dict(data)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", lineterminator="\n")
    writer.writerow(list(flat.keys()))
    writer.writerow(list(flat.values()))
    return buf.getvalue().encode("utf-8")


def _rows_to_tsv(rows: list[dict[str, Any]]) -> bytes:
    """Flatten a list of dicts and write as TSV with a header row."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", lineterminator="\n")
    _write_rows(writer, rows)
    return buf.getvalue().encode("utf-8")


def _write_rows(writer: csv.writer, rows: list[dict[str, Any]]) -> None:
    """Flatten *rows* and append header + data lines to *writer*."""
    flat_rows = [_flatten_dict(row) for row in rows]
    all_keys = _stable_keys(flat_rows)
    writer.writerow(all_keys)
    for flat in flat_rows:
        writer.writerow([flat.get(k, "") for k in all_keys])


def _flatten_dict(d: Any, prefix: str = "") -> dict[str, Any]:
    """Recursively flatten a nested dict. ``{"a": {"b": 1}}`` → ``{"a.b": 1}``."""
    out: dict[str, Any] = {}
    if not isinstance(d, dict):
        return {prefix: d} if prefix else {}

    for key, value in d.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten_dict(value, full_key))
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            for i, item in enumerate(value):
                out.update(_flatten_dict(item, f"{full_key}[{i}]"))
        else:
            out[full_key] = value
    return out


def _stable_keys(rows: list[dict[str, Any]]) -> list[str]:
    """Collect all keys preserving first-seen order."""
    seen: dict[str, None] = {}
    for row in rows:
        for k in row:
            if k not in seen:
                seen[k] = None
    return list(seen.keys())
