"""Tests for fetch_api's operation dispatch (mode="api_call" routing).

Narrow coverage: just the new SP_FINANCE_TRANSACTIONS dispatch branch, since
the reportOptions.transactionStatus extraction is easy to get wrong silently
(a typo'd key just means "fetch everything" instead of erroring).
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

# fetch_api.main calls shared.logging_setup.init_logging() at module import
# time, which replaces the root logger's handlers — including pytest's
# caplog handler, if that import is the first one and happens lazily inside
# a test body (after caplog's fixture setup already attached its handler).
# Import here, once, at collection time, so this always happens before any
# per-test fixture setup and caplog-based assertions stay order-independent.
from fetch_api import main as _fetch_api_main  # noqa: E402,F401

_REQUIREMENTS_TXT = (
    Path(__file__).resolve().parent.parent / "functions" / "fetch_api" / "requirements.txt"
)


class TestRequirements:
    def test_declares_bigquery_dependency(self):
        """fetch_api's _dispatch imports shared.pricing_client, which imports
        google.cloud.bigquery and instantiates a bigquery.Client — without
        this pin, fetch_api breaks at deploy/runtime for ALL its operations,
        not just buybox_status.
        """
        requirements = _REQUIREMENTS_TXT.read_text()
        assert "google-cloud-bigquery" in requirements

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")
os.environ.setdefault("WORKFLOW_NAME", "test-workflow")
os.environ.setdefault("WORKFLOW_LOCATION", "us-central1")


class TestDispatchFinanceTransactions:
    def test_routes_to_finances_client_with_dates(self):
        from fetch_api import main as fa

        op = {"handler": "finance_transactions"}
        with patch.object(fa.finances_client, "fetch_transactions", return_value=[]) as mock:
            fa._dispatch(op, "moxe", "US", date(2026, 8, 1), date(2026, 8, 31), {})

        mock.assert_called_once_with(
            "moxe", "US", date(2026, 8, 1), date(2026, 8, 31), transaction_status=None,
        )

    def test_reads_transaction_status_from_report_options(self):
        from fetch_api import main as fa

        op = {"handler": "finance_transactions"}
        report_params = {"reportOptions": {"transactionStatus": "RELEASED"}}
        with patch.object(fa.finances_client, "fetch_transactions", return_value=[]) as mock:
            fa._dispatch(op, "moxe", "US", date(2026, 8, 1), date(2026, 8, 31), report_params)

        assert mock.call_args.kwargs["transaction_status"] == "RELEASED"

    def test_empty_string_transaction_status_treated_as_none(self):
        from fetch_api import main as fa

        op = {"handler": "finance_transactions"}
        report_params = {"reportOptions": {"transactionStatus": ""}}
        with patch.object(fa.finances_client, "fetch_transactions", return_value=[]) as mock:
            fa._dispatch(op, "moxe", "US", date(2026, 8, 1), date(2026, 8, 31), report_params)

        assert mock.call_args.kwargs["transaction_status"] is None

    def test_unknown_handler_raises(self):
        from fetch_api import main as fa

        with pytest.raises(ValueError, match="nonexistent_handler"):
            fa._dispatch({"handler": "nonexistent_handler"}, "moxe", "US", date(2026, 8, 1), date(2026, 8, 1), {})


class TestDispatchBuyboxStatus:
    def test_routes_to_pricing_client(self):
        from fetch_api import main as fa

        op = {"handler": "buybox_status"}
        with patch.object(fa.pricing_client, "get_active_asins", return_value=["B0ABC12345"]) as mock_asins, \
             patch.object(fa.pricing_client, "fetch_buybox_status", return_value=[{"asin": "B0ABC12345"}]) as mock_check:
            rows = fa._dispatch(op, "itsbodily", "US", date(2026, 9, 11), date(2026, 9, 11), {})

        mock_asins.assert_called_once_with("itsbodily", "US")
        mock_check.assert_called_once_with("itsbodily", "US", ["B0ABC12345"])
        assert rows == [{"asin": "B0ABC12345"}]

    def test_no_active_asins_returns_empty_without_calling_pricing_api(self):
        from fetch_api import main as fa

        op = {"handler": "buybox_status"}
        with patch.object(fa.pricing_client, "get_active_asins", return_value=[]), \
             patch.object(fa.pricing_client, "fetch_buybox_status") as mock_check:
            rows = fa._dispatch(op, "itsbodily", "US", date(2026, 9, 11), date(2026, 9, 11), {})

        mock_check.assert_not_called()
        assert rows == []

    def test_no_active_asins_logs_clear_message(self, caplog):
        """Spec's Error Handling section requires a clear log message when
        zero ASINs come back, distinguishing "client hasn't run
        GET_MERCHANT_LISTINGS_ALL_DATA yet" from a silent no-op.
        """
        import logging

        from fetch_api import main as fa

        op = {"handler": "buybox_status"}
        with caplog.at_level(logging.INFO, logger="fetch_api.main"):
            with patch.object(fa.pricing_client, "get_active_asins", return_value=[]), \
                 patch.object(fa.pricing_client, "fetch_buybox_status") as mock_check:
                fa._dispatch(op, "itsbodily", "US", date(2026, 9, 11), date(2026, 9, 11), {})

        mock_check.assert_not_called()
        matches = [
            r for r in caplog.records
            if "No active listings found" in r.getMessage()
            and "GET_MERCHANT_LISTINGS_ALL_DATA" in r.getMessage()
        ]
        assert len(matches) == 1, [r.getMessage() for r in caplog.records]

    def test_all_rows_erroring_logs_warning(self, caplog):
        """When every row in a run errors out (e.g. auth broken, endpoint
        down for the whole run), a warning must distinguish that from
        "checked fine, nobody's losing their buy box".
        """
        import logging

        from fetch_api import main as fa

        op = {"handler": "buybox_status"}
        error_rows = [
            {"asin": "B0ABC12345", "is_buybox_winner": None, "error": "SP-API 500: boom"},
            {"asin": "B0DEF67890", "is_buybox_winner": None, "error": "SP-API 500: boom"},
        ]
        with caplog.at_level(logging.WARNING, logger="fetch_api.main"):
            with patch.object(fa.pricing_client, "get_active_asins", return_value=["B0ABC12345", "B0DEF67890"]), \
                 patch.object(fa.pricing_client, "fetch_buybox_status", return_value=error_rows):
                rows = fa._dispatch(op, "itsbodily", "US", date(2026, 9, 11), date(2026, 9, 11), {})

        assert rows == error_rows  # visibility only, no behavior change
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
        rec = warnings[0]
        assert rec.client_id == "itsbodily"
        assert rec.marketplace == "US"
        assert rec.row_count == 2

    def test_some_rows_erroring_does_not_log_all_error_warning(self, caplog):
        import logging

        from fetch_api import main as fa

        op = {"handler": "buybox_status"}
        mixed_rows = [
            {"asin": "B0ABC12345", "is_buybox_winner": True, "error": None},
            {"asin": "B0DEF67890", "is_buybox_winner": None, "error": "SP-API 404: Not Found"},
        ]
        with caplog.at_level(logging.WARNING, logger="fetch_api.main"):
            with patch.object(fa.pricing_client, "get_active_asins", return_value=["B0ABC12345", "B0DEF67890"]), \
                 patch.object(fa.pricing_client, "fetch_buybox_status", return_value=mixed_rows):
                rows = fa._dispatch(op, "itsbodily", "US", date(2026, 9, 11), date(2026, 9, 11), {})

        assert rows == mixed_rows
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert warnings == []


class TestDispatchInboundExpiration:
    def test_routes_to_inbound_client_with_start_date(self):
        from fetch_api import main as fa

        op = {"handler": "inbound_expiration"}
        with patch.object(fa.inbound_client, "fetch_inbound_expirations",
                          return_value=[{"msku": "A"}]) as mock_fetch:
            rows = fa._dispatch(op, "itsbodily", "US", date(2026, 11, 1), date(2026, 11, 12), {})

        mock_fetch.assert_called_once_with("itsbodily", "US", date(2026, 11, 1))
        assert rows == [{"msku": "A"}]
