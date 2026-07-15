"""Regression tests locking in end-to-end AU (Amazon Australia) marketplace support.

AU is region ``fe`` (Far East), unlike the ``na``/``eu`` marketplaces that made
up the original set. These tests assert that AU is a first-class marketplace
across every backend map the report + daily-recap pipelines depend on, so it can
never silently drop out of the account selector's supported set (CU-868k6gq75).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))


class TestAuConfig:
    def test_au_marketplace_id(self):
        from shared.config import MARKETPLACE_IDS, get_marketplace_id

        # Amazon's Australia marketplace id.
        assert MARKETPLACE_IDS["AU"] == "A39IBJ37TRP1C6"
        assert get_marketplace_id("AU") == "A39IBJ37TRP1C6"

    def test_au_is_far_east_region(self):
        from shared.config import MARKETPLACE_TO_REGION

        assert MARKETPLACE_TO_REGION["AU"] == "fe"

    def test_au_resolves_to_far_east_api_endpoints(self):
        from shared.config import get_ads_api_endpoint, get_sp_api_endpoint

        assert get_sp_api_endpoint("AU") == "https://sellingpartnerapi-fe.amazon.com"
        assert get_ads_api_endpoint("AU") == "https://advertising-api-fe.amazon.com"

    def test_au_timezone_is_sydney(self):
        from shared.config import MARKETPLACE_TIMEZONES

        assert MARKETPLACE_TIMEZONES["AU"] == "Australia/Sydney"

    def test_au_sales_channel(self):
        from shared.config import get_marketplace_sales_channel

        assert get_marketplace_sales_channel("AU") == "Amazon.com.au"

    def test_au_currency_is_aud(self):
        from shared.slack_client import MARKETPLACE_CURRENCIES

        assert MARKETPLACE_CURRENCIES["AU"] == "AUD"


class TestAuAccountFamily:
    def test_au_suffix_is_a_known_marketplace_split(self):
        """``brand-au`` groups into the ``brand`` family like other marketplaces."""
        from slack_bot.main import account_family

        assert account_family("moxe-au") == "moxe"
        assert account_family("skylight-frame-au") == "skylight-frame"

    def test_au_timezone_present_in_slack_bot(self):
        from slack_bot.main import _MARKETPLACE_TIMEZONES

        assert _MARKETPLACE_TIMEZONES["AU"] == "Australia/Sydney"
