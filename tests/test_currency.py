"""Tests for live FX rates and currency conversion (functions/shared/currency.py)."""

from __future__ import annotations

import os
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

import shared.currency as currency  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test starts and ends with a clean in-process cache."""
    currency._cache = None
    yield
    currency._cache = None


class TestConvert:
    _rates = {"USD": 1.0, "EUR": 0.5, "GBP": 0.8, "CAD": 1.25}

    def test_identity_returns_amount_without_rate(self):
        # Same currency never needs a rate, even one missing from the table.
        assert currency.convert(123.45, "JPY", "JPY", {}) == 123.45

    def test_cross_convert_via_usd_base(self):
        # 100 USD -> EUR = 100 / 1.0 * 0.5 = 50.
        assert currency.convert(100.0, "USD", "EUR", self._rates) == 50.0
        # 100 CAD -> USD = 100 / 1.25 * 1.0 = 80.
        assert currency.convert(100.0, "CAD", "USD", self._rates) == 80.0

    def test_missing_currency_returns_none(self):
        assert currency.convert(100.0, "USD", "JPY", self._rates) is None
        assert currency.convert(100.0, "AAA", "USD", self._rates) is None

    def test_non_positive_rate_returns_none(self):
        assert currency.convert(100.0, "USD", "ZZZ", {"USD": 1.0, "ZZZ": 0.0}) is None

    def test_empty_rates_returns_none_for_cross(self):
        assert currency.convert(100.0, "USD", "EUR", {}) is None


class TestGetRates:
    @staticmethod
    def _ok_response(rates: dict) -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"result": "success", "base_code": "USD", "rates": rates}
        return resp

    def test_fetches_live_persists_and_then_uses_in_process_cache(self):
        resp = self._ok_response({"USD": 1.0, "EUR": 0.9})
        with (
            patch("shared.currency._read_firestore", return_value=None),
            patch("shared.currency._write_firestore") as mock_write,
            patch("shared.currency.requests.get", return_value=resp) as mock_get,
        ):
            rates = currency.get_rates()
            # Second call must be served from the in-process cache (no refetch).
            rates_again = currency.get_rates()

        assert rates == {"USD": 1.0, "EUR": 0.9}
        assert rates_again == {"USD": 1.0, "EUR": 0.9}
        mock_get.assert_called_once()
        mock_write.assert_called_once()

    def test_uses_fresh_firestore_cache_without_fetching(self):
        stored = {"rates": {"USD": 1.0, "GBP": 0.8}, "base": "USD", "fetched_at": time.time()}
        with (
            patch("shared.currency._read_firestore", return_value=stored),
            patch("shared.currency.requests.get") as mock_get,
        ):
            rates = currency.get_rates()

        assert rates == {"USD": 1.0, "GBP": 0.8}
        mock_get.assert_not_called()

    def test_falls_back_to_stale_cache_when_fetch_fails(self):
        stale = {
            "rates": {"USD": 1.0, "EUR": 0.7},
            "base": "USD",
            "fetched_at": time.time() - 10 * 24 * 60 * 60,  # well past TTL
        }
        with (
            patch("shared.currency._read_firestore", return_value=stale),
            patch("shared.currency.requests.get", side_effect=RuntimeError("network down")),
        ):
            rates = currency.get_rates()

        # Never assume 1:1 — reuse the last known rates instead.
        assert rates == {"USD": 1.0, "EUR": 0.7}

    def test_empty_when_no_cache_and_fetch_fails(self):
        with (
            patch("shared.currency._read_firestore", return_value=None),
            patch("shared.currency.requests.get", side_effect=RuntimeError("network down")),
        ):
            assert currency.get_rates() == {}

    def test_non_success_payload_is_treated_as_failure(self):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"result": "error", "error-type": "invalid-key"}
        with (
            patch("shared.currency._read_firestore", return_value=None),
            patch("shared.currency.requests.get", return_value=resp),
        ):
            assert currency.get_rates() == {}
