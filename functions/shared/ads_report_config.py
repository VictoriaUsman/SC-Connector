"""Ads API v3 report configuration — single source of truth.

Maps each reportTypeId to its adProduct, default groupBy, and available columns
(split into dimensions and metrics for UI display). Used by create_report to
auto-populate required fields, and exposed via the API for frontend column pickers.
"""

from __future__ import annotations

ADS_REPORT_TYPES: dict[str, dict] = {
    "spCampaigns": {
        "adProduct": "SPONSORED_PRODUCTS",
        "groupBy": ["campaign"],
        "columns": {
            "dimensions": [
                "date", "campaignName", "campaignId", "campaignStatus",
                "campaignBudgetAmount", "campaignBudgetType",
            ],
            "metrics": [
                "impressions", "clicks", "cost",
                "purchases1d", "purchases7d", "purchases14d", "purchases30d",
                "sales1d", "sales7d", "sales14d", "sales30d",
                "unitsSoldClicks1d", "unitsSoldClicks7d", "unitsSoldClicks14d", "unitsSoldClicks30d",
            ],
        },
    },
    "spSearchTerm": {
        "adProduct": "SPONSORED_PRODUCTS",
        "groupBy": ["searchTerm"],
        "columns": {
            "dimensions": [
                "date", "searchTerm", "campaignName", "campaignId",
                "adGroupName", "adGroupId", "targeting", "keywordId", "keywordType",
            ],
            "metrics": [
                "impressions", "clicks", "cost",
                "purchases7d", "sales7d", "unitsSoldClicks7d",
            ],
        },
    },
    "spTargeting": {
        "adProduct": "SPONSORED_PRODUCTS",
        "groupBy": ["targeting"],
        "columns": {
            "dimensions": [
                "date", "targeting", "targetingId", "targetingType",
                "campaignName", "campaignId", "adGroupName", "adGroupId",
            ],
            "metrics": [
                "impressions", "clicks", "cost",
                "purchases7d", "sales7d", "unitsSoldClicks7d",
            ],
        },
    },
    "spAdvertisedProduct": {
        "adProduct": "SPONSORED_PRODUCTS",
        "groupBy": ["advertiser"],
        "columns": {
            "dimensions": [
                "date", "advertisedAsin", "advertisedSku",
                "campaignName", "campaignId", "adGroupName", "adGroupId",
            ],
            "metrics": [
                "impressions", "clicks", "cost",
                "purchases7d", "sales7d", "unitsSoldClicks7d",
            ],
        },
    },
    "sbCampaigns": {
        "adProduct": "SPONSORED_BRANDS",
        "groupBy": ["campaign"],
        "columns": {
            "dimensions": [
                "date", "campaignName", "campaignId", "campaignStatus",
                "campaignBudgetAmount",
            ],
            "metrics": [
                "impressions", "clicks", "cost",
                "purchases", "sales", "unitsSoldClicks",
                "detailPageViewsClicks", "newToBrandPurchases", "newToBrandSales",
            ],
        },
    },
    "sbSearchTerm": {
        "adProduct": "SPONSORED_BRANDS",
        "groupBy": ["searchTerm"],
        "columns": {
            "dimensions": [
                "date", "searchTerm", "campaignName", "campaignId",
                "adGroupName", "adGroupId",
            ],
            "metrics": [
                "impressions", "clicks", "cost",
                "purchases", "sales",
            ],
        },
    },
    "sdCampaigns": {
        "adProduct": "SPONSORED_DISPLAY",
        "groupBy": ["campaign"],
        "columns": {
            "dimensions": [
                "date", "campaignName", "campaignId", "campaignStatus",
                "campaignBudgetAmount",
            ],
            "metrics": [
                "impressions", "clicks", "cost",
                "purchases", "sales", "unitsSoldClicks",
                "detailPageViewsClicks", "newToBrandPurchases", "newToBrandSales",
            ],
        },
    },
    "sdTargeting": {
        "adProduct": "SPONSORED_DISPLAY",
        "groupBy": ["targeting"],
        "columns": {
            "dimensions": [
                "date", "targeting", "targetingId",
                "campaignName", "campaignId", "adGroupName", "adGroupId",
            ],
            "metrics": [
                "impressions", "clicks", "cost",
                "purchases", "sales", "unitsSoldClicks",
            ],
        },
    },
}

TIME_UNITS = ["DAILY", "SUMMARY"]


def get_all_columns(report_type: str) -> list[str]:
    """Return the full default column list (dimensions + metrics) for a report type."""
    config = ADS_REPORT_TYPES.get(report_type, {})
    cols = config.get("columns", {})
    return cols.get("dimensions", []) + cols.get("metrics", [])


def get_report_defaults(report_type: str) -> dict | None:
    """Return the full config for a report type, with a flat 'all_columns' list added."""
    config = ADS_REPORT_TYPES.get(report_type)
    if not config:
        return None
    return {
        **config,
        "all_columns": get_all_columns(report_type),
    }
