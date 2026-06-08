"""Registry of SP-API report types Amazon has permanently removed.

Some report types are still accepted by ``createReport`` (HTTP 202) but the
report is immediately marked ``CANCELLED`` by Amazon, so the pipeline surfaces a
generic "No data available" failure and burns the very limited ``createReport``
quota on every run.  This module is the single source of truth for those
report types so the scheduler/launcher fan-out and the create_report function
can reject them up front with clear, actionable guidance.

The Subscribe & Save reports below were deprecated on 2025-07-25 (empty
responses) and fully removed from the SP-API on 2025-12-11.  Amazon's
recommended replacement is the Replenishment API (v2022-11-07).
"""

from __future__ import annotations

FBA_SNS_PERFORMANCE_REPORT = "GET_FBA_SNS_PERFORMANCE_DATA"
FBA_SNS_FORECAST_REPORT = "GET_FBA_SNS_FORECAST_DATA"

# Maps a removed SP-API report type to an actionable, human-readable reason.
REMOVED_SP_REPORT_TYPES: dict[str, str] = {
    FBA_SNS_PERFORMANCE_REPORT: (
        "Amazon removed the Subscribe & Save Performance report "
        "(GET_FBA_SNS_PERFORMANCE_DATA) from the SP-API on 2025-12-11; requests "
        "are accepted but the report is immediately cancelled, so it can no "
        "longer be generated. Remove it from this schedule and pull Subscribe & "
        "Save metrics from the Replenishment API (v2022-11-07) or the Subscribe "
        "& Save Program Page in Seller Central instead."
    ),
    FBA_SNS_FORECAST_REPORT: (
        "Amazon removed the Subscribe & Save Forecast report "
        "(GET_FBA_SNS_FORECAST_DATA) from the SP-API on 2025-12-11; requests are "
        "accepted but the report is immediately cancelled, so it can no longer "
        "be generated. Remove it from this schedule and use the Replenishment "
        "API (v2022-11-07) for Subscribe & Save metrics."
    ),
}


def is_removed_report_type(report_type: str) -> bool:
    """Return True if *report_type* is a removed SP-API report."""
    return report_type in REMOVED_SP_REPORT_TYPES


def removed_report_reason(report_type: str) -> str | None:
    """Return an actionable message if *report_type* is removed, else None."""
    return REMOVED_SP_REPORT_TYPES.get(report_type)
