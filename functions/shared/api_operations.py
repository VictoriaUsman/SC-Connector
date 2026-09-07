"""Registry of synchronous API "operations" — the non-report data pulls.

Some Amazon data products are plain synchronous REST endpoints rather than the
asynchronous create-report -> poll -> download flow. The Replenishment API
(Subscribe & Save) is the first such consumer. This module is the single source
of truth that lets the launcher route these to the synchronous ``fetch_api``
path (``mode="api_call"``) and lets ``fetch_api`` dispatch to the right client.

It is the synchronous-call analogue of ``ads_report_config.ADS_REPORT_TYPES``.

Each entry:
- ``api_source``  : "sp_api" | "ads_api"
- ``label``       : human-readable name
- ``handler``     : dispatch key resolved by ``functions/fetch_api/main.py``
- ``aggregation`` : default aggregation frequency for metric pulls (or None)
- ``bq_table``    : BigQuery table name for ingestion (or None to skip BQ)
- ``replaces``    : optional list of removed report types this supersedes
"""

from __future__ import annotations

# Subscribe & Save (Replenishment API v2022-11-07) — replaces the SP-API
# GET_FBA_SNS_* reports Amazon removed on 2025-12-11.
SNS_OFFER_METRICS = "SNS_OFFER_METRICS"
SNS_SP_METRICS = "SNS_SP_METRICS"
SNS_OFFERS = "SNS_OFFERS"

# Finances API v2024-06-19 (listTransactions) — replaces the SP-API report
# GET_DATE_RANGE_FINANCIAL_TRANSACTION_DATA, which Amazon deprecated from
# programmatic (Reports API) requests; see shared.removed_reports.
SP_FINANCE_TRANSACTIONS = "SP_FINANCE_TRANSACTIONS"

API_OPERATIONS: dict[str, dict] = {
    SNS_OFFER_METRICS: {
        "api_source": "sp_api",
        "label": "Subscribe & Save \u2014 Offer Metrics",
        "description": (
            "Per-ASIN Subscribe & Save performance metrics from the Replenishment "
            "API (v2022-11-07). Replaces the removed GET_FBA_SNS_PERFORMANCE_DATA report."
        ),
        "handler": "replenishment_offer_metrics",
        "aggregation": "WEEK",
        "bq_table": "sns_offer_metrics",
        "replaces": ["GET_FBA_SNS_PERFORMANCE_DATA"],
    },
    SNS_SP_METRICS: {
        "api_source": "sp_api",
        "label": "Subscribe & Save \u2014 Account Metrics",
        "description": (
            "Account-level Subscribe & Save business metrics from the Replenishment "
            "API (v2022-11-07)."
        ),
        "handler": "replenishment_sp_metrics",
        "aggregation": "WEEK",
        "bq_table": "sns_sp_metrics",
    },
    SNS_OFFERS: {
        "api_source": "sp_api",
        "label": "Subscribe & Save \u2014 Offers",
        "description": (
            "Subscribe & Save program offer and enrollment details from the "
            "Replenishment API (v2022-11-07)."
        ),
        "handler": "replenishment_offers",
        "aggregation": None,
        "bq_table": None,
    },
    SP_FINANCE_TRANSACTIONS: {
        "api_source": "sp_api",
        "label": "Financial Transactions (Finances API)",
        "description": (
            "Line-item financial transactions (sales, refunds, fees, "
            "reimbursements, adjustments) for a custom date range from the "
            "Finances API (v2024-06-19). Replaces the removed "
            "GET_DATE_RANGE_FINANCIAL_TRANSACTION_DATA report."
        ),
        "handler": "finance_transactions",
        "aggregation": None,
        "bq_table": "finance_transactions",
        "replaces": ["GET_DATE_RANGE_FINANCIAL_TRANSACTION_DATA"],
    },
}


def is_api_operation(report_type: str) -> bool:
    """Return True if *report_type* is a synchronous API operation."""
    return report_type in API_OPERATIONS


def get_api_operation(report_type: str) -> dict | None:
    """Return the operation config for *report_type*, or None."""
    return API_OPERATIONS.get(report_type)
