"""Tests for the daily recap bot — gating, metric derivation, and message format."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

import shared.firestore_utils  # noqa: F401 — register module before patch()

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")
os.environ.setdefault("BQ_DATASET", "kalilos_reports_staging")


def _make_request() -> MagicMock:
    req = MagicMock()
    req.get_json.return_value = {}
    return req


def _make_bot_config(
    client_id: str = "c1",
    marketplaces: list[str] | None = None,
    daily_recap_enabled: bool = True,
    hourly_enabled: bool = False,
) -> dict:
    return {
        "id": client_id,
        "client_id": client_id,
        "hourly_bot": {"enabled": hourly_enabled},
        "daily_recap_enabled": daily_recap_enabled,
        "marketplaces": marketplaces or ["US"],
        "slack_channel_id": "C123",
        "client_timezone": "America/Los_Angeles",
        "base_currency": "USD",
        "use_test_channel": False,
    }


@pytest.fixture(autouse=True)
def _mock_firestore():
    with patch("shared.firestore_utils.firestore.Client"):
        yield


# ---------------------------------------------------------------------------
# AccountTotals derivation
# ---------------------------------------------------------------------------

class TestAccountTotals:
    def test_acos_and_tacos(self):
        from daily_recap.main import AccountTotals

        t = AccountTotals(spend=100.18, ppc_sales=347.68, total_sales=1604.29)
        assert abs(t.acos - 28.81) < 0.01
        assert abs(t.tacos - 6.24) < 0.01

    def test_zero_division(self):
        from daily_recap.main import AccountTotals

        t = AccountTotals(spend=0, ppc_sales=0, total_sales=0)
        assert t.acos == 0.0
        assert t.tacos == 0.0


# ---------------------------------------------------------------------------
# Previous calendar day in client timezone
# ---------------------------------------------------------------------------

class TestPreviousCalendarDay:
    def test_pacific_previous_day(self):
        from daily_recap.main import _previous_calendar_day

        # 10:00 UTC on 06/05 is 03:00 PDT on 06/05 -> previous full day is 06/04
        now = datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc)
        assert _previous_calendar_day(now, ZoneInfo("America/Los_Angeles")).isoformat() == "2026-06-04"

    def test_timezone_affects_rollover(self):
        from daily_recap.main import _previous_calendar_day

        # 02:00 UTC on 06/05 is still 06/04 in Pacific -> previous day is 06/03
        now = datetime(2026, 6, 5, 2, 0, tzinfo=timezone.utc)
        assert _previous_calendar_day(now, ZoneInfo("America/Los_Angeles")).isoformat() == "2026-06-03"
        # ...but already 06/05 in Singapore -> previous day is 06/04
        assert _previous_calendar_day(now, ZoneInfo("Asia/Singapore")).isoformat() == "2026-06-04"


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

class TestBuildRecapBlocks:
    def test_exact_format(self):
        from daily_recap.main import _build_recap_blocks, AccountTotals
        from datetime import date

        totals = AccountTotals(spend=100.18, ppc_sales=347.68, total_sales=1604.29)
        blocks = _build_recap_blocks(date(2026, 6, 4), totals, "USD")

        assert len(blocks) == 1
        text = blocks[0]["text"]["text"]
        expected = (
            "06/04/26\n"
            "• Spend: $100.18\n"
            "• PPC Sales: $347.68\n"
            "• ACoS: 28.81%\n"
            "• Total Sales: $1,604.29\n"
            "• TACoS: 6.24%"
        )
        assert text == expected

    def test_no_comparison_or_event_language(self):
        from daily_recap.main import _build_recap_blocks, AccountTotals
        from datetime import date

        totals = AccountTotals(spend=10, ppc_sales=50, total_sales=200)
        text = _build_recap_blocks(date(2026, 6, 4), totals, "USD")[0]["text"]["text"]

        for token in ("YoY", "DoD", "WoW", "MoM", "Day ", "Recap", "Event", "vs"):
            assert token not in text

    def test_non_usd_currency_symbol(self):
        from daily_recap.main import _build_recap_blocks, AccountTotals
        from datetime import date

        totals = AccountTotals(spend=6.03, ppc_sales=213.32, total_sales=255.98)
        text = _build_recap_blocks(date(2026, 6, 4), totals, "GBP")[0]["text"]["text"]
        assert "£6.03" in text


# ---------------------------------------------------------------------------
# Handler gating + delivery
# ---------------------------------------------------------------------------

class TestHandlerGating:
    def test_no_enabled_configs(self):
        from daily_recap.main import handler

        with patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config(daily_recap_enabled=False)]):
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 0

    def test_disabled_client_gets_nothing(self):
        from daily_recap.main import handler

        with (
            patch("daily_recap.main.list_bot_configs", return_value=[
                _make_bot_config(client_id="off", daily_recap_enabled=False),
            ]),
            patch("daily_recap.main.get_client", return_value={"id": "off", "name": "Off", "is_active": True}),
            patch("daily_recap.main._query_account_totals") as mock_query,
            patch("daily_recap.main.post_message") as mock_post,
        ):
            body, status = handler(_make_request())

        assert body["messages_sent"] == 0
        mock_query.assert_not_called()
        mock_post.assert_not_called()

    def test_independent_of_hourly_toggle(self):
        """daily_recap fires for a client even when the hourly bot is disabled."""
        from daily_recap.main import handler, AccountTotals

        with (
            patch("daily_recap.main.list_bot_configs", return_value=[
                _make_bot_config(daily_recap_enabled=True, hourly_enabled=False),
            ]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main._resolve_effective_date", return_value="2026-06-04"),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(100, 400, 1000)),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity"),
        ):
            body, status = handler(_make_request())

        assert body["messages_sent"] == 1
        mock_post.assert_called_once()


class TestHandlerDelivery:
    def test_posts_recap_for_enabled_client(self):
        from daily_recap.main import handler, AccountTotals

        totals = AccountTotals(spend=100.18, ppc_sales=347.68, total_sales=1604.29)
        now = datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc)

        with (
            patch("daily_recap.main.datetime") as mock_dt,
            patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main._resolve_effective_date", return_value="2026-06-04") as mock_resolve,
            patch("daily_recap.main._query_account_totals", return_value=totals) as mock_query,
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity") as mock_log,
        ):
            mock_dt.now.return_value = now
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 1

        # Resolution targets the previous full calendar day in the client's timezone.
        assert mock_resolve.call_args[0][2] == "2026-06-04"
        # The resolved effective date drives the metric query.
        assert mock_query.call_args[0][2] == "2026-06-04"

        blocks = mock_post.call_args[0][1]
        text = blocks[0]["text"]["text"]
        assert text.startswith("06/04/26\n")
        assert "• Spend: $100.18" in text
        assert "• ACoS: 28.81%" in text
        assert "• TACoS: 6.24%" in text

        log_data = mock_log.call_args[0][0]
        assert log_data["status"] == "sent"
        assert log_data["bot"] == "daily_recap"

    def test_uses_test_channel_when_configured(self):
        from daily_recap.main import handler, AccountTotals

        config = _make_bot_config()
        config["use_test_channel"] = True
        config["test_channel_id"] = "CTEST"

        with (
            patch("daily_recap.main.list_bot_configs", return_value=[config]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main._resolve_effective_date", return_value="2026-06-04"),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(1, 4, 10)),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity"),
        ):
            handler(_make_request())

        assert mock_post.call_args[0][0] == "CTEST"

    def test_logs_failure(self):
        from daily_recap.main import handler

        with (
            patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main._resolve_effective_date", return_value="2026-06-04"),
            patch("daily_recap.main._query_account_totals", side_effect=RuntimeError("BQ down")),
            patch("daily_recap.main.log_bot_activity") as mock_log,
        ):
            body, status = handler(_make_request())

        assert body["errors"] == 1
        log_data = mock_log.call_args[0][0]
        assert log_data["status"] == "failed"


# ---------------------------------------------------------------------------
# BigQuery query construction (regression: recap showed all-zero values)
# ---------------------------------------------------------------------------

class _FakeBQ:
    """Captures issued queries and returns canned rows matched by SQL substring."""

    def __init__(self, rows_by_substring: list[tuple[str, list[dict]]]):
        self.calls: list[tuple[str, object]] = []
        self._rows_by_substring = rows_by_substring

    def query(self, sql, job_config=None):
        self.calls.append((sql, job_config))
        for sub, rows in self._rows_by_substring:
            if sub in sql:
                return rows
        return []


def _params(job_config) -> dict:
    out = {}
    for p in job_config.query_parameters:
        out[p.name] = getattr(p, "value", None) if hasattr(p, "value") else getattr(p, "values", None)
    return out


class TestDayBounds:
    def test_pacific_daylight_bounds(self):
        from daily_recap.main import _day_bounds_utc

        # 06/04 is PDT (UTC-7): local midnight -> 07:00 UTC, next midnight -> 07:00 UTC.
        start, end = _day_bounds_utc("2026-06-04", ZoneInfo("America/Los_Angeles"))
        assert start == "2026-06-04T07:00:00Z"
        assert end == "2026-06-05T07:00:00Z"

    def test_utc_bounds(self):
        from daily_recap.main import _day_bounds_utc

        start, end = _day_bounds_utc("2026-06-04", ZoneInfo("UTC"))
        assert start == "2026-06-04T00:00:00Z"
        assert end == "2026-06-05T00:00:00Z"


class TestQueryConstruction:
    def test_orders_query_uses_purchase_date_window_not_report_partition(self):
        from daily_recap.main import _query_orders_total

        fake = _FakeBQ([("`proj.ds.orders`", [{"total_sales": 1604.29}])])
        result = _query_orders_total(
            fake, "proj", "ds", "c1", ["US"],
            "2026-06-04T07:00:00Z", "2026-06-05T07:00:00Z",
        )

        assert result["total_sales"] == 1604.29
        sql, job_config = fake.calls[0]
        assert "purchase_date >= @day_start" in sql
        assert "purchase_date < @day_end" in sql
        # The ingestion report_date partition is the range start for multi-day
        # schedules, so the recap must NOT filter on it.
        assert "report_date" not in sql
        # BigQuery parses the TIMESTAMP string into an aware datetime.
        params = _params(job_config)
        assert params["day_start"] == datetime(2026, 6, 4, 7, 0, tzinfo=timezone.utc)
        assert params["day_end"] == datetime(2026, 6, 5, 7, 0, tzinfo=timezone.utc)

    def test_ads_query_filters_on_data_date_and_dedups(self):
        from daily_recap.main import _query_ads_total

        fake = _FakeBQ([("UNION ALL", [{"spend": 100.18, "ppc_sales": 347.68}])])
        result = _query_ads_total(fake, "proj", "ds", "c1", ["US"], "2026-06-04")

        assert result["spend"] == 100.18
        assert result["ppc_sales"] == 347.68
        sql, job_config = fake.calls[0]
        # Keyed on the campaign performance date, not the ingestion partition.
        assert "date = @report_date" in sql
        assert "report_date = " not in sql
        # Most-recent value per bucket (post-restatement) — guards double counting
        # when the same data date is re-pulled under several overlapping ranges.
        assert "ROW_NUMBER()" in sql
        assert "ORDER BY ingested_at DESC" in sql
        for table in ("sp_campaigns", "sb_campaigns", "sd_campaigns"):
            assert table in sql

    def test_account_totals_wires_orders_and_ads_by_data_date(self):
        from daily_recap.main import _query_account_totals

        fake = _FakeBQ([
            ("`proj.ds.orders`", [{"total_sales": 1604.29}]),
            ("UNION ALL", [{"spend": 100.18, "ppc_sales": 347.68}]),
        ])
        with (
            patch("daily_recap.main._get_bq", return_value=fake),
            patch.dict(os.environ, {"GCP_PROJECT": "proj", "BQ_DATASET": "ds"}),
        ):
            totals = _query_account_totals(
                "c1", ["US"], "2026-06-04", ZoneInfo("America/Los_Angeles"),
            )

        assert abs(totals.total_sales - 1604.29) < 0.01
        assert abs(totals.spend - 100.18) < 0.01
        assert abs(totals.ppc_sales - 347.68) < 0.01

        orders_sql = next(c[0] for c in fake.calls if "`proj.ds.orders`" in c[0])
        ads_sql = next(c[0] for c in fake.calls if "UNION ALL" in c[0])
        assert "purchase_date" in orders_sql
        assert "ROW_NUMBER()" in ads_sql


# ---------------------------------------------------------------------------
# Effective-date resolution (regression: recap showed 0 when yesterday's data
# had not been ingested yet due to Amazon reporting / schedule latency)
# ---------------------------------------------------------------------------

import datetime as _dt  # noqa: E402


class TestResolveEffectiveDate:
    def test_falls_back_to_latest_available_day(self):
        """When yesterday has no rows, resolve the most recent day that does."""
        from daily_recap.main import _resolve_effective_date

        # The recap targets 2026-06-08, but the freshest ingested data is 06-07.
        fake = _FakeBQ([("data_date", [{"data_date": _dt.date(2026, 6, 7)}])])
        with (
            patch("daily_recap.main._get_bq", return_value=fake),
            patch.dict(os.environ, {"GCP_PROJECT": "proj", "BQ_DATASET": "ds"}),
        ):
            resolved = _resolve_effective_date(
                "c1", ["US"], "2026-06-08", ZoneInfo("America/Los_Angeles"),
            )

        assert resolved == "2026-06-07"
        sql, job_config = fake.calls[0]
        # Resolution keys on the actual data date, never the ingestion partition.
        assert "MAX(date)" in sql
        assert "report_date" not in sql
        # Bounded lookback below the target day, capped at the target day.
        # BigQuery coerces DATE params to datetime.date.
        params = _params(job_config)
        assert params["target_date"] == _dt.date(2026, 6, 8)
        assert params["lookback_start"] == _dt.date(2026, 5, 25)  # 14 days before target
        # Considers ads campaign tables and orders purchase dates.
        for table in ("sp_campaigns", "sb_campaigns", "sd_campaigns", "orders"):
            assert table in sql

    def test_returns_target_when_yesterday_has_data(self):
        from daily_recap.main import _resolve_effective_date

        fake = _FakeBQ([("data_date", [{"data_date": _dt.date(2026, 6, 8)}])])
        with (
            patch("daily_recap.main._get_bq", return_value=fake),
            patch.dict(os.environ, {"GCP_PROJECT": "proj", "BQ_DATASET": "ds"}),
        ):
            resolved = _resolve_effective_date(
                "c1", ["US"], "2026-06-08", ZoneInfo("America/Los_Angeles"),
            )

        assert resolved == "2026-06-08"

    def test_returns_target_when_no_data_in_window(self):
        """A genuinely dark account still legitimately reports zeros for the day."""
        from daily_recap.main import _resolve_effective_date

        fake = _FakeBQ([("data_date", [{"data_date": None}])])
        with (
            patch("daily_recap.main._get_bq", return_value=fake),
            patch.dict(os.environ, {"GCP_PROJECT": "proj", "BQ_DATASET": "ds"}),
        ):
            resolved = _resolve_effective_date(
                "c1", ["US"], "2026-06-08", ZoneInfo("America/Los_Angeles"),
            )

        assert resolved == "2026-06-08"


class TestHandlerUsesEffectiveDate:
    def test_recap_labels_and_queries_latest_available_day(self):
        """End to end: a lagging client recaps its freshest day, not an empty one."""
        from daily_recap.main import handler, AccountTotals

        totals = AccountTotals(spend=12.34, ppc_sales=56.78, total_sales=0.0)
        now = datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc)  # -> targets 06-08

        with (
            patch("daily_recap.main.datetime") as mock_dt,
            patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            # Yesterday (06-08) has no data yet; latest available is 06-07.
            patch("daily_recap.main._resolve_effective_date", return_value="2026-06-07") as mock_resolve,
            patch("daily_recap.main._query_account_totals", return_value=totals) as mock_query,
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity") as mock_log,
        ):
            mock_dt.now.return_value = now
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 1
        # Resolution targeted yesterday...
        assert mock_resolve.call_args[0][2] == "2026-06-08"
        # ...but metrics + the date line use the resolved (freshest) day.
        assert mock_query.call_args[0][2] == "2026-06-07"
        text = mock_post.call_args[0][1][0]["text"]["text"]
        assert text.startswith("06/07/26\n")
        assert "• Spend: $12.34" in text

        log_data = mock_log.call_args[0][0]
        assert log_data["recap_date"] == "2026-06-07"
        assert log_data["target_date"] == "2026-06-08"
