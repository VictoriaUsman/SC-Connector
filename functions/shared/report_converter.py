"""Convert nested JSON reports to flat TSV for spreadsheet-friendly output."""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_JSON_REPORT_ARRAY_KEYS: dict[str, list[str]] = {
    "GET_SALES_AND_TRAFFIC_REPORT": ["salesAndTrafficByDate", "salesAndTrafficByAsin"],
    "GET_BRAND_ANALYTICS_SEARCH_TERMS_REPORT": ["dataByDepartmentAndSearchTerm"],
    "GET_BRAND_ANALYTICS_MARKET_BASKET_REPORT": ["dataByDepartmentAndAsin"],
    "GET_BRAND_ANALYTICS_REPEAT_PURCHASE_REPORT": ["dataByDepartmentAndAsin"],
    "GET_BRAND_ANALYTICS_ALTERNATE_PURCHASE_REPORT": ["dataByDepartmentAndAsin"],
    "GET_LEDGER_SUMMARY_VIEW_DATA": ["ledgerSummaryViewData"],
}


def should_convert(report_type: str) -> bool:
    return report_type in _JSON_REPORT_ARRAY_KEYS


def json_report_to_tsv(raw: bytes, report_type: str) -> bytes:
    """Parse a JSON SP API report and flatten it into TSV.

    Locates the main data array(s) inside the JSON, recursively flattens
    nested objects (e.g. ``orderedProductSales.amount``), and writes
    tab-separated output with a header row.  If the JSON contains multiple
    data arrays (e.g. byDate + byAsin), each section is separated by an
    empty line with a section header.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Failed to parse JSON for %s, returning raw content", report_type)
        return raw

    if not isinstance(data, dict):
        return raw

    array_keys = _JSON_REPORT_ARRAY_KEYS.get(report_type, [])
    if not array_keys:
        return _flatten_generic(data)

    sections: list[tuple[str, list[dict[str, Any]]]] = []
    for key in array_keys:
        arr = data.get(key)
        if isinstance(arr, list) and arr:
            sections.append((key, arr))

    if not sections:
        return _flatten_generic(data)

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", lineterminator="\n")

    for i, (section_name, rows) in enumerate(sections):
        if i > 0:
            writer.writerow([])
            writer.writerow([])

        if len(sections) > 1:
            writer.writerow([f"# {section_name}"])

        flat_rows = [_flatten_dict(row) for row in rows]
        all_keys = _stable_keys(flat_rows)

        writer.writerow(all_keys)
        for flat in flat_rows:
            writer.writerow([flat.get(k, "") for k in all_keys])

    return buf.getvalue().encode("utf-8")


def _flatten_generic(data: dict) -> bytes:
    """Best-effort flattening when we don't know the report structure."""
    for value in data.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            flat_rows = [_flatten_dict(row) for row in value]
            all_keys = _stable_keys(flat_rows)
            buf = io.StringIO()
            writer = csv.writer(buf, delimiter="\t", lineterminator="\n")
            writer.writerow(all_keys)
            for flat in flat_rows:
                writer.writerow([flat.get(k, "") for k in all_keys])
            return buf.getvalue().encode("utf-8")

    flat = _flatten_dict(data)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter="\t", lineterminator="\n")
    writer.writerow(list(flat.keys()))
    writer.writerow(list(flat.values()))
    return buf.getvalue().encode("utf-8")


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
