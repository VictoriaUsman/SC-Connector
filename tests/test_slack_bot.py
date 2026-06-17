"""Tests for the hourly Slack bot function — metrics, message formatting, and delivery."""

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
    enabled: bool = True,
) -> dict:
    return {
        "id": client_id,
        "client_id": client_id,
        "hourly_bot": {"enabled": enabled},
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
# Handler no-op scenarios
# ---------------------------------------------------------------------------

class TestHandlerNoOp:
    def test_no_live_event(self):
        from slack_bot.main import handler

        with patch("slack_bot.main.get_live_event", return_value=None):
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 0

    def test_no_enabled_configs(self):
        from slack_bot.main import handler

        with (
            patch("slack_bot.main.get_live_event", return_value={"id": "e1", "name": "Test", "start_date": "2026-07-13"}),
            patch("slack_bot.main.list_bot_configs", return_value=[_make_bot_config(enabled=False)]),
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 0


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------

class TestBuildMessageBlocks:
    def test_single_marketplace(self):
        from slack_bot.main import _build_message_blocks, MarketplaceMetrics

        metrics = [
            MarketplaceMetrics(
                marketplace="US", currency="USD",
                total_sales=39650.42, units=500, spend=1720.88, ppc_sales=6244.40,
            ),
        ]
        now = datetime(2026, 7, 13, 18, 45, tzinfo=timezone.utc)
        blocks = _build_message_blocks(
            client_name="Acme",
            event_name="Prime Day 2026",
            day_index=1,
            now=now,
            client_tz=ZoneInfo("America/Los_Angeles"),
            metrics=metrics,
            base_currency="USD",
        )

        assert len(blocks) >= 2
        header = blocks[0]["text"]["text"]
        assert "Acme" in header
        assert "Prime Day 2026" in header
        assert "Day 1" in header

        us_block = blocks[1]["text"]["text"]
        assert "*US*" in us_block
        assert "$1,720.88" in us_block
        assert "$6,244.40" in us_block
        assert "$39,650.42" in us_block

    def test_total_row_single_currency(self):
        from slack_bot.main import _build_message_blocks, MarketplaceMetrics

        metrics = [
            MarketplaceMetrics(marketplace="US", currency="USD", total_sales=1000, units=10, spend=100, ppc_sales=500),
            MarketplaceMetrics(marketplace="CA", currency="USD", total_sales=500, units=5, spend=50, ppc_sales=200),
        ]
        now = datetime(2026, 7, 13, 18, 45, tzinfo=timezone.utc)
        blocks = _build_message_blocks(
            client_name="Acme",
            event_name="Test",
            day_index=1,
            now=now,
            client_tz=ZoneInfo("America/Los_Angeles"),
            metrics=metrics,
            base_currency="USD",
        )

        all_text = " ".join(b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section")
        assert "*Total*" in all_text

    def test_no_total_row_multi_currency(self):
        from slack_bot.main import _build_message_blocks, MarketplaceMetrics

        metrics = [
            MarketplaceMetrics(marketplace="US", currency="USD", total_sales=1000, units=10, spend=100, ppc_sales=500),
            MarketplaceMetrics(marketplace="UK", currency="GBP", total_sales=500, units=5, spend=50, ppc_sales=200),
        ]
        now = datetime(2026, 7, 13, 18, 45, tzinfo=timezone.utc)
        blocks = _build_message_blocks(
            client_name="Acme",
            event_name="Test",
            day_index=1,
            now=now,
            client_tz=ZoneInfo("America/Los_Angeles"),
            metrics=metrics,
            base_currency="USD",
        )

        all_text = " ".join(b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section")
        assert "*Total*" not in all_text


# ---------------------------------------------------------------------------
# Metrics dataclass
# ---------------------------------------------------------------------------

class TestMarketplaceMetrics:
    def test_acos_calculation(self):
        from slack_bot.main import MarketplaceMetrics

        m = MarketplaceMetrics(marketplace="US", currency="USD", total_sales=10000, units=100, spend=2000, ppc_sales=8000)
        assert abs(m.acos - 25.0) < 0.01

    def test_tacos_calculation(self):
        from slack_bot.main import MarketplaceMetrics

        m = MarketplaceMetrics(marketplace="US", currency="USD", total_sales=10000, units=100, spend=2000, ppc_sales=8000)
        assert abs(m.tacos - 20.0) < 0.01

    def test_zero_division_handling(self):
        from slack_bot.main import MarketplaceMetrics

        m = MarketplaceMetrics(marketplace="US", currency="USD", total_sales=0, units=0, spend=0, ppc_sales=0)
        assert m.acos == 0.0
        assert m.tacos == 0.0


# ---------------------------------------------------------------------------
# Day index computation
# ---------------------------------------------------------------------------

class TestComputeDayIndex:
    _pst = ZoneInfo("America/Los_Angeles")

    def test_day_one(self):
        from slack_bot.main import _compute_day_index

        assert _compute_day_index("2026-07-13", datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc), self._pst) == 1

    def test_day_two(self):
        from slack_bot.main import _compute_day_index

        assert _compute_day_index("2026-07-13", datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc), self._pst) == 2

    def test_invalid_date(self):
        from slack_bot.main import _compute_day_index

        assert _compute_day_index("invalid", datetime(2026, 7, 13, 18, 0, tzinfo=timezone.utc), self._pst) == 0

    def test_no_premature_rollover_at_midnight_utc(self):
        """At midnight UTC (5 PM PDT), Day N should NOT increment for Pacific clients."""
        from slack_bot.main import _compute_day_index

        midnight_utc = datetime(2026, 7, 14, 0, 45, tzinfo=timezone.utc)
        assert _compute_day_index("2026-07-13", midnight_utc, self._pst) == 1


class TestMidnightRecap:
    _pst = ZoneInfo("America/Los_Angeles")

    def test_midnight_slot_detected(self):
        from slack_bot.main import _is_midnight_recap_slot

        now = datetime(2026, 7, 14, 7, 45, tzinfo=timezone.utc)  # 12:45 AM PDT
        assert _is_midnight_recap_slot(now, self._pst) is True

    def test_non_midnight_slot(self):
        from slack_bot.main import _is_midnight_recap_slot

        now = datetime(2026, 7, 14, 18, 45, tzinfo=timezone.utc)  # 11:45 AM PDT
        assert _is_midnight_recap_slot(now, self._pst) is False

    def test_report_date_for_event_day(self):
        from slack_bot.main import _report_date_for_event_day

        assert _report_date_for_event_day("2026-07-13", 1, self._pst) == "2026-07-13"
        assert _report_date_for_event_day("2026-07-13", 2, self._pst) == "2026-07-14"


class TestBuildRecapBlocks:
    def test_day_one_recap_label_and_yoy_na(self):
        from slack_bot.main import _build_recap_blocks, MarketplaceMetrics

        metrics = [
            MarketplaceMetrics(
                marketplace="US", currency="USD",
                total_sales=10000, units=100, spend=2000, ppc_sales=8000,
            ),
        ]
        now = datetime(2026, 7, 14, 7, 45, tzinfo=timezone.utc)
        blocks = _build_recap_blocks(
            client_name="Acme",
            event_name="Prime Day 2026",
            recap_day=1,
            now=now,
            client_tz=ZoneInfo("America/Los_Angeles"),
            metrics=metrics,
            prior_single=None,
            prior_cumulative=None,
            current_cumulative=None,
            base_currency="USD",
        )

        header = blocks[0]["text"]["text"]
        assert "Day 1 Recap" in header
        assert "Day 2 Recap" not in header
        us_block = blocks[1]["text"]["text"]
        assert "YoY: —" in us_block
        assert "*Cumulative*" not in " ".join(
            b.get("text", {}).get("text", "") for b in blocks
        )

    def test_day_two_recap_includes_cumulative_yoy(self):
        from slack_bot.main import _build_recap_blocks, MarketplaceMetrics

        metrics = [
            MarketplaceMetrics(
                marketplace="US", currency="USD",
                total_sales=5000, units=50, spend=1000, ppc_sales=4000,
            ),
        ]
        cumulative = [
            MarketplaceMetrics(
                marketplace="US", currency="USD",
                total_sales=15000, units=150, spend=3000, ppc_sales=12000,
            ),
        ]
        prior_single = [
            MarketplaceMetrics(
                marketplace="US", currency="USD",
                total_sales=4000, units=40, spend=800, ppc_sales=3200,
            ),
        ]
        prior_cumulative = [
            MarketplaceMetrics(
                marketplace="US", currency="USD",
                total_sales=9000, units=90, spend=1800, ppc_sales=7200,
            ),
        ]
        now = datetime(2026, 7, 15, 7, 45, tzinfo=timezone.utc)
        blocks = _build_recap_blocks(
            client_name="Acme",
            event_name="Prime Day 2026",
            recap_day=2,
            now=now,
            client_tz=ZoneInfo("America/Los_Angeles"),
            metrics=metrics,
            prior_single=prior_single,
            prior_cumulative=prior_cumulative,
            current_cumulative=cumulative,
            base_currency="USD",
        )

        all_text = " ".join(b.get("text", {}).get("text", "") for b in blocks)
        assert "Day 2 Recap" in all_text
        assert "Cumulative (Days 1–2)" in all_text
        assert "YoY: $800.00" in all_text or "YoY: $800" in all_text

    def test_yoy_suffix_with_prior_values(self):
        from slack_bot.main import _format_yoy_suffix

        assert "—" in _format_yoy_suffix(100, None, "USD")
        assert "[+25%]" in _format_yoy_suffix(100, 80, "USD")


class TestQueryOrders:
    """Recap Total Sales bug: the full-day query must bound by the purchase-date
    window, not sum a whole ``report_date`` partition (which spans many purchase
    days and roughly doubled Total Sales)."""

    @staticmethod
    def _fake_bq(captured: dict) -> MagicMock:
        def fake_query(query, job_config=None):
            captured["query"] = query
            captured["params"] = {
                p.name: p.value for p in job_config.query_parameters
            }
            return iter([{"total_sales": 1234.56, "units": 12}])

        bq = MagicMock()
        bq.query.side_effect = fake_query
        return bq

    def test_full_day_uses_purchase_date_window_not_report_date(self):
        from slack_bot.main import _query_orders

        captured: dict = {}
        bq = self._fake_bq(captured)

        result = _query_orders(
            bq, "proj", "ds", "c1", "US", "2026-07-13",
            datetime(2026, 7, 14, 7, 45, tzinfo=timezone.utc),
            full_day=True,
        )

        assert result == {"total_sales": 1234.56, "units": 12}
        query = captured["query"]
        # The fix: bound the completed day by its purchase-date window...
        assert "purchase_date >= @day_start" in query
        assert "purchase_date < @day_end" in query
        # ...and never filter the recap by the ingestion report_date partition,
        # which caused the ~2x Total Sales overcount.
        assert "report_date" not in query

        params = captured["params"]
        # US -> America/Los_Angeles; 2026-07-13 is PDT (UTC-7): 00:00 local = 07:00Z.
        # BigQuery parses the TIMESTAMP param string into a datetime.
        assert params["day_start"] == datetime(2026, 7, 13, 7, 0, tzinfo=timezone.utc)
        assert params["day_end"] == datetime(2026, 7, 14, 7, 0, tzinfo=timezone.utc)

    def test_hourly_bounds_current_day_by_purchase_date_not_report_date(self):
        """Hourly Total Sales freeze bug: the hourly query must bound the current
        day purely by its purchase-date window. Filtering on the ingestion
        ``report_date`` partition pinned Total Sales to the first run's pull
        while ads metrics kept refreshing, so PPC Sales could exceed Total
        Sales."""
        from slack_bot.main import _query_orders

        captured: dict = {}
        bq = self._fake_bq(captured)

        _query_orders(
            bq, "proj", "ds", "c1", "US", "2026-07-13",
            datetime(2026, 7, 13, 23, 45, tzinfo=timezone.utc),
            full_day=False,
        )

        query = captured["query"]
        assert "purchase_date >= @mkt_midnight" in query
        # The fix: bound the top of the current day too, and never pin to the
        # ingestion report_date partition (the source of the freeze).
        assert "purchase_date < @mkt_next_midnight" in query
        assert "report_date" not in query

        params = captured["params"]
        # US -> America/Los_Angeles; 2026-07-13 is PDT (UTC-7): 00:00 local = 07:00Z,
        # next midnight 2026-07-14 00:00 local = 2026-07-14 07:00Z.
        assert params["mkt_midnight"] == datetime(2026, 7, 13, 7, 0, tzinfo=timezone.utc)
        assert params["mkt_next_midnight"] == datetime(2026, 7, 14, 7, 0, tzinfo=timezone.utc)


class TestQueryAds:
    """Recap/hourly ads must key off each campaign row's actual performance
    ``date``, not the ingestion ``report_date`` partition. The linked prior-year
    event is backfilled as one multi-day pull, so its rows carry a range-start
    ``report_date`` that differs from the data date — filtering on ``report_date``
    matched nothing and rendered last year's Spend/PPC/ACoS as 0 (the reported
    bug)."""

    @staticmethod
    def _fake_bq(captured: dict) -> MagicMock:
        def fake_query(query, job_config=None):
            captured["query"] = query
            captured["params"] = {p.name: p.value for p in job_config.query_parameters}
            return iter([{"spend": 663.41, "ppc_sales": 2075.92}])

        bq = MagicMock()
        bq.query.side_effect = fake_query
        return bq

    def test_filters_by_performance_date_not_report_date(self):
        from slack_bot.main import _query_ads

        captured: dict = {}
        bq = self._fake_bq(captured)

        result = _query_ads(bq, "proj", "ds", "c1", "US", "2025-06-09")

        assert result == {"spend": 663.41, "ppc_sales": 2075.92}
        query = captured["query"]
        # The fix: bound by the campaign performance `date`...
        assert "date = @perf_date" in query
        # ...never the ingestion report_date partition (the prior-year-shows-0 bug).
        assert "report_date" not in query
        # ...and dedup overlapping re-pulls by most-recent ingestion.
        assert "ROW_NUMBER()" in query
        assert "ingested_at DESC" in query

        params = captured["params"]
        # BigQuery parses the DATE param string into a date object.
        assert str(params["perf_date"]) == "2025-06-09"
        assert params["marketplace"] == "US"
        assert params["client_id"] == "c1"


class TestTotalSalesInvariant:
    """Total Sales >= PPC Sales must hold for every hourly row: total ordered
    sales include ad-attributed sales, so PPC Sales can never exceed them."""

    def test_passes_when_total_ge_ppc(self):
        from slack_bot.main import MarketplaceMetrics, _check_total_sales_invariant

        metrics = [
            MarketplaceMetrics(marketplace="US", currency="USD", total_sales=1000, units=10, spend=100, ppc_sales=500),
            MarketplaceMetrics(marketplace="CA", currency="CAD", total_sales=500, units=5, spend=50, ppc_sales=500),
        ]
        # Should not raise (equality is allowed).
        _check_total_sales_invariant(metrics)

    def test_raises_when_ppc_exceeds_total(self):
        from slack_bot.main import (
            MarketplaceMetrics,
            TotalSalesInvariantError,
            _check_total_sales_invariant,
        )

        metrics = [
            MarketplaceMetrics(marketplace="US", currency="USD", total_sales=100, units=1, spend=50, ppc_sales=300),
        ]
        with pytest.raises(TotalSalesInvariantError) as exc_info:
            _check_total_sales_invariant(metrics)
        assert "US" in str(exc_info.value)

    def test_brook_whittle_6am_regression(self):
        """Fixture from the ticket: Brook Whittle 6 AM had PPC Sales $293.79
        against a frozen Total Sales of $90.69 — impossible. The invariant must
        catch it. (Fails before the fix added the assertion; passes after.)"""
        from slack_bot.main import (
            MarketplaceMetrics,
            TotalSalesInvariantError,
            _check_total_sales_invariant,
        )

        metrics = [
            MarketplaceMetrics(
                marketplace="US", currency="USD",
                total_sales=90.69, units=3, spend=102.57, ppc_sales=293.79,
            ),
        ]
        with pytest.raises(TotalSalesInvariantError) as exc_info:
            _check_total_sales_invariant(metrics)
        msg = str(exc_info.value)
        assert "293.79" in msg
        assert "90.69" in msg

    def test_tolerance_allows_subcent_rounding(self):
        from slack_bot.main import MarketplaceMetrics, _check_total_sales_invariant

        metrics = [
            MarketplaceMetrics(marketplace="US", currency="USD", total_sales=100.0, units=1, spend=10, ppc_sales=100.004),
        ]
        # Within half-cent rounding tolerance — must not raise.
        _check_total_sales_invariant(metrics)


# ---------------------------------------------------------------------------
# Full handler integration
# ---------------------------------------------------------------------------

class TestHandlerIntegration:
    def test_sends_message_on_active_event(self):
        from slack_bot.main import handler

        with (
            patch("slack_bot.main.get_live_event", return_value={
                "id": "e1", "name": "Prime Day", "start_date": "2026-07-13",
            }),
            patch("slack_bot.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("slack_bot.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("slack_bot.main._query_metrics", return_value=[]),
            patch("slack_bot.main.get_thread_anchor_ts", return_value="999.000"),
            patch("slack_bot.main.set_thread_anchor_ts"),
            patch("slack_bot.main.post_message", return_value={"ok": True, "ts": "123.456"}) as mock_post,
            patch("slack_bot.main.log_bot_activity"),
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 1
        # Anchor already exists, so only the threaded update is posted.
        mock_post.assert_called_once()
        assert mock_post.call_args.kwargs.get("thread_ts") == "999.000"

    def test_midnight_slot_posts_recap_not_hourly(self):
        from slack_bot.main import handler, MarketplaceMetrics

        metrics = [
            MarketplaceMetrics(
                marketplace="US", currency="USD",
                total_sales=1000, units=10, spend=100, ppc_sales=500,
            ),
        ]
        midnight_pst = datetime(2026, 7, 14, 7, 45, tzinfo=timezone.utc)  # 12:45 AM PDT, Day 2

        with (
            patch("slack_bot.main.datetime") as mock_dt_cls,
            patch("slack_bot.main.get_live_event", return_value={
                "id": "e1",
                "name": "Prime Day",
                "start_date": "2026-07-13",
                "prior_event_id": "prior_e1",
            }),
            patch("slack_bot.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("slack_bot.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("slack_bot.main._query_metrics", return_value=metrics) as mock_query,
            patch("slack_bot.main._fetch_prior_yoy_metrics", return_value=(None, None)),
            patch("slack_bot.main._query_cumulative_metrics", return_value=None),
            patch("slack_bot.main.get_thread_anchor_ts", return_value="999.000"),
            patch("slack_bot.main.set_thread_anchor_ts"),
            patch("slack_bot.main.post_message", return_value={"ok": True, "ts": "123.456"}) as mock_post,
            patch("slack_bot.main.log_bot_activity"),
        ):
            mock_dt_cls.now.return_value = midnight_pst
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 1
        mock_query.assert_called_once()
        call_kwargs = mock_query.call_args[1]
        assert call_kwargs.get("report_date") == "2026-07-13"
        assert call_kwargs.get("full_day") is True
        # Anchor already exists, so the recap is the only post and is threaded.
        fallback = mock_post.call_args[0][2]
        assert "Day 1 Recap" in fallback
        blocks = mock_post.call_args[0][1]
        assert "Day 1 Recap" in blocks[0]["text"]["text"]
        assert mock_post.call_args.kwargs.get("thread_ts") == "999.000"

    def test_invariant_violation_skips_post_as_warning(self):
        """When Total Sales < PPC Sales, the row's orders pull has not ingested
        yet — skip the post (recorded as an invariant skip / WARNING, not a
        paging ERROR) instead of posting a misleading update."""
        from slack_bot.main import handler, MarketplaceMetrics

        bad_metrics = [
            MarketplaceMetrics(
                marketplace="US", currency="USD",
                total_sales=90.69, units=3, spend=102.57, ppc_sales=293.79,
            ),
        ]

        with (
            patch("slack_bot.main.get_live_event", return_value={
                "id": "e1", "name": "Prime Day", "start_date": "2026-07-13",
            }),
            patch("slack_bot.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("slack_bot.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("slack_bot.main._query_metrics", return_value=bad_metrics),
            patch("slack_bot.main.post_message") as mock_post,
            patch("slack_bot.main.log_bot_activity") as mock_log,
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 0
        # Invariant violations are skips, not genuine errors (no ERROR/page).
        assert body["errors"] == 0
        assert body["invariant_skips"] == 1
        mock_post.assert_not_called()
        log_data = mock_log.call_args[0][0]
        assert log_data["status"] == "failed"
        assert "invariant" in log_data["error"].lower()

    def test_logs_failure(self):
        from slack_bot.main import handler

        with (
            patch("slack_bot.main.get_live_event", return_value={
                "id": "e1", "name": "Test", "start_date": "2026-07-13",
            }),
            patch("slack_bot.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("slack_bot.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("slack_bot.main._query_metrics", side_effect=RuntimeError("BQ down")),
            patch("slack_bot.main.log_bot_activity") as mock_log,
        ):
            body, status = handler(_make_request())

        assert body["errors"] == 1
        log_data = mock_log.call_args[0][0]
        assert log_data["status"] == "failed"


# ---------------------------------------------------------------------------
# Per-day thread anchor (daily parent message + threaded hourly replies)
# ---------------------------------------------------------------------------

class TestBuildDayAnchorBlocks:
    def test_thin_anchor_text_and_fallback(self):
        from datetime import date

        from slack_bot.main import _build_day_anchor_blocks

        blocks, fallback = _build_day_anchor_blocks("Prime Day", 2, date(2026, 6, 22))

        assert len(blocks) == 1
        text = blocks[0]["text"]["text"]
        assert ":bar_chart:" in text
        assert "Prime Day" in text
        assert "Day 2" in text
        assert "Jun 22" in text
        assert fallback == "Prime Day — Day 2, Jun 22"

    def test_anchor_carries_no_metrics(self):
        """The anchor is a thin parent — never any spend/sales lines."""
        from datetime import date

        from slack_bot.main import _build_day_anchor_blocks

        blocks, _ = _build_day_anchor_blocks("Prime Day", 1, date(2026, 6, 21))
        text = blocks[0]["text"]["text"]
        assert "Spend" not in text
        assert "PPC Sales" not in text
        assert "Total Sales" not in text


class TestEnsureDayAnchor:
    def test_creates_parent_when_absent_and_persists_ts(self):
        from datetime import date

        from slack_bot.main import _ensure_day_anchor

        with (
            patch("slack_bot.main.get_thread_anchor_ts", return_value=None),
            patch("slack_bot.main.set_thread_anchor_ts") as mock_set,
            patch("slack_bot.main.post_message", return_value={"ok": True, "ts": "PARENT.1"}) as mock_post,
        ):
            ts = _ensure_day_anchor(
                channel_id="C1",
                event_id="e1",
                client_id="c1",
                event_name="Prime Day",
                event_date=date(2026, 6, 21),
                day_index=1,
            )

        assert ts == "PARENT.1"
        # Parent posted top-level (no thread_ts).
        mock_post.assert_called_once()
        assert "thread_ts" not in mock_post.call_args.kwargs
        # Stored keyed on (event, client, channel, local date).
        mock_set.assert_called_once_with("e1", "c1", "C1", "2026-06-21", "PARENT.1")

    def test_reuses_existing_parent_without_posting(self):
        from datetime import date

        from slack_bot.main import _ensure_day_anchor

        with (
            patch("slack_bot.main.get_thread_anchor_ts", return_value="PARENT.EXISTING"),
            patch("slack_bot.main.set_thread_anchor_ts") as mock_set,
            patch("slack_bot.main.post_message") as mock_post,
        ):
            ts = _ensure_day_anchor(
                channel_id="C1",
                event_id="e1",
                client_id="c1",
                event_name="Prime Day",
                event_date=date(2026, 6, 22),
                day_index=2,
            )

        assert ts == "PARENT.EXISTING"
        mock_post.assert_not_called()
        mock_set.assert_not_called()


class TestThreadedHourlyDelivery:
    def test_first_update_creates_parent_then_threads_reply(self):
        """First hourly update of a new event-day: one top-level parent anchor,
        then the update posted as a threaded reply under it."""
        from slack_bot.main import handler, MarketplaceMetrics

        metrics = [
            MarketplaceMetrics(
                marketplace="US", currency="USD",
                total_sales=1000, units=10, spend=100, ppc_sales=500,
            ),
        ]

        with (
            patch("slack_bot.main.get_live_event", return_value={
                "id": "e1", "name": "Prime Day", "start_date": "2026-06-21",
            }),
            patch("slack_bot.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("slack_bot.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("slack_bot.main._query_metrics", return_value=metrics),
            patch("slack_bot.main.get_thread_anchor_ts", return_value=None),
            patch("slack_bot.main.set_thread_anchor_ts") as mock_set,
            patch(
                "slack_bot.main.post_message",
                side_effect=[{"ok": True, "ts": "PARENT.1"}, {"ok": True, "ts": "REPLY.1"}],
            ) as mock_post,
            patch("slack_bot.main.log_bot_activity") as mock_log,
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 1
        # Two posts: the parent anchor, then the threaded update.
        assert mock_post.call_count == 2
        parent_call, reply_call = mock_post.call_args_list
        assert "thread_ts" not in parent_call.kwargs  # parent is top-level
        assert reply_call.kwargs.get("thread_ts") == "PARENT.1"  # update threaded
        mock_set.assert_called_once()
        assert mock_log.call_args[0][0]["parent_ts"] == "PARENT.1"

    def test_midday_restart_reuses_stored_parent(self):
        """A mid-day restart finds the stored parent ts and threads under it
        instead of creating a duplicate parent."""
        from slack_bot.main import handler, MarketplaceMetrics

        metrics = [
            MarketplaceMetrics(
                marketplace="US", currency="USD",
                total_sales=1000, units=10, spend=100, ppc_sales=500,
            ),
        ]

        with (
            patch("slack_bot.main.get_live_event", return_value={
                "id": "e1", "name": "Prime Day", "start_date": "2026-06-21",
            }),
            patch("slack_bot.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("slack_bot.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("slack_bot.main._query_metrics", return_value=metrics),
            patch("slack_bot.main.get_thread_anchor_ts", return_value="PARENT.EXISTING"),
            patch("slack_bot.main.set_thread_anchor_ts") as mock_set,
            patch("slack_bot.main.post_message", return_value={"ok": True, "ts": "REPLY.1"}) as mock_post,
            patch("slack_bot.main.log_bot_activity"),
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 1
        # No new parent created — only the threaded reply is posted.
        mock_post.assert_called_once()
        assert mock_post.call_args.kwargs.get("thread_ts") == "PARENT.EXISTING"
        mock_set.assert_not_called()

    def test_day_rollover_keys_anchor_on_new_local_date(self):
        """When the local date rolls over, the anchor lookup uses the new date,
        so a new parent thread is created for the new event-day."""
        from datetime import datetime as real_datetime

        from slack_bot.main import handler, MarketplaceMetrics

        metrics = [
            MarketplaceMetrics(
                marketplace="US", currency="USD",
                total_sales=1000, units=10, spend=100, ppc_sales=500,
            ),
        ]
        # 10:45 AM PDT on Day 2 (2026-06-22) — a regular hourly slot, not midnight.
        day2_morning = real_datetime(2026, 6, 22, 17, 45, tzinfo=timezone.utc)
        captured: dict = {}

        def fake_get_anchor(event_id, client_id, channel_id, event_date):
            captured["event_date"] = event_date
            return None

        with (
            patch("slack_bot.main.datetime") as mock_dt_cls,
            patch("slack_bot.main.get_live_event", return_value={
                "id": "e1", "name": "Prime Day", "start_date": "2026-06-21",
            }),
            patch("slack_bot.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("slack_bot.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("slack_bot.main._query_metrics", return_value=metrics),
            patch("slack_bot.main.get_thread_anchor_ts", side_effect=fake_get_anchor),
            patch("slack_bot.main.set_thread_anchor_ts"),
            patch(
                "slack_bot.main.post_message",
                side_effect=[{"ok": True, "ts": "PARENT.D2"}, {"ok": True, "ts": "REPLY.D2"}],
            ) as mock_post,
            patch("slack_bot.main.log_bot_activity"),
        ):
            mock_dt_cls.now.return_value = day2_morning
            body, status = handler(_make_request())

        assert status == 200
        # Anchor keyed on Day 2's local date, and a new parent was created.
        assert captured["event_date"] == "2026-06-22"
        parent_call = mock_post.call_args_list[0]
        assert "Day 2" in parent_call.args[1][0]["text"]["text"]
        assert "Jun 22" in parent_call.args[1][0]["text"]["text"]


class TestManualPriorAds:
    """Operator-supplied prior-year ads back-fill recap YoY when Amazon Ads can
    no longer serve the history (its reporting API retains only ~95 days, so an
    event a full year in the past returns no ads rows and YoY rendered 0/—).
    """

    def test_lookup_returns_entry_and_coerces(self):
        from slack_bot.main import _manual_ads_lookup

        manual = {"US": {"2025-06-09": {"spend": "150.5", "ppc_sales": 600}}}
        assert _manual_ads_lookup(manual, "US", "2025-06-09") == {
            "spend": 150.5,
            "ppc_sales": 600.0,
        }

    def test_lookup_misses_return_none(self):
        from slack_bot.main import _manual_ads_lookup

        manual = {"US": {"2025-06-09": {"spend": 1, "ppc_sales": 2}}}
        assert _manual_ads_lookup(None, "US", "2025-06-09") is None
        assert _manual_ads_lookup(manual, "CA", "2025-06-09") is None
        assert _manual_ads_lookup(manual, "US", "2025-06-08") is None

    def test_query_metrics_prefers_manual_ads_over_bigquery(self):
        import slack_bot.main as mod

        manual = {"US": {"2025-06-09": {"spend": 150.0, "ppc_sales": 600.0}}}
        with (
            patch.object(mod, "_get_bq", return_value=MagicMock()),
            patch.object(mod, "_query_orders", return_value={"total_sales": 1000.0, "units": 10}),
            patch.object(mod, "_query_ads") as mock_ads,
        ):
            result = mod._query_metrics(
                "c1", ["US"], datetime(2026, 6, 10, tzinfo=timezone.utc),
                report_date="2025-06-09", full_day=True, manual_ads=manual,
            )

        # Manual ads win; BigQuery ads must not even be queried for that day.
        mock_ads.assert_not_called()
        assert result[0].spend == 150.0
        assert result[0].ppc_sales == 600.0
        assert result[0].total_sales == 1000.0

    def test_query_metrics_falls_back_to_bigquery_without_manual(self):
        import slack_bot.main as mod

        manual = {"US": {"2025-06-08": {"spend": 1.0, "ppc_sales": 2.0}}}
        with (
            patch.object(mod, "_get_bq", return_value=MagicMock()),
            patch.object(mod, "_query_orders", return_value={"total_sales": 1000.0, "units": 10}),
            patch.object(mod, "_query_ads", return_value={"spend": 50.0, "ppc_sales": 200.0}) as mock_ads,
        ):
            result = mod._query_metrics(
                "c1", ["US"], datetime(2026, 6, 10, tzinfo=timezone.utc),
                report_date="2025-06-09", full_day=True, manual_ads=manual,
            )

        mock_ads.assert_called_once()
        assert result[0].spend == 50.0
        assert result[0].ppc_sales == 200.0

    def test_prior_yoy_uses_manual_ads_for_single_and_cumulative(self):
        """Regression: a year-ago prior event returns 0 ads from BigQuery, so the
        recap YoY ads rendered 0. With manual ads on the prior event, single-day
        and cumulative YoY ads populate from the operator figures."""
        import slack_bot.main as mod

        prior_event = {
            "id": "prior_e1",
            "start_date": "2025-06-08",
            "manual_ads": {
                "US": {
                    "2025-06-08": {"spend": 100.0, "ppc_sales": 400.0},
                    "2025-06-09": {"spend": 150.0, "ppc_sales": 600.0},
                }
            },
        }

        with (
            patch.object(mod, "get_event", return_value=prior_event),
            patch.object(mod, "_get_bq", return_value=MagicMock()),
            patch.object(mod, "_query_orders", return_value={"total_sales": 1000.0, "units": 10}),
            # Simulate Amazon no longer serving year-old ads: BigQuery returns nothing.
            patch.object(mod, "_query_ads", return_value={"spend": 0.0, "ppc_sales": 0.0}),
        ):
            prior_single, prior_cumulative = mod._fetch_prior_yoy_metrics(
                "prior_e1", "c1", ["US"], recap_day=2, client_tz=ZoneInfo("America/Los_Angeles"),
            )

        # Single-day YoY = prior Day 2 (2025-06-09).
        assert prior_single is not None
        assert prior_single[0].spend == 150.0
        assert prior_single[0].ppc_sales == 600.0

        # Cumulative YoY = prior Days 1+2 (2025-06-08 + 2025-06-09).
        assert prior_cumulative is not None
        assert prior_cumulative[0].spend == 250.0
        assert prior_cumulative[0].ppc_sales == 1000.0
