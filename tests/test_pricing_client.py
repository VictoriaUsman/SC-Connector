"""Tests for shared.pricing_client — active-ASIN sourcing + Buy Box checks."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")
os.environ.setdefault("BQ_DATASET", "test_dataset")


class TestGetActiveAsins:
    def test_queries_active_listings_latest_and_returns_asins(self):
        from shared import pricing_client

        fake_rows = [{"asin": "B0ABC12345"}, {"asin": "B0DEF67890"}]
        fake_client = MagicMock()
        fake_client.query.return_value = fake_rows

        with patch.object(pricing_client, "_get_bq_client", return_value=fake_client):
            asins = pricing_client.get_active_asins("itsbodily", "US")

        assert asins == ["B0ABC12345", "B0DEF67890"]
        query_sql = fake_client.query.call_args[0][0]
        assert "active_listings_latest" in query_sql
        assert "status" in query_sql

    def test_empty_result_returns_empty_list(self):
        from shared import pricing_client

        fake_client = MagicMock()
        fake_client.query.return_value = []

        with patch.object(pricing_client, "_get_bq_client", return_value=fake_client):
            asins = pricing_client.get_active_asins("itsbodily", "US")

        assert asins == []


class TestFetchBuyboxStatus:
    def _client_row(self):
        return {"id": "itsbodily", "selling_partner_id": "A38K1AMGZXHFY8"}

    def test_our_offer_wins_the_box(self):
        from shared import pricing_client

        response = {
            "payload": {
                "Summary": {"BuyBoxPrices": [{"ListingPrice": {"Amount": 19.99, "CurrencyCode": "USD"}}]},
                "Offers": [
                    {"SellerId": "A38K1AMGZXHFY8", "IsBuyBoxWinner": True,
                     "ListingPrice": {"Amount": 19.99, "CurrencyCode": "USD"}},
                    {"SellerId": "OTHERSELLER", "IsBuyBoxWinner": False,
                     "ListingPrice": {"Amount": 21.50, "CurrencyCode": "USD"}},
                ],
            }
        }
        with patch.object(pricing_client, "get_client", return_value=self._client_row()), \
             patch.object(pricing_client, "sp_api_request", return_value=response) as mock_req:
            rows = pricing_client.fetch_buybox_status("itsbodily", "US", ["B0ABC12345"])

        assert rows == [{
            "asin": "B0ABC12345",
            "is_buybox_winner": True,
            "buybox_price": 19.99,
            "your_price": 19.99,
            "currency": "USD",
            "offer_count": 2,
            "checked_at": rows[0]["checked_at"],
            "error": None,
        }]
        path = mock_req.call_args[0][3]
        assert path == "/products/pricing/v0/items/B0ABC12345/offers"

    def test_our_offer_exists_but_does_not_win(self):
        from shared import pricing_client

        response = {
            "payload": {
                "Summary": {"BuyBoxPrices": [{"ListingPrice": {"Amount": 21.50, "CurrencyCode": "USD"}}]},
                "Offers": [
                    {"SellerId": "A38K1AMGZXHFY8", "IsBuyBoxWinner": False,
                     "ListingPrice": {"Amount": 22.00, "CurrencyCode": "USD"}},
                    {"SellerId": "OTHERSELLER", "IsBuyBoxWinner": True,
                     "ListingPrice": {"Amount": 21.50, "CurrencyCode": "USD"}},
                ],
            }
        }
        with patch.object(pricing_client, "get_client", return_value=self._client_row()), \
             patch.object(pricing_client, "sp_api_request", return_value=response):
            rows = pricing_client.fetch_buybox_status("itsbodily", "US", ["B0ABC12345"])

        assert rows[0]["is_buybox_winner"] is False
        assert rows[0]["your_price"] == 22.00
        assert rows[0]["buybox_price"] == 21.50

    def test_no_offer_for_this_seller(self):
        from shared import pricing_client

        response = {
            "payload": {
                "Summary": {"BuyBoxPrices": [{"ListingPrice": {"Amount": 21.50, "CurrencyCode": "USD"}}]},
                "Offers": [
                    {"SellerId": "OTHERSELLER", "IsBuyBoxWinner": True,
                     "ListingPrice": {"Amount": 21.50, "CurrencyCode": "USD"}},
                ],
            }
        }
        with patch.object(pricing_client, "get_client", return_value=self._client_row()), \
             patch.object(pricing_client, "sp_api_request", return_value=response):
            rows = pricing_client.fetch_buybox_status("itsbodily", "US", ["B0ABC12345"])

        assert rows[0]["is_buybox_winner"] is None
        assert rows[0]["your_price"] is None
        assert rows[0]["error"] is None

    def test_one_asin_failure_does_not_abort_the_batch(self):
        from shared import pricing_client
        from shared.sp_api_rest import SPAPIRequestError

        ok_response = {
            "payload": {
                "Summary": {"BuyBoxPrices": []},
                "Offers": [{"SellerId": "A38K1AMGZXHFY8", "IsBuyBoxWinner": True,
                            "ListingPrice": {"Amount": 10.0, "CurrencyCode": "USD"}}],
            }
        }

        def _side_effect(client_id, marketplace, method, path, **kwargs):
            if "BADASIN" in path:
                raise SPAPIRequestError(404, "Not Found")
            return ok_response

        with patch.object(pricing_client, "get_client", return_value=self._client_row()), \
             patch.object(pricing_client, "sp_api_request", side_effect=_side_effect), \
             patch.object(pricing_client.time, "sleep"):
            rows = pricing_client.fetch_buybox_status(
                "itsbodily", "US", ["BADASIN", "GOODASIN"],
            )

        assert len(rows) == 2
        assert rows[0]["asin"] == "BADASIN"
        assert rows[0]["is_buybox_winner"] is None
        assert rows[0]["error"] == "SP-API 404: Not Found"
        assert rows[1]["asin"] == "GOODASIN"
        assert rows[1]["is_buybox_winner"] is True

    def test_persistent_network_error_on_one_asin_does_not_abort_the_batch(self):
        """sp_api_rest.sp_api_request re-raises the raw requests.RequestException
        unwrapped when every retry attempt fails with a network error (not
        every failure path wraps into SPAPIRequestError) — this must be
        caught per-ASIN too, or one bad ASIN kills the whole batch.
        """
        import requests

        from shared import pricing_client

        ok_response = {
            "payload": {
                "Summary": {"BuyBoxPrices": []},
                "Offers": [{"SellerId": "A38K1AMGZXHFY8", "IsBuyBoxWinner": True,
                            "ListingPrice": {"Amount": 10.0, "CurrencyCode": "USD"}}],
            }
        }

        def _side_effect(client_id, marketplace, method, path, **kwargs):
            if "BADASIN" in path:
                raise requests.RequestException("Connection reset by peer")
            return ok_response

        with patch.object(pricing_client, "get_client", return_value=self._client_row()), \
             patch.object(pricing_client, "sp_api_request", side_effect=_side_effect), \
             patch.object(pricing_client.time, "sleep"):
            rows = pricing_client.fetch_buybox_status(
                "itsbodily", "US", ["BADASIN", "GOODASIN"],
            )

        assert len(rows) == 2
        assert rows[0]["asin"] == "BADASIN"
        assert rows[0]["is_buybox_winner"] is None
        assert rows[0]["error"] == "Connection reset by peer"
        assert rows[1]["asin"] == "GOODASIN"
        assert rows[1]["is_buybox_winner"] is True

    def test_unknown_client_raises(self):
        from shared import pricing_client

        with patch.object(pricing_client, "get_client", return_value=None):
            try:
                pricing_client.fetch_buybox_status("nosuchclient", "US", ["B0ABC12345"])
                assert False, "expected ValueError"
            except ValueError as exc:
                assert "nosuchclient" in str(exc)

    def test_missing_selling_partner_id_raises(self):
        """A client record with no selling_partner_id configured must raise,
        not silently return is_buybox_winner=None for every row — that value
        is indistinguishable from a legitimate "we never win this box"
        result and would mask a config problem.
        """
        from shared import pricing_client

        client_row = {"id": "itsbodily"}  # no selling_partner_id key at all
        with patch.object(pricing_client, "get_client", return_value=client_row):
            try:
                pricing_client.fetch_buybox_status("itsbodily", "US", ["B0ABC12345"])
                assert False, "expected ValueError"
            except ValueError as exc:
                assert "itsbodily" in str(exc)
                assert "selling_partner_id" in str(exc)

    def test_empty_string_selling_partner_id_raises(self):
        from shared import pricing_client

        client_row = {"id": "itsbodily", "selling_partner_id": ""}
        with patch.object(pricing_client, "get_client", return_value=client_row):
            try:
                pricing_client.fetch_buybox_status("itsbodily", "US", ["B0ABC12345"])
                assert False, "expected ValueError"
            except ValueError as exc:
                assert "selling_partner_id" in str(exc)
