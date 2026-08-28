"""Tests for shared.metrics_repository — Supabase metrics backend."""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

from shared import metrics_repository


class _FakeCursor:
    def __init__(self, results_by_call):
        self._results = results_by_call
        self._call_index = 0
        self.queries: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.queries.append((sql, params))

    def fetchone(self):
        result = self._results[self._call_index]
        self._call_index += 1
        return result


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class TestDayBoundsUtc:
    def test_pacific_full_day(self):
        start, end = metrics_repository._day_bounds_utc("2026-06-04", ZoneInfo("America/Los_Angeles"))
        assert start == datetime(2026, 6, 4, 7, 0, tzinfo=timezone.utc)
        assert end == datetime(2026, 6, 5, 7, 0, tzinfo=timezone.utc)


class TestGetAccountTotals:
    def test_sums_orders_and_ads_from_supabase(self):
        cursor = _FakeCursor(results_by_call=[(1604.29,), (100.18, 347.68)])
        with patch.object(metrics_repository, "_get_connection", return_value=_FakeConnection(cursor)):
            totals = metrics_repository.get_account_totals(
                "c1", ["US"], "2026-06-04", ZoneInfo("America/Los_Angeles"),
            )
        assert totals == {"spend": 100.18, "ppc_sales": 347.68, "total_sales": 1604.29}

    def test_orders_query_filters_by_client_marketplace_and_day_window(self):
        cursor = _FakeCursor(results_by_call=[(0,), (0, 0)])
        with patch.object(metrics_repository, "_get_connection", return_value=_FakeConnection(cursor)):
            metrics_repository.get_account_totals("c1", ["US", "CA"], "2026-06-04", ZoneInfo("America/Los_Angeles"))
        orders_sql, orders_params = cursor.queries[0]
        assert "FROM orders" in orders_sql
        assert orders_params[0] == "c1"
        assert orders_params[1] == ["US", "CA"]

    def test_ads_query_filters_by_client_marketplace_and_date(self):
        cursor = _FakeCursor(results_by_call=[(0,), (0, 0)])
        with patch.object(metrics_repository, "_get_connection", return_value=_FakeConnection(cursor)):
            metrics_repository.get_account_totals("c1", ["US"], "2026-06-04", ZoneInfo("America/Los_Angeles"))
        ads_sql, ads_params = cursor.queries[1]
        assert "FROM ad_campaign_metrics" in ads_sql
        assert ads_params == ("c1", ["US"], date(2026, 6, 4))

    def test_missing_rows_default_to_zero(self):
        cursor = _FakeCursor(results_by_call=[(0,), (0, 0)])
        with patch.object(metrics_repository, "_get_connection", return_value=_FakeConnection(cursor)):
            totals = metrics_repository.get_account_totals("c1", ["US"], "2026-06-04", ZoneInfo("America/Los_Angeles"))
        assert totals == {"spend": 0.0, "ppc_sales": 0.0, "total_sales": 0.0}


class TestGetConnection:
    def test_reads_supabase_db_url_from_env(self, monkeypatch):
        metrics_repository._conn = None
        monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://fake")
        fake_psycopg2 = MagicMock()
        fake_psycopg2.connect.return_value = "fake-connection"
        try:
            with patch.dict(sys.modules, {"psycopg2": fake_psycopg2}):
                conn = metrics_repository._get_connection()
            assert conn == "fake-connection"
            fake_psycopg2.connect.assert_called_once_with("postgresql://fake")
        finally:
            metrics_repository._conn = None

    def test_caches_connection_across_calls(self, monkeypatch):
        metrics_repository._conn = None
        monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://fake")
        fake_psycopg2 = MagicMock()
        fake_psycopg2.connect.return_value = "fake-connection"
        try:
            with patch.dict(sys.modules, {"psycopg2": fake_psycopg2}):
                metrics_repository._get_connection()
                metrics_repository._get_connection()
            fake_psycopg2.connect.assert_called_once()
        finally:
            metrics_repository._conn = None
