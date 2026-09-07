"""Tests for fetch_api's operation dispatch (mode="api_call" routing).

Narrow coverage: just the new SP_FINANCE_TRANSACTIONS dispatch branch, since
the reportOptions.transactionStatus extraction is easy to get wrong silently
(a typo'd key just means "fetch everything" instead of erroring).
"""

from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

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
