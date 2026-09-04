"""Tests for the daily recap bot — gating, metric derivation, and message format."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

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
            patch("daily_recap.main._query_account_totals", return_value=totals) as mock_query,
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity") as mock_log,
        ):
            mock_dt.now.return_value = now
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 1

        # The recap queries the previous full calendar day in the client's
        # timezone (10:00/23:00 UTC on 06/05 is still 06/05 in Pacific -> 06/04).
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
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(1, 4, 10)),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity"),
        ):
            handler(_make_request())

        assert mock_post.call_args[0][0] == "CTEST"

    def test_tags_configured_channel_users(self):
        """A channel with tag_user_ids gets a leading mention block so those
        people are notified regardless of their own channel settings."""
        from daily_recap.main import handler, AccountTotals

        config = _make_bot_config()
        del config["slack_channel_id"]
        config["channels"] = [{"id": "C123", "tag_user_ids": ["U111", "U222"]}]

        with (
            patch("daily_recap.main.list_bot_configs", return_value=[config]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(1, 4, 10)),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity"),
        ):
            handler(_make_request())

        blocks = mock_post.call_args[0][1]
        assert blocks[0]["text"]["text"] == "<@U111> <@U222>"

    def test_no_tag_block_when_channel_has_no_tag_user_ids(self):
        from daily_recap.main import handler, AccountTotals

        with (
            patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(1, 4, 10)),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity"),
        ):
            handler(_make_request())

        blocks = mock_post.call_args[0][1]
        assert "<@" not in blocks[0]["text"]["text"]

    def test_logs_failure(self):
        from daily_recap.main import handler

        with (
            patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
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


class TestQueryAccountTotalsBackendDispatch:
    def test_supabase_backend_delegates_to_metrics_repository(self, monkeypatch):
        from daily_recap.main import AccountTotals, _query_account_totals

        monkeypatch.setenv("METRICS_BACKEND", "supabase")
        try:
            with patch(
                "daily_recap.main.metrics_repository.get_account_totals",
                return_value={"spend": 1.0, "ppc_sales": 2.0, "total_sales": 3.0},
            ) as mock_get:
                totals = _query_account_totals("c1", ["US"], "2026-06-04", ZoneInfo("America/Los_Angeles"))
            assert totals == AccountTotals(spend=1.0, ppc_sales=2.0, total_sales=3.0)
            mock_get.assert_called_once_with("c1", ["US"], "2026-06-04", ZoneInfo("America/Los_Angeles"))
        finally:
            monkeypatch.delenv("METRICS_BACKEND", raising=False)

    def test_default_backend_still_uses_bigquery(self, monkeypatch):
        from daily_recap.main import _query_account_totals

        monkeypatch.delenv("METRICS_BACKEND", raising=False)
        with (
            patch("daily_recap.main._get_bq", return_value=MagicMock()) as mock_bq,
            patch("daily_recap.main._query_orders_total", return_value={"total_sales": 5.0}),
            patch("daily_recap.main._query_ads_total", return_value={"spend": 1.0, "ppc_sales": 2.0}),
        ):
            totals = _query_account_totals("c1", ["US"], "2026-06-04", ZoneInfo("America/Los_Angeles"))
        assert totals.total_sales == 5.0
        mock_bq.assert_called_once()


class TestQueryConstruction:
    def test_orders_query_uses_purchase_date_window_not_report_partition(self):
        from daily_recap.main import _query_orders_total

        fake = _FakeBQ([("`proj.ds.orders_latest`", [{"total_sales": 1604.29}])])
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
            ("`proj.ds.orders_latest`", [{"total_sales": 1604.29}]),
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

        orders_sql = next(c[0] for c in fake.calls if "`proj.ds.orders_latest`" in c[0])
        ads_sql = next(c[0] for c in fake.calls if "UNION ALL" in c[0])
        assert "purchase_date" in orders_sql
        assert "ROW_NUMBER()" in ads_sql


# ---------------------------------------------------------------------------
# Recap day (regression: recap reported $0 Total Sales because it ran before
# the day's orders report had ingested, then a prior fix slid the reported day
# back to whichever day the ads tables — which ingest earlier — had data for)
# ---------------------------------------------------------------------------


class TestRecapDayIsAlwaysPreviousCalendarDay:
    def test_queries_and_labels_yesterday_regardless_of_data_availability(self):
        """The recap always reports the previous full calendar day.

        The buggy behavior keyed the recap day off the freshest *ingested* day,
        which was driven by the ads tables (ingested before the orders report).
        That day's orders were therefore still missing at query time, so Total
        Sales read $0 while Spend/PPC/ACoS were correct. The recap must instead
        always query yesterday — and is scheduled to run after the day's reports
        (orders included) have ingested.
        """
        from daily_recap.main import handler, AccountTotals

        totals = AccountTotals(spend=121.49, ppc_sales=483.43, total_sales=1604.29)
        now = datetime(2026, 6, 14, 23, 0, tzinfo=timezone.utc)  # -> 06-13 Pacific

        with (
            patch("daily_recap.main.datetime") as mock_dt,
            patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main._query_account_totals", return_value=totals) as mock_query,
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity") as mock_log,
        ):
            mock_dt.now.return_value = now
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 1
        # The metric query is pinned to yesterday — no date slipping.
        assert mock_query.call_args[0][2] == "2026-06-13"
        text = mock_post.call_args[0][1][0]["text"]["text"]
        assert text.startswith("06/13/26\n")
        assert "• Total Sales: $1,604.29" in text

        # The activity log records the recap (data) day.
        log_data = mock_log.call_args[0][0]
        assert log_data["recap_date"] == "2026-06-13"

    def test_resolver_indirection_is_gone(self):
        """The date-slipping resolver was removed (it caused the wrong day)."""
        import daily_recap.main as m

        assert not hasattr(m, "_resolve_effective_date")

    def test_timezone_rollover_singapore(self):
        from daily_recap.main import handler, AccountTotals

        # 02:00 UTC on 06/05 is already 06/05 in Singapore -> yesterday is 06/04.
        now = datetime(2026, 6, 5, 2, 0, tzinfo=timezone.utc)
        config = _make_bot_config()
        config["client_timezone"] = "Asia/Singapore"

        with (
            patch("daily_recap.main.datetime") as mock_dt,
            patch("daily_recap.main.list_bot_configs", return_value=[config]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(1, 4, 10)) as mock_query,
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity"),
        ):
            mock_dt.now.return_value = now
            handler(_make_request())

        assert mock_query.call_args[0][2] == "2026-06-04"
        assert mock_post.call_args[0][1][0]["text"]["text"].startswith("06/04/26\n")


# ---------------------------------------------------------------------------
# AU (Amazon Australia) account — end-to-end daily recap (CU-868k6gq75)
# ---------------------------------------------------------------------------


class TestAuAccountEndToEnd:
    """An AU account produces and delivers a daily recap like US/UK/EU accounts.

    AU is region ``fe`` and uses AUD + Australia/Sydney. This drives the real
    query-construction and currency-formatting paths (only BigQuery and Slack
    transport are faked) to prove the recap generates and delivers for AU.
    """

    def test_recap_generated_and_delivered_for_au_account(self):
        from daily_recap.main import handler

        config = _make_bot_config(client_id="acme-au", marketplaces=["AU"])
        config["base_currency"] = "AUD"
        config["client_timezone"] = "Australia/Sydney"

        # 01:00 UTC on 07/02 is 11:00 AEST (UTC+10) on 07/02 -> previous day 07/01.
        # A datetime subclass freezes now() while still constructing real
        # datetimes (the real _day_bounds_utc path builds the AU day window).
        now = datetime(2026, 7, 2, 1, 0, tzinfo=timezone.utc)

        class _FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz) if tz else now

        fake = _FakeBQ([
            ("`proj.ds.orders_latest`", [{"total_sales": 1604.29}]),
            ("UNION ALL", [{"spend": 100.18, "ppc_sales": 347.68}]),
        ])

        with (
            patch("daily_recap.main.datetime", _FrozenDateTime),
            patch("daily_recap.main.list_bot_configs", return_value=[config]),
            patch("daily_recap.main.get_client", return_value={"id": "acme-au", "name": "Acme AU", "is_active": True}),
            patch("daily_recap.main._get_bq", return_value=fake),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity") as mock_log,
            patch.dict(os.environ, {"GCP_PROJECT": "proj", "BQ_DATASET": "ds"}),
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 1

        # The recap targets yesterday in Sydney and scopes BigQuery to AU.
        orders_call = next(c for c in fake.calls if "`proj.ds.orders_latest`" in c[0])
        assert _params(orders_call[1])["marketplaces"] == ["AU"]
        ads_call = next(c for c in fake.calls if "UNION ALL" in c[0])
        assert _params(ads_call[1])["marketplaces"] == ["AU"]

        # Delivered message: Sydney recap date + AUD (A$) currency symbol.
        text = mock_post.call_args[0][1][0]["text"]["text"]
        assert text.startswith("07/01/26\n")
        assert "• Spend: A$100.18" in text
        assert "• PPC Sales: A$347.68" in text
        assert "• Total Sales: A$1,604.29" in text
        assert "• ACoS: 28.81%" in text
        assert "• TACoS: 6.24%" in text

        log_data = mock_log.call_args[0][0]
        assert log_data["status"] == "sent"
        assert log_data["marketplaces_reported"] == ["AU"]
        assert log_data["recap_date"] == "2026-07-01"
