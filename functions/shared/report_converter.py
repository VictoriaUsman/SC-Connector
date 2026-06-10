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

from shared.vendor_reports import VENDOR_REPORT_ARRAY_KEYS

logger = logging.getLogger(__name__)

_SP_API_ARRAY_KEYS: dict[str, list[str]] = {
    "GET_SALES_AND_TRAFFIC_REPORT": ["salesAndTrafficByDate", "salesAndTrafficByAsin"],
    "GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT": ["dataByDepartmentAndSearchTerm"],
    "GET_BRAND_ANALYTICS_MARKET_BASKET_REPORT": ["dataByDepartmentAndAsin"],
    "GET_BRAND_ANALYTICS_REPEAT_PURCHASE_REPORT": ["dataByDepartmentAndAsin"],
    "GET_BRAND_ANALYTICS_ALTERNATE_PURCHASE_REPORT": ["dataByDepartmentAndAsin"],
    "GET_BRAND_ANALYTICS_SEARCH_QUERY_PERFORMANCE_REPORT": ["dataByAsin"],
    "GET_BRAND_ANALYTICS_SEARCH_CATALOG_PERFORMANCE_REPORT": ["dataByAsin"],
    "GET_LEDGER_SUMMARY_VIEW_DATA": ["ledgerSummaryViewData"],
    # Vendor (1P) reports return nested JSON — flatten the same way.
    **VENDOR_REPORT_ARRAY_KEYS,
}

_SP_PERCENTAGE_SUFFIXES: frozenset[str] = frozenset({
    "Percentage", "Rate", "Share",
})


def _is_percentage_column(col: str) -> bool:
    """True when *col* ends with a known SP API percentage suffix."""
    leaf = col.rsplit(".", 1)[-1]
    return any(leaf.endswith(suffix) for suffix in _SP_PERCENTAGE_SUFFIXES)


def maybe_convert_to_tsv(
    raw: bytes,
    api_source: str,
    report_type: str,
    *,
    output_columns: list[str] | None = None,
    normalize_percentages: bool = False,
) -> tuple[bytes, bool]:
    """Convert a JSON report to TSV if applicable.

    Returns ``(content, converted)`` — the caller can use *converted* to
    override file extension and MIME type.

    Conversion rules:
    - **Ads API**: always converted (top-level JSON array).
    - **SP API**: converted only for known JSON report types.

    Optional post-processing:
    - *output_columns*: keep only these columns (in order). ``None`` = all.
    - *normalize_percentages*: divide SP API percentage fields by 100.
    """
    if api_source == "ads_api":
        result = _convert_ads(raw, output_columns=output_columns)
        return (result, result is not raw)

    if report_type in _SP_API_ARRAY_KEYS:
        result = _convert_sp(
            raw,
            report_type,
            output_columns=output_columns,
            normalize_percentages=normalize_percentages,
        )
        return (result, result is not raw)

    return raw, False


def rows_to_tsv(
    rows: list[dict[str, Any]],
    *,
    output_columns: list[str] | None = None,
) -> bytes:
    """Flatten an in-memory list of dict rows to TSV bytes (header + rows).

    Used by the synchronous ``fetch_api`` path, which already has parsed JSON
    rows (e.g. from the Replenishment API) rather than a raw report body. An
    empty list yields an empty byte string.
    """
    if not rows:
        return b""
    return _rows_to_tsv(rows, output_columns=output_columns)


# ------------------------------------------------------------------
# Internal converters
# ------------------------------------------------------------------

def _convert_sp(
    raw: bytes,
    report_type: str,
    *,
    output_columns: list[str] | None = None,
    normalize_percentages: bool = False,
) -> bytes:
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

    prepared: list[tuple[str, list[dict[str, Any]], list[str]]] = []
    for section_name, rows in sections:
        flat_rows = [_flatten_dict(row) for row in rows]
        all_keys = _stable_keys(flat_rows)
        cols = _apply_column_filter(all_keys, output_columns)
        if cols:
            prepared.append((section_name, flat_rows, cols))

    if not prepared:
        return raw

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", lineterminator="\n")
    multi = len(prepared) > 1

    for i, (section_name, flat_rows, cols) in enumerate(prepared):
        if i > 0:
            writer.writerow([])
            writer.writerow([])
        if multi:
            writer.writerow([f"# {section_name}"])
        _write_flat_rows(writer, flat_rows, cols, normalize_percentages)

    return buf.getvalue().encode("utf-8")


def _convert_ads(
    raw: bytes,
    *,
    output_columns: list[str] | None = None,
) -> bytes:
    """Ads API: parse a top-level JSON array (or dict-wrapped array) and flatten."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Failed to parse Ads API JSON, returning raw content")
        return raw

    rows = _extract_row_list(data)
    if rows is None:
        return raw

    return _rows_to_tsv(rows, output_columns=output_columns)


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


def _rows_to_tsv(
    rows: list[dict[str, Any]],
    *,
    output_columns: list[str] | None = None,
    normalize_percentages: bool = False,
) -> bytes:
    """Flatten a list of dicts and write as TSV with a header row."""
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", lineterminator="\n")
    flat_rows = [_flatten_dict(row) for row in rows]
    all_keys = _stable_keys(flat_rows)
    cols = _apply_column_filter(all_keys, output_columns)
    _write_flat_rows(writer, flat_rows, cols, normalize_percentages)
    return buf.getvalue().encode("utf-8")


def _apply_column_filter(
    all_keys: list[str],
    output_columns: list[str] | None,
) -> list[str]:
    """Return *output_columns* (preserving order) filtered to those in *all_keys*,
    or *all_keys* unchanged when *output_columns* is ``None``."""
    if output_columns is None:
        return all_keys
    available = set(all_keys)
    return [c for c in output_columns if c in available]


def _write_flat_rows(
    writer: csv.writer,
    flat_rows: list[dict[str, Any]],
    columns: list[str],
    normalize_percentages: bool,
) -> None:
    """Write header + data rows, optionally normalizing percentage values."""
    pct_cols = frozenset(c for c in columns if _is_percentage_column(c)) if normalize_percentages else frozenset()
    writer.writerow(columns)
    for flat in flat_rows:
        row: list[Any] = []
        for k in columns:
            v = flat.get(k, "")
            if k in pct_cols and isinstance(v, (int, float)):
                v = round(v / 100, 6)
            row.append(v)
        writer.writerow(row)


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
