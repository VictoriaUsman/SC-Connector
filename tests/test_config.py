"""Tests for shared.config marketplace routing.

Focused on the EU marketplaces added for client onboarding (NL, BE, SE, PL):
each must resolve to a marketplace id and route to the EU SP-API / Ads API
endpoints, without disturbing the existing marketplace entries.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")

from shared import config  # noqa: E402

# The four marketplaces enabled for onboarding, with their official Amazon
# marketplace ids. All belong to the EU region alongside UK/DE/FR/IT/ES.
NEW_EU_MARKETPLACES = {
    "NL": "A1805IZSGTT6HS",
    "BE": "AMEN7PMS3EDWL",
    "SE": "A2NODRKZP88ZB9",
    "PL": "A1C3SOZRARQ6R3",
}


@pytest.mark.parametrize("marketplace, expected_id", NEW_EU_MARKETPLACES.items())
def test_new_marketplace_id(marketplace: str, expected_id: str) -> None:
    assert config.get_marketplace_id(marketplace) == expected_id


@pytest.mark.parametrize("marketplace", NEW_EU_MARKETPLACES)
def test_new_marketplace_region_is_eu(marketplace: str) -> None:
    assert config.MARKETPLACE_TO_REGION[marketplace] == "eu"


@pytest.mark.parametrize("marketplace", NEW_EU_MARKETPLACES)
def test_new_marketplace_sp_api_endpoint_is_eu(marketplace: str) -> None:
    assert config.get_sp_api_endpoint(marketplace) == config.SP_API_ENDPOINTS["eu"]


@pytest.mark.parametrize("marketplace", NEW_EU_MARKETPLACES)
def test_new_marketplace_ads_api_endpoint_is_eu(marketplace: str) -> None:
    assert config.get_ads_api_endpoint(marketplace) == config.ADS_API_ENDPOINTS["eu"]


@pytest.mark.parametrize("marketplace", NEW_EU_MARKETPLACES)
def test_new_marketplace_has_timezone(marketplace: str) -> None:
    assert config.MARKETPLACE_TIMEZONES[marketplace] == "Europe/Paris"


@pytest.mark.parametrize("marketplace", NEW_EU_MARKETPLACES)
def test_new_marketplace_has_sales_channel(marketplace: str) -> None:
    assert config.get_marketplace_sales_channel(marketplace)


def test_existing_marketplaces_unchanged() -> None:
    # Guard against regressions to the previously supported marketplaces.
    assert config.MARKETPLACE_IDS["US"] == "ATVPDKIKX0DER"
    assert config.MARKETPLACE_IDS["UK"] == "A1F83G8C2ARO7P"
    assert config.MARKETPLACE_IDS["AU"] == "A39IBJ37TRP1C6"
    assert config.get_sp_api_endpoint("US") == config.SP_API_ENDPOINTS["na"]
    assert config.get_ads_api_endpoint("AU") == config.ADS_API_ENDPOINTS["fe"]
