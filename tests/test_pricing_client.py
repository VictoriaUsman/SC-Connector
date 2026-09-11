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
