"""Environment configuration, Amazon endpoint routing, and marketplace mappings."""

from __future__ import annotations

import os


def get_project() -> str:
    return os.environ.get("GCP_PROJECT", "")


def get_environment() -> str:
    return os.environ.get("ENVIRONMENT", "staging")


LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"

MARKETPLACE_TO_REGION: dict[str, str] = {
    "US": "na", "CA": "na", "MX": "na", "BR": "na",
    "UK": "eu", "DE": "eu", "FR": "eu", "IT": "eu",
    "ES": "eu", "NL": "eu", "SE": "eu", "PL": "eu", "TR": "eu",
    "JP": "fe", "AU": "fe", "IN": "fe", "SG": "fe",
}

MARKETPLACE_TIMEZONES: dict[str, str] = {
    "US": "America/Los_Angeles",
    "CA": "America/Los_Angeles",
    "MX": "America/Los_Angeles",
    "BR": "America/Sao_Paulo",
    "UK": "Europe/London",
    "DE": "Europe/Paris",
    "FR": "Europe/Paris",
    "IT": "Europe/Paris",
    "ES": "Europe/Paris",
    "NL": "Europe/Paris",
    "SE": "Europe/Paris",
    "PL": "Europe/Paris",
    "TR": "Europe/Istanbul",
    "JP": "Asia/Tokyo",
    "AU": "Australia/Sydney",
    "IN": "Asia/Kolkata",
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
    "JP": "A1VC38T7YXB528",
    "AU": "A39IBJ37TRP1C6",
    "IN": "A21TJRUUN4KGV",
}


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
