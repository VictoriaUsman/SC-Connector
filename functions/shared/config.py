"""Environment configuration, Amazon endpoint routing, and marketplace mappings."""

from __future__ import annotations

import os


def get_project() -> str:
    return os.environ.get("GCP_PROJECT", "")


def get_environment() -> str:
    return os.environ.get("ENVIRONMENT", "staging")


LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"

MARKETPLACE_TO_REGION: dict[str, str] = {
    "US": "na", "CA": "na", "MX": "na",
    "UK": "eu", "DE": "eu", "FR": "eu", "IT": "eu",
    "ES": "eu", "NL": "eu", "BE": "eu", "SE": "eu", "PL": "eu", "TR": "eu",
    "AU": "fe", "SG": "fe",
}

# Operations clock — the team's operating timezone (Philippine Time, UTC+8).
# Used to anchor the Sales & Traffic trailing-window end date to a single,
# marketplace-independent "run date" so that a run firing near a day boundary
# (e.g. ~6am PHT, which is the previous calendar day in UTC and in the western
# marketplaces) resolves to the operator's calendar date rather than each
# marketplace's local date. Overridable via the OPERATIONS_TIMEZONE env var.
OPERATIONS_TIMEZONE: str = os.environ.get("OPERATIONS_TIMEZONE", "Asia/Manila")

MARKETPLACE_TIMEZONES: dict[str, str] = {
    "US": "America/Los_Angeles",
    "CA": "America/Los_Angeles",
    "MX": "America/Los_Angeles",
    "UK": "Europe/London",
    "DE": "Europe/Paris",
    "FR": "Europe/Paris",
    "IT": "Europe/Paris",
    "ES": "Europe/Paris",
    "NL": "Europe/Paris",
    "BE": "Europe/Paris",
    "SE": "Europe/Paris",
    "PL": "Europe/Paris",
    "TR": "Europe/Istanbul",
    "AU": "Australia/Sydney",
    "SG": "Asia/Singapore",
}

SP_API_ENDPOINTS: dict[str, str] = {
    "na": "https://sellingpartnerapi-na.amazon.com",
    "eu": "https://sellingpartnerapi-eu.amazon.com",
    "fe": "https://sellingpartnerapi-fe.amazon.com",
}

ADS_API_ENDPOINTS: dict[str, str] = {
    "na": "https://advertising-api.amazon.com",
    "eu": "https://advertising-api-eu.amazon.com",
    "fe": "https://advertising-api-fe.amazon.com",
}

MARKETPLACE_IDS: dict[str, str] = {
    "US": "ATVPDKIKX0DER",
    "CA": "A2EUQ1WTGCTBG2",
    "MX": "A1AM78C64UM0Y8",
    "UK": "A1F83G8C2ARO7P",
    "DE": "A1PA6795UKMFR9",
    "FR": "A13V1IB3VIYZZH",
    "IT": "APJ6JRA9NG5V4",
    "ES": "A1RKKUPIHCS9HS",
    "NL": "A1805IZSGTT6HS",
    "BE": "AMEN7PMS3EDWL",
    "SE": "A2NODRKZP88ZB9",
    "PL": "A1C3SOZRARQ6R3",
    "AU": "A39IBJ37TRP1C6",
}

# Amazon "sales-channel" value (the storefront domain) carried on every line of
# the All Orders flat file (GET_FLAT_FILE_ALL_ORDERS_DATA_BY_LAST_UPDATE_GENERAL).
# That report is account-wide: Amazon returns every order for the seller's
# region regardless of the marketplaceId requested, and the connector stamps
# all of those rows with the single marketplace it pulled under. Filtering only
# on that stamped marketplace therefore leaks other marketplaces' orders into a
# per-marketplace Total Sales total. The sales-channel column is the only
# per-row signal of the order's true marketplace, so order queries that must be
# scoped to one marketplace filter on it. Compared case-insensitively.
MARKETPLACE_SALES_CHANNELS: dict[str, str] = {
    "US": "Amazon.com",
    "CA": "Amazon.ca",
    "MX": "Amazon.com.mx",
    "UK": "Amazon.co.uk",
    "DE": "Amazon.de",
    "FR": "Amazon.fr",
    "IT": "Amazon.it",
    "ES": "Amazon.es",
    "NL": "Amazon.nl",
    "BE": "Amazon.com.be",
    "SE": "Amazon.se",
    "PL": "Amazon.pl",
    "TR": "Amazon.com.tr",
    "AU": "Amazon.com.au",
    "SG": "Amazon.sg",
}


def get_marketplace_sales_channel(marketplace: str) -> str | None:
    """Return the Amazon storefront domain (``sales-channel``) for a marketplace.

    Returns ``None`` for unknown marketplaces so callers can fall back to their
    prior, unscoped behaviour rather than filtering everything out.
    """
    return MARKETPLACE_SALES_CHANNELS.get(marketplace)


def get_sp_api_endpoint(marketplace: str) -> str:
    region = MARKETPLACE_TO_REGION.get(marketplace, "na")
    return SP_API_ENDPOINTS[region]


def get_ads_api_endpoint(marketplace: str) -> str:
    region = MARKETPLACE_TO_REGION.get(marketplace, "na")
    return ADS_API_ENDPOINTS[region]


def get_marketplace_id(marketplace: str) -> str:
    mid = MARKETPLACE_IDS.get(marketplace)
    if not mid:
        raise ValueError(f"Unknown marketplace: {marketplace}")
    return mid
