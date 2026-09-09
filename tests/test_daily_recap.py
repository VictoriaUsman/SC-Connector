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
# Trigger window: when to send daily recap
# ---------------------------------------------------------------------------

class TestIsDue:
    def test_before_window_not_due(self):
        from daily_recap.main import _is_due

        # Pacific midnight (PDT, UTC-7) on 06/05 is 07:00 UTC. 30 min later
        # is well inside the "not yet" zone (window starts at 1h).
        now = datetime(2026, 6, 5, 7, 30, tzinfo=timezone.utc)
        assert _is_due(now, ZoneInfo("America/Los_Angeles")) is False

    def test_window_start_is_due_inclusive(self):
        from daily_recap.main import _is_due

        # Exactly 1h past Pacific midnight (07:00 UTC -> 08:00 UTC).
        now = datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc)
        assert _is_due(now, ZoneInfo("America/Los_Angeles")) is True

    def test_inside_window_is_due(self):
        from daily_recap.main import _is_due

        # 2h past Pacific midnight.
        now = datetime(2026, 6, 5, 9, 0, tzinfo=timezone.utc)
        assert _is_due(now, ZoneInfo("America/Los_Angeles")) is True

    def test_window_end_not_due_exclusive(self):
        from daily_recap.main import _is_due

        # Exactly 3h past Pacific midnight — window end is exclusive.
        now = datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc)
        assert _is_due(now, ZoneInfo("America/Los_Angeles")) is False

    def test_after_window_not_due(self):
        from daily_recap.main import _is_due

        # 4h past Pacific midnight.
        now = datetime(2026, 6, 5, 11, 0, tzinfo=timezone.utc)
        assert _is_due(now, ZoneInfo("America/Los_Angeles")) is False

    def test_different_timezone_computed_independently(self):
        from daily_recap.main import _is_due

        # Berlin (CEST, UTC+2) midnight on 06/05 is 05/04 22:00 UTC. 2h later
        # is 06/05 00:00 UTC.
        now = datetime(2026, 6, 5, 0, 0, tzinfo=timezone.utc)
        assert _is_due(now, ZoneInfo("Europe/Berlin")) is True

    def test_dst_spring_forward_still_has_a_due_window(self):
        """US DST begins 2026-03-08: clocks skip 2:00 AM -> 3:00 AM Pacific.
        A window based on absolute elapsed time (not wall-clock hour) must
        still produce a due instant that day, even though the wall clock
        never reads some hours at all.
        """
        from daily_recap.main import _is_due

        # Pacific midnight on 2026-03-08 is still PST (UTC-8) -> 08:00 UTC.
        # 2h of *absolute* elapsed time later is 10:00 UTC, which is exactly
        # the DST transition instant (2:00 AM PST becomes 3:00 AM PDT).
        now = datetime(2026, 3, 8, 10, 0, tzinfo=timezone.utc)
        assert _is_due(now, ZoneInfo("America/Los_Angeles")) is True

    def test_dst_spring_forward_window_still_closes(self):
        from daily_recap.main import _is_due

        # 4h absolute elapsed past the same Pacific midnight.
        now = datetime(2026, 3, 8, 12, 0, tzinfo=timezone.utc)
        assert _is_due(now, ZoneInfo("America/Los_Angeles")) is False


class TestHoursSinceLocalMidnight:
    def test_exact_hours(self):
        from daily_recap.main import _hours_since_local_midnight

        now = datetime(2026, 6, 5, 9, 30, tzinfo=timezone.utc)  # 07:00 UTC = Pacific midnight
        hours = _hours_since_local_midnight(now, ZoneInfo("America/Los_Angeles"))
        assert abs(hours - 2.5) < 0.001

    def test_never_negative_at_midnight_itself(self):
        from daily_recap.main import _hours_since_local_midnight

        now = datetime(2026, 6, 5, 7, 0, tzinfo=timezone.utc)  # exactly Pacific midnight
        hours = _hours_since_local_midnight(now, ZoneInfo("America/Los_Angeles"))
        assert abs(hours - 0.0) < 0.001


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

class TestBuildTitleBlock:
    def test_exact_format(self):
        """Title + subtitle, matching the hourly bot's header style."""
        from daily_recap.main import _build_title_block
        from datetime import date

        # 10:00 UTC on 06/05 is 3 AM PDT.
        now = datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc)
        block = _build_title_block("ItsBodily", date(2026, 6, 4), now, ZoneInfo("America/Los_Angeles"))
        assert block["text"]["text"] == ":bar_chart: *Daily Recap — ItsBodily*\n3 AM PDT | 06/04/26"

    def test_no_event_or_day_language(self):
        """daily_recap is year-round, not event-based — no "Day N" / event name."""
        from daily_recap.main import _build_title_block
        from datetime import date

        now = datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc)
        text = _build_title_block("Acme", date(2026, 6, 4), now, ZoneInfo("America/Los_Angeles"))["text"]["text"]
        for token in ("Day ", "Event"):
            assert token not in text


class TestBuildMarketplaceBlock:
    def test_exact_format(self):
        from daily_recap.main import _build_marketplace_block, AccountTotals

        totals = AccountTotals(spend=100.18, ppc_sales=347.68, total_sales=1604.29)
        now = datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc)
        block = _build_marketplace_block("US", totals, "USD", now, ZoneInfo("America/Los_Angeles"))

        expected = (
            "*US*\n"
            "Spend: $100.18\n"
            "PPC Sales: $347.68\n"
            "ACoS: 28.81%\n"
            "Total Sales: $1,604.29\n"
            "TACoS: 6.24%"
        )
        assert block["text"]["text"] == expected

    def test_no_comparison_or_event_language_without_yoy(self):
        from daily_recap.main import _build_marketplace_block, AccountTotals

        totals = AccountTotals(spend=10, ppc_sales=50, total_sales=200)
        now = datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc)
        text = _build_marketplace_block("US", totals, "USD", now, ZoneInfo("America/Los_Angeles"))["text"]["text"]

        for token in ("YoY", "DoD", "WoW", "MoM", "Day ", "Recap", "Event", "vs"):
            assert token not in text

    def test_non_usd_currency_symbol(self):
        from daily_recap.main import _build_marketplace_block, AccountTotals

        totals = AccountTotals(spend=6.03, ppc_sales=213.32, total_sales=255.98)
        now = datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc)
        text = _build_marketplace_block("UK", totals, "GBP", now, ZoneInfo("Europe/London"))["text"]["text"]
        assert "£6.03" in text

    def test_marketplace_local_time_shown_when_it_differs_from_client_tz(self):
        """A marketplace whose timezone differs from the client's own gets its
        own local time annotated next to the header, matching the hourly bot."""
        from daily_recap.main import _build_marketplace_block, AccountTotals

        totals = AccountTotals(spend=1, ppc_sales=2, total_sales=3)
        now = datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc)  # 3 AM PDT / 11 AM BST
        text = _build_marketplace_block("UK", totals, "GBP", now, ZoneInfo("America/Los_Angeles"))["text"]["text"]
        assert text.startswith("*UK* (11 AM BST)\n")

    def test_no_marketplace_local_time_when_it_matches_client_tz(self):
        from daily_recap.main import _build_marketplace_block, AccountTotals

        totals = AccountTotals(spend=1, ppc_sales=2, total_sales=3)
        now = datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc)
        text = _build_marketplace_block("US", totals, "USD", now, ZoneInfo("America/Los_Angeles"))["text"]["text"]
        assert text.startswith("*US*\n")


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
            patch("daily_recap.main._is_due", return_value=True),
            patch("daily_recap.main.has_bot_activity", return_value=False),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(100, 400, 1000)),
            patch("daily_recap.main._query_prior_year_totals", return_value=None),
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
            patch("daily_recap.main._is_due", return_value=True),
            patch("daily_recap.main.has_bot_activity", return_value=False),
            patch("daily_recap.main._query_account_totals", return_value=totals) as mock_query,
            patch("daily_recap.main._query_prior_year_totals", return_value=None),
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
        title_text = blocks[0]["text"]["text"]
        assert title_text.startswith(":bar_chart: *Daily Recap — Acme*\n")
        assert "06/04/26" in title_text

        mkt_text = blocks[1]["text"]["text"]
        assert mkt_text.startswith("*US*\n")
        assert "Spend: $100.18" in mkt_text
        assert "ACoS: 28.81%" in mkt_text
        assert "TACoS: 6.24%" in mkt_text

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
            patch("daily_recap.main._is_due", return_value=True),
            patch("daily_recap.main.has_bot_activity", return_value=False),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(1, 4, 10)),
            patch("daily_recap.main._query_prior_year_totals", return_value=None),
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
            patch("daily_recap.main._is_due", return_value=True),
            patch("daily_recap.main.has_bot_activity", return_value=False),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(1, 4, 10)),
            patch("daily_recap.main._query_prior_year_totals", return_value=None),
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
            patch("daily_recap.main._is_due", return_value=True),
            patch("daily_recap.main.has_bot_activity", return_value=False),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(1, 4, 10)),
            patch("daily_recap.main._query_prior_year_totals", return_value=None),
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
            patch("daily_recap.main._is_due", return_value=True),
            patch("daily_recap.main.has_bot_activity", return_value=False),
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
            patch("daily_recap.main._is_due", return_value=True),
            patch("daily_recap.main.has_bot_activity", return_value=False),
            patch("daily_recap.main._query_account_totals", return_value=totals) as mock_query,
            patch("daily_recap.main._query_prior_year_totals", return_value=None),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity") as mock_log,
        ):
            mock_dt.now.return_value = now
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 1
        # The metric query is pinned to yesterday — no date slipping.
        assert mock_query.call_args[0][2] == "2026-06-13"
        blocks = mock_post.call_args[0][1]
        assert "06/13/26" in blocks[0]["text"]["text"]
        assert "Total Sales: $1,604.29" in blocks[1]["text"]["text"]

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
            patch("daily_recap.main._is_due", return_value=True),
            patch("daily_recap.main.has_bot_activity", return_value=False),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(1, 4, 10)) as mock_query,
            patch("daily_recap.main._query_prior_year_totals", return_value=None),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity"),
        ):
            mock_dt.now.return_value = now
            handler(_make_request())

        assert mock_query.call_args[0][2] == "2026-06-04"
        assert "06/04/26" in mock_post.call_args[0][1][0]["text"]["text"]


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
            patch("daily_recap.main._is_due", return_value=True),
            patch("daily_recap.main.has_bot_activity", return_value=False),
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
        blocks = mock_post.call_args[0][1]
        assert "07/01/26" in blocks[0]["text"]["text"]
        mkt_text = blocks[1]["text"]["text"]
        assert mkt_text.startswith("*AU*\n")
        assert "Spend: A$100.18" in mkt_text
        assert "PPC Sales: A$347.68" in mkt_text
        assert "Total Sales: A$1,604.29" in mkt_text
        assert "ACoS: 28.81%" in mkt_text
        assert "TACoS: 6.24%" in mkt_text

        log_data = mock_log.call_args[0][0]
        assert log_data["status"] == "sent"
        assert log_data["marketplaces_reported"] == ["AU"]
        assert log_data["recap_date"] == "2026-07-01"


# ---------------------------------------------------------------------------
# YoY suffix on Spend / PPC Sales / ACoS only
# ---------------------------------------------------------------------------


class TestYoySuffix:
    def test_no_yoy_by_default(self):
        """Omitting yoy produces the plain (no-comparison) message."""
        from daily_recap.main import _build_marketplace_block, AccountTotals

        totals = AccountTotals(spend=100.18, ppc_sales=347.68, total_sales=1604.29)
        now = datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc)
        text = _build_marketplace_block("US", totals, "USD", now, ZoneInfo("America/Los_Angeles"))["text"]["text"]
        assert "YoY" not in text

    def test_yoy_added_to_spend_ppc_acos_only(self):
        from daily_recap.main import _build_marketplace_block, AccountTotals

        totals = AccountTotals(spend=932.53, ppc_sales=4052.57, total_sales=7556.51)
        yoy = AccountTotals(spend=1313.88, ppc_sales=5330.00, total_sales=0.0)
        now = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
        text = _build_marketplace_block(
            "US", totals, "USD", now, ZoneInfo("America/Los_Angeles"), yoy=yoy,
        )["text"]["text"]

        assert "Spend: $932.53 _(YoY: $1,313.88 [-29%])_" in text
        assert "PPC Sales: $4,052.57 _(YoY: $5,330.00 [-24%])_" in text
        assert "ACoS: 23.01% _(YoY: 24.65%" in text
        # Total Sales / TACoS never get a YoY suffix — no baseline metric exists.
        assert text.endswith("Total Sales: $7,556.51\nTACoS: 12.34%")
        assert "Total Sales: $7,556.51 _(YoY" not in text
        assert "TACoS: 12.34% _(YoY" not in text

    def test_yoy_omitted_when_no_prior_year_data(self):
        from daily_recap.main import _build_marketplace_block, AccountTotals

        totals = AccountTotals(spend=10, ppc_sales=50, total_sales=200)
        now = datetime(2026, 6, 5, 10, 0, tzinfo=timezone.utc)
        text = _build_marketplace_block(
            "US", totals, "USD", now, ZoneInfo("America/Los_Angeles"), yoy=None,
        )["text"]["text"]
        assert "YoY" not in text


# ---------------------------------------------------------------------------
# Prior-year reference lookup (scripts/load_prior_year_reference.py's table)
# ---------------------------------------------------------------------------


class TestQueryPriorYearTotals:
    def test_returns_totals_when_row_exists(self, monkeypatch):
        from daily_recap.main import _query_prior_year_totals
        from datetime import date

        monkeypatch.setenv("GCP_PROJECT", "proj")
        monkeypatch.setenv("BQ_DATASET", "ds")
        fake = _FakeBQ([("ads_prior_year_reference", [{"spend": 1313.88, "ppc_sales": 5330.00}])])

        with patch("daily_recap.main._get_bq", return_value=fake):
            result = _query_prior_year_totals("itsbodily", "US", date(2026, 9, 7))

        assert result.spend == 1313.88
        assert result.ppc_sales == 5330.00
        assert result.total_sales == 0.0  # never a real baseline for this metric

        sql, job_config = fake.calls[0]
        params = _params(job_config)
        # Queries the *prior* year's same calendar date.
        assert params["date"] == date(2025, 9, 7)
        assert params["client_id"] == "itsbodily"
        assert params["marketplace"] == "US"

    def test_returns_none_when_no_row(self):
        from daily_recap.main import _query_prior_year_totals
        from datetime import date

        fake = _FakeBQ([])  # no matching rows for any query
        with patch("daily_recap.main._get_bq", return_value=fake):
            result = _query_prior_year_totals("itsbodily", "US", date(2026, 9, 7))
        assert result is None

    def test_returns_none_on_query_failure_not_raises(self):
        """A client with no reference data loaded is the normal case, not an
        error — the recap must still send without YoY rather than blow up."""
        from daily_recap.main import _query_prior_year_totals
        from datetime import date

        class _Boom:
            def query(self, *a, **kw):
                raise RuntimeError("no such table")

        with patch("daily_recap.main._get_bq", return_value=_Boom()):
            result = _query_prior_year_totals("itsbodily", "US", date(2026, 9, 7))
        assert result is None

    def test_returns_none_for_feb_29_leap_day(self):
        """Recapping Feb 29 has no same-calendar-date last year in a non-leap year."""
        from daily_recap.main import _query_prior_year_totals
        from datetime import date

        result = _query_prior_year_totals("itsbodily", "US", date(2028, 2, 29))
        assert result is None


# ---------------------------------------------------------------------------
# Multi-marketplace: per-marketplace blocks + conditional Total
# ---------------------------------------------------------------------------


class TestMultiMarketplaceRecap:
    def test_single_marketplace_has_header_but_no_total(self):
        """A single-marketplace client still gets its own marketplace header
        (matching the hourly bot, which always shows one) but no Total block —
        a Total across one marketplace would just repeat its own numbers."""
        from daily_recap.main import handler, AccountTotals

        with (
            patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config(marketplaces=["US"])]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main._is_due", return_value=True),
            patch("daily_recap.main.has_bot_activity", return_value=False),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(100, 400, 1000)),
            patch("daily_recap.main._query_prior_year_totals", return_value=None),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity"),
        ):
            handler(_make_request())

        blocks = mock_post.call_args[0][1]
        # Title block + one marketplace block — no Total, no divider.
        assert len(blocks) == 2
        assert blocks[0]["text"]["text"].startswith(":bar_chart: *Daily Recap — Acme*\n")
        assert blocks[1]["text"]["text"].startswith("*US*\n")
        assert "*Total*" not in blocks[1]["text"]["text"]
        assert {"type": "divider"} not in blocks

    def test_multi_marketplace_gets_header_per_marketplace_and_total(self):
        from daily_recap.main import handler, AccountTotals

        totals_by_mkt = {
            "US": AccountTotals(100, 400, 1000),
            "CA": AccountTotals(50, 200, 500),
        }

        with (
            patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config(marketplaces=["US", "CA"])]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main._is_due", return_value=True),
            patch("daily_recap.main.has_bot_activity", return_value=False),
            patch("daily_recap.main._query_account_totals", side_effect=lambda cid, mkts, *a: totals_by_mkt[mkts[0]]),
            patch("daily_recap.main._query_prior_year_totals", return_value=None),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity"),
        ):
            handler(_make_request())

        blocks = mock_post.call_args[0][1]
        full_text = "\n".join(b.get("text", {}).get("text", "") for b in blocks)

        assert "*US*" in full_text
        assert "*CA*" in full_text
        assert "*Total*" in full_text
        # Total sums both marketplaces: spend 100+50, ppc 400+200, total_sales 1000+500.
        assert "Spend: $150.00" in full_text
        assert "PPC Sales: $600.00" in full_text
        assert "Total Sales: $1,500.00" in full_text
        # A divider separates the per-marketplace blocks from the Total block.
        assert {"type": "divider"} in blocks

    def test_total_yoy_only_when_every_marketplace_has_a_baseline(self):
        from daily_recap.main import handler, AccountTotals

        totals_by_mkt = {
            "US": AccountTotals(100, 400, 1000),
            "CA": AccountTotals(50, 200, 500),
        }
        yoy_by_mkt = {
            "US": AccountTotals(80, 300, 0),
            "CA": None,  # CA has no prior-year reference data loaded
        }

        with (
            patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config(marketplaces=["US", "CA"])]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main._is_due", return_value=True),
            patch("daily_recap.main.has_bot_activity", return_value=False),
            patch("daily_recap.main._query_account_totals", side_effect=lambda cid, mkts, *a: totals_by_mkt[mkts[0]]),
            patch("daily_recap.main._query_prior_year_totals", side_effect=lambda cid, mkt, *a: yoy_by_mkt[mkt]),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity"),
        ):
            handler(_make_request())

        blocks = mock_post.call_args[0][1]
        full_text = "\n".join(b.get("text", {}).get("text", "") for b in blocks)

        # US alone has a baseline, so its own line gets a YoY suffix...
        assert "Spend: $100.00 _(YoY: $80.00" in full_text
        # ...but the combined Total does not, since CA has none.
        total_block = next(
            b["text"]["text"] for b in blocks if b.get("text", {}).get("text", "").startswith("*Total*")
        )
        assert "YoY" not in total_block

    def test_total_yoy_shown_when_all_marketplaces_have_a_baseline(self):
        from daily_recap.main import handler, AccountTotals

        totals_by_mkt = {
            "US": AccountTotals(100, 400, 1000),
            "CA": AccountTotals(50, 200, 500),
        }
        yoy_by_mkt = {
            "US": AccountTotals(80, 300, 0),
            "CA": AccountTotals(40, 150, 0),
        }

        with (
            patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config(marketplaces=["US", "CA"])]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main._is_due", return_value=True),
            patch("daily_recap.main.has_bot_activity", return_value=False),
            patch("daily_recap.main._query_account_totals", side_effect=lambda cid, mkts, *a: totals_by_mkt[mkts[0]]),
            patch("daily_recap.main._query_prior_year_totals", side_effect=lambda cid, mkt, *a: yoy_by_mkt[mkt]),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity"),
        ):
            handler(_make_request())

        blocks = mock_post.call_args[0][1]
        total_block = next(
            b["text"]["text"] for b in blocks if b.get("text", {}).get("text", "").startswith("*Total*")
        )
        # Combined Total: spend 150 vs YoY 120, ppc 600 vs YoY 450.
        assert "Spend: $150.00 _(YoY: $120.00" in total_block
        assert "PPC Sales: $600.00 _(YoY: $450.00" in total_block


class TestTriggerGating:
    def test_client_not_due_yet_is_skipped(self):
        """Outside the 1-3h post-local-midnight window: no query, no send."""
        from daily_recap.main import handler, AccountTotals

        # 30 min past Pacific midnight — before the window opens.
        now = datetime(2026, 6, 5, 7, 30, tzinfo=timezone.utc)

        with (
            patch("daily_recap.main.datetime") as mock_dt,
            patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main._query_account_totals") as mock_query,
            patch("daily_recap.main.has_bot_activity") as mock_has_activity,
            patch("daily_recap.main.post_message") as mock_post,
        ):
            mock_dt.now.return_value = now
            body, status = handler(_make_request())

        assert body["messages_sent"] == 0
        mock_query.assert_not_called()
        mock_post.assert_not_called()
        # Not-due is decided before ever checking activity history.
        mock_has_activity.assert_not_called()

    def test_client_due_but_already_sent_is_skipped(self):
        from daily_recap.main import handler, AccountTotals

        # 2h past Pacific midnight — inside the window.
        now = datetime(2026, 6, 5, 9, 0, tzinfo=timezone.utc)

        with (
            patch("daily_recap.main.datetime") as mock_dt,
            patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main.has_bot_activity", return_value=True) as mock_has_activity,
            patch("daily_recap.main._query_account_totals") as mock_query,
            patch("daily_recap.main.post_message") as mock_post,
        ):
            mock_dt.now.return_value = now
            body, status = handler(_make_request())

        assert body["messages_sent"] == 0
        mock_query.assert_not_called()
        mock_post.assert_not_called()
        mock_has_activity.assert_called_once_with("daily_recap", "c1", "2026-06-04")

    def test_client_due_and_not_yet_sent_proceeds(self):
        from daily_recap.main import handler, AccountTotals

        now = datetime(2026, 6, 5, 9, 0, tzinfo=timezone.utc)

        with (
            patch("daily_recap.main.datetime") as mock_dt,
            patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main.has_bot_activity", return_value=False),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(100, 400, 1000)),
            patch("daily_recap.main._query_prior_year_totals", return_value=None),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity"),
        ):
            mock_dt.now.return_value = now
            body, status = handler(_make_request())

        assert body["messages_sent"] == 1
        mock_post.assert_called_once()

    def test_mixed_clients_only_due_one_sends(self):
        """Two clients in the same invocation, in different timezones — only
        the one whose local time is inside its own window sends."""
        from daily_recap.main import handler, AccountTotals

        due_config = _make_bot_config(client_id="due-client")
        due_config["client_timezone"] = "America/Los_Angeles"
        due_config["channels"] = [{"id": "C_DUE"}]
        del due_config["slack_channel_id"]

        not_due_config = _make_bot_config(client_id="not-due-client")
        not_due_config["client_timezone"] = "Europe/Berlin"
        not_due_config["channels"] = [{"id": "C_NOT_DUE"}]
        del not_due_config["slack_channel_id"]

        # 2h past Pacific midnight (due) — Berlin midnight was ~11h ago (not due).
        now = datetime(2026, 6, 5, 9, 0, tzinfo=timezone.utc)

        with (
            patch("daily_recap.main.datetime") as mock_dt,
            patch("daily_recap.main.list_bot_configs", return_value=[due_config, not_due_config]),
            patch("daily_recap.main.get_client", return_value={"id": "x", "name": "X", "is_active": True}),
            patch("daily_recap.main.has_bot_activity", return_value=False),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(100, 400, 1000)),
            patch("daily_recap.main._query_prior_year_totals", return_value=None),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
            patch("daily_recap.main.log_bot_activity"),
        ):
            mock_dt.now.return_value = now
            body, status = handler(_make_request())

        assert body["messages_sent"] == 1
        mock_post.assert_called_once()
        assert mock_post.call_args[0][0] == "C_DUE"


class TestActivityRoundTrip:
    def test_second_handler_call_sees_first_calls_activity_and_skips(self):
        """Proves the real contract between log_bot_activity (write) and
        has_bot_activity (read) — not two independently-mocked halves that
        happen to agree. A real in-memory list stands in for the activity
        store; the patched functions mirror the real write/read shapes so a
        second handler() invocation for the same due window only sends once."""
        from daily_recap.main import handler, AccountTotals

        activity_log: list[dict] = []

        def fake_log_bot_activity(data: dict) -> None:
            activity_log.append({
                "bot": data["bot"],
                "client_id": data["client_id"],
                "recap_date": data["recap_date"],
                "status": data["status"],
            })

        def fake_has_bot_activity(bot: str, client_id: str, recap_date: str) -> bool:
            return any(
                a["bot"] == bot
                and a["client_id"] == client_id
                and a["recap_date"] == recap_date
                and a["status"] == "sent"
                for a in activity_log
            )

        # 2h past Pacific midnight — inside the trigger window.
        now = datetime(2026, 6, 5, 9, 0, tzinfo=timezone.utc)

        with (
            patch("daily_recap.main.datetime") as mock_dt,
            patch("daily_recap.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("daily_recap.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("daily_recap.main.log_bot_activity", side_effect=fake_log_bot_activity),
            patch("daily_recap.main.has_bot_activity", side_effect=fake_has_bot_activity),
            patch("daily_recap.main._query_account_totals", return_value=AccountTotals(100, 400, 1000)),
            patch("daily_recap.main._query_prior_year_totals", return_value=None),
            patch("daily_recap.main.post_message", return_value={"ok": True, "ts": "1.2"}) as mock_post,
        ):
            mock_dt.now.return_value = now

            first_body, _ = handler(_make_request())
            second_body, _ = handler(_make_request())

        assert first_body["messages_sent"] == 1
        assert second_body["messages_sent"] == 0
        mock_post.assert_called_once()


# ---------------------------------------------------------------------------
# _maybe_add_total_block unit tests (no handler/Slack involved)
# ---------------------------------------------------------------------------


class TestMaybeAddTotalBlock:
    def test_noop_for_single_marketplace(self):
        from daily_recap.main import _maybe_add_total_block, AccountTotals

        blocks: list[dict] = []
        _maybe_add_total_block(blocks, {"US": AccountTotals(1, 2, 3)}, {"US": None}, "USD")
        assert blocks == []

    def test_noop_for_zero_marketplaces(self):
        from daily_recap.main import _maybe_add_total_block

        blocks: list[dict] = []
        _maybe_add_total_block(blocks, {}, {}, "USD")
        assert blocks == []
