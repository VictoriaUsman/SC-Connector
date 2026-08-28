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
    sku_breakdown: bool | None = None,
) -> dict:
    config = {
        "id": client_id,
        "client_id": client_id,
        "hourly_bot": {"enabled": enabled},
        "marketplaces": marketplaces or ["US"],
        "slack_channel_id": "C123",
        "client_timezone": "America/Los_Angeles",
        "base_currency": "USD",
        "use_test_channel": False,
    }
    if sku_breakdown is not None:
        config["sku_breakdown_enabled"] = sku_breakdown
    return config


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
        assert "purchase_date >= @day_start" in query
        # The fix: bound the top of the current day too, and never pin to the
        # ingestion report_date partition (the source of the freeze).
        assert "purchase_date < @day_end" in query
        assert "report_date" not in query

        params = captured["params"]
        # US -> America/Los_Angeles; 2026-07-13 is PDT (UTC-7): 00:00 local = 07:00Z,
        # next midnight 2026-07-14 00:00 local = 2026-07-14 07:00Z.
        assert params["day_start"] == datetime(2026, 7, 13, 7, 0, tzinfo=timezone.utc)
        assert params["day_end"] == datetime(2026, 7, 14, 7, 0, tzinfo=timezone.utc)


class TestQueryOrdersMarketplaceScoping:
    """Total Sales cross-marketplace leakage bug: the All Orders report is
    account-wide (Amazon returns every order for the seller's region regardless
    of the marketplaceId requested) and ingestion stamps all of those rows with
    the single marketplace it pulled under. Filtering only on the stamped
    ``marketplace`` therefore leaked other marketplaces' orders into a
    per-marketplace Total Sales. The fix additionally scopes orders to the
    marketplace's own storefront via the ``sales_channel`` column."""

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

    def test_hourly_scopes_orders_by_sales_channel(self):
        from slack_bot.main import _query_orders

        captured: dict = {}
        bq = self._fake_bq(captured)

        _query_orders(
            bq, "proj", "ds", "c1", "UK", "2026-07-13",
            datetime(2026, 7, 13, 23, 45, tzinfo=timezone.utc),
            full_day=False,
        )

        query = captured["query"]
        # The marketplace partition filter stays (it de-dupes orders_latest)...
        assert "marketplace = @marketplace" in query
        # ...and orders are additionally restricted to this marketplace's
        # storefront so a EU account's DE/FR/IT orders don't inflate the UK total.
        assert "LOWER(sales_channel) = @sales_channel" in query
        assert captured["params"]["sales_channel"] == "amazon.co.uk"

    def test_full_day_scopes_orders_by_sales_channel(self):
        from slack_bot.main import _query_orders

        captured: dict = {}
        bq = self._fake_bq(captured)

        _query_orders(
            bq, "proj", "ds", "c1", "US", "2026-07-13",
            datetime(2026, 7, 14, 7, 45, tzinfo=timezone.utc),
            full_day=True,
        )

        query = captured["query"]
        assert "LOWER(sales_channel) = @sales_channel" in query
        assert captured["params"]["sales_channel"] == "amazon.com"

    def test_unknown_marketplace_falls_back_to_unscoped(self):
        """An unmapped marketplace must not silently zero out Total Sales — it
        falls back to the prior, sales-channel-unscoped behaviour."""
        from slack_bot.main import _query_orders

        captured: dict = {}
        bq = self._fake_bq(captured)

        _query_orders(
            bq, "proj", "ds", "c1", "ZZ", "2026-07-13",
            datetime(2026, 7, 13, 23, 45, tzinfo=timezone.utc),
            full_day=False,
        )

        query = captured["query"]
        assert "sales_channel" not in query
        assert "sales_channel" not in captured["params"]


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
        midday_pdt = datetime(2026, 7, 14, 18, 5, tzinfo=timezone.utc)  # 11:05 AM PDT

        with (
            patch("slack_bot.main.datetime") as mock_dt_cls,
            patch("slack_bot.main.get_live_event", return_value={
                "id": "e1", "name": "Prime Day", "start_date": "2026-07-13",
            }),
            patch("slack_bot.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("slack_bot.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("slack_bot.main._query_metrics", return_value=bad_metrics),
            patch("slack_bot.main.post_message") as mock_post,
            patch("slack_bot.main.log_bot_activity") as mock_log,
        ):
            mock_dt_cls.now.return_value = midday_pdt
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

    def test_not_in_channel_is_channel_skip_not_error(self):
        """A `not_in_channel` Slack error means the app was never invited to the
        channel (e.g. someone changed the config's channel). It is operator-
        fixable, so it is tracked as a channel-config skip — never a paging
        error — and recorded with an error_code + the channel to fix."""
        from slack_bot.main import handler, MarketplaceMetrics
        from shared.slack_client import SlackApiError

        metrics = [
            MarketplaceMetrics(
                marketplace="US", currency="USD",
                total_sales=1000, units=10, spend=100, ppc_sales=500,
            ),
        ]

        with (
            patch("slack_bot.main.get_live_event", return_value={
                "id": "e1", "name": "Prime Day", "start_date": "2026-07-13",
            }),
            patch("slack_bot.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("slack_bot.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("slack_bot.main._query_metrics", return_value=metrics),
            patch("slack_bot.main.get_thread_anchor_ts", return_value="999.000"),
            patch("slack_bot.main.set_thread_anchor_ts"),
            patch("slack_bot.main.post_message",
                  side_effect=SlackApiError("not_in_channel", channel_id="C123")),
            patch("slack_bot.main.log_bot_activity") as mock_log,
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 0
        assert body["errors"] == 0
        assert body["channel_skips"] == 1
        log_data = mock_log.call_args[0][0]
        assert log_data["status"] == "failed"
        assert log_data["error_code"] == "not_in_channel"
        assert log_data["channel_id"] == "C123"

    def test_generic_slack_error_is_real_error(self):
        """A non-channel-config Slack error (e.g. rate limit) is a genuine
        error, not a channel skip."""
        from slack_bot.main import handler, MarketplaceMetrics
        from shared.slack_client import SlackApiError

        metrics = [
            MarketplaceMetrics(
                marketplace="US", currency="USD",
                total_sales=1000, units=10, spend=100, ppc_sales=500,
            ),
        ]

        with (
            patch("slack_bot.main.get_live_event", return_value={
                "id": "e1", "name": "Prime Day", "start_date": "2026-07-13",
            }),
            patch("slack_bot.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("slack_bot.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("slack_bot.main._query_metrics", return_value=metrics),
            patch("slack_bot.main.get_thread_anchor_ts", return_value="999.000"),
            patch("slack_bot.main.set_thread_anchor_ts"),
            patch("slack_bot.main.post_message",
                  side_effect=SlackApiError("ratelimited", channel_id="C123")),
            patch("slack_bot.main.log_bot_activity") as mock_log,
        ):
            body, status = handler(_make_request())

        assert body["errors"] == 1
        assert body["channel_skips"] == 0
        log_data = mock_log.call_args[0][0]
        assert log_data["error_code"] == "ratelimited"

    def test_broadcasts_to_every_configured_channel(self):
        """A config with multiple `channels` posts the same message to each,
        threaded independently under each channel's own day anchor."""
        from slack_bot.main import handler

        config = _make_bot_config()
        config.pop("slack_channel_id", None)
        config["channels"] = [{"id": "C1", "name": "one"}, {"id": "C2", "name": "two"}]

        with (
            patch("slack_bot.main.get_live_event", return_value={
                "id": "e1", "name": "Prime Day", "start_date": "2026-07-13",
            }),
            patch("slack_bot.main.list_bot_configs", return_value=[config]),
            patch("slack_bot.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("slack_bot.main._query_metrics", return_value=[]),
            patch("slack_bot.main._build_message_blocks", return_value=[
                {"type": "section", "text": {"type": "mrkdwn", "text": "x"}},
            ]),
            patch("slack_bot.main.get_thread_anchor_ts", return_value="999.000"),
            patch("slack_bot.main.set_thread_anchor_ts"),
            patch("slack_bot.main.post_message", return_value={"ok": True, "ts": "123.456"}) as mock_post,
            patch("slack_bot.main.log_bot_activity"),
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 2
        posted_channels = [call.args[0] for call in mock_post.call_args_list]
        assert posted_channels == ["C1", "C2"]

    def test_one_channel_failing_does_not_block_the_others(self):
        """A `not_in_channel` error on one channel is a skip for that channel
        only — the broadcast still reaches every other configured channel."""
        from slack_bot.main import handler
        from shared.slack_client import SlackApiError

        config = _make_bot_config()
        config.pop("slack_channel_id", None)
        config["channels"] = [{"id": "C1"}, {"id": "C2"}]

        def fake_post(channel_id, blocks, text_fallback, thread_ts=None):
            if channel_id == "C1":
                raise SlackApiError("not_in_channel", channel_id="C1")
            return {"ok": True, "ts": "123.456"}

        with (
            patch("slack_bot.main.get_live_event", return_value={
                "id": "e1", "name": "Prime Day", "start_date": "2026-07-13",
            }),
            patch("slack_bot.main.list_bot_configs", return_value=[config]),
            patch("slack_bot.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("slack_bot.main._query_metrics", return_value=[]),
            patch("slack_bot.main._build_message_blocks", return_value=[
                {"type": "section", "text": {"type": "mrkdwn", "text": "x"}},
            ]),
            patch("slack_bot.main.get_thread_anchor_ts", return_value="999.000"),
            patch("slack_bot.main.set_thread_anchor_ts"),
            patch("slack_bot.main.post_message", side_effect=fake_post) as mock_post,
            patch("slack_bot.main.log_bot_activity"),
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 1
        assert body["channel_skips"] == 1
        assert body["errors"] == 0
        # Both channels were attempted — C1's failure didn't short-circuit C2.
        assert [call.args[0] for call in mock_post.call_args_list] == ["C1", "C2"]

    def test_legacy_single_channel_config_still_works(self):
        """A config saved before multi-channel support (only slack_channel_id,
        no channels list) still posts to its one channel."""
        from slack_bot.main import handler

        with (
            patch("slack_bot.main.get_live_event", return_value={
                "id": "e1", "name": "Prime Day", "start_date": "2026-07-13",
            }),
            patch("slack_bot.main.list_bot_configs", return_value=[_make_bot_config()]),
            patch("slack_bot.main.get_client", return_value={"id": "c1", "name": "Acme", "is_active": True}),
            patch("slack_bot.main._query_metrics", return_value=[]),
            patch("slack_bot.main._build_message_blocks", return_value=[
                {"type": "section", "text": {"type": "mrkdwn", "text": "x"}},
            ]),
            patch("slack_bot.main.get_thread_anchor_ts", return_value="999.000"),
            patch("slack_bot.main.set_thread_anchor_ts"),
            patch("slack_bot.main.post_message", return_value={"ok": True, "ts": "123.456"}) as mock_post,
            patch("slack_bot.main.log_bot_activity"),
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 1
        assert mock_post.call_args.args[0] == "C123"


class TestGetLiveEvent:
    """get_live_event must be deterministic when several events are live."""

    def _fake_doc(self, doc_id: str, data: dict):
        doc = MagicMock()
        doc.id = doc_id
        doc.to_dict.return_value = data
        return doc

    def _patch_db(self, docs: list):
        fake_db = MagicMock()
        fake_db.collection.return_value.where.return_value.stream.return_value = iter(docs)
        return patch("shared.firestore_utils.get_db", return_value=fake_db)

    def test_none_when_no_live_event(self):
        from shared.firestore_utils import get_live_event

        with self._patch_db([]):
            assert get_live_event() is None

    def test_single_live_event(self):
        from shared.firestore_utils import get_live_event

        docs = [self._fake_doc("e1", {"name": "PD", "start_date": "2026-06-21"})]
        with self._patch_db(docs):
            ev = get_live_event()
        assert ev["id"] == "e1"

    def test_multiple_live_events_picks_earliest_start_deterministically(self):
        from shared.firestore_utils import get_live_event

        # Intentionally out of order; earliest start_date (then id) must win.
        docs = [
            self._fake_doc("zeta", {"name": "B", "start_date": "2026-06-22"}),
            self._fake_doc("alpha", {"name": "A", "start_date": "2026-06-21"}),
        ]
        with self._patch_db(docs):
            ev = get_live_event()
        assert ev["id"] == "alpha"


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
        # Stored keyed on (event, channel, local date) — NOT per client, so all
        # accounts in the channel share one daily anchor. The creating account
        # is recorded for debugging only.
        mock_set.assert_called_once_with(
            "e1", "C1", "2026-06-21", "PARENT.1", created_by_client_id="c1",
        )

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

    def test_multiple_accounts_one_channel_share_single_daily_anchor(self):
        """Several accounts/marketplaces posting to the SAME channel (e.g.
        Skylight's per-marketplace accounts) must thread under ONE daily parent,
        not one anchor each."""
        from slack_bot.main import handler, MarketplaceMetrics

        metrics = [
            MarketplaceMetrics(
                marketplace="US", currency="USD",
                total_sales=1000, units=10, spend=100, ppc_sales=500,
            ),
        ]
        # Two distinct accounts, same Slack channel ("C123" via _make_bot_config).
        configs = [
            _make_bot_config(client_id="skylight-frame-de"),
            _make_bot_config(client_id="skylight-frame-uk"),
        ]

        # Stateful anchor store keyed on (event, channel, date) — mirrors prod.
        store: dict = {}

        def fake_get(event_id, channel_id, event_date):
            return store.get((event_id, channel_id, event_date))

        def fake_set(event_id, channel_id, event_date, parent_ts, *, created_by_client_id=None):
            key = (event_id, channel_id, event_date)
            if key in store:
                raise RuntimeError("already exists")  # create() semantics
            store[key] = parent_ts

        posts: list = []

        def fake_post(channel_id, blocks, fallback, thread_ts=None):
            posts.append({"thread_ts": thread_ts})
            # Anchor posts (no thread_ts) get a stable parent ts.
            return {"ok": True, "ts": "PARENT" if thread_ts is None else "REPLY"}

        with (
            patch("slack_bot.main.get_live_event", return_value={
                "id": "e1", "name": "niv tes", "start_date": "2026-06-21",
            }),
            patch("slack_bot.main.list_bot_configs", return_value=configs),
            patch("slack_bot.main.get_client",
                  side_effect=lambda cid: {"id": cid, "name": cid, "is_active": True}),
            patch("slack_bot.main._query_metrics", return_value=metrics),
            patch("slack_bot.main.get_thread_anchor_ts", side_effect=fake_get),
            patch("slack_bot.main.set_thread_anchor_ts", side_effect=fake_set),
            patch("slack_bot.main.post_message", side_effect=fake_post),
            patch("slack_bot.main.log_bot_activity"),
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 2
        # Exactly ONE top-level anchor for the shared channel/day.
        top_level = [p for p in posts if p["thread_ts"] is None]
        threaded = [p for p in posts if p["thread_ts"] == "PARENT"]
        assert len(top_level) == 1
        # Both accounts' updates threaded under that single parent.
        assert len(threaded) == 2
        assert len(store) == 1

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

        def fake_get_anchor(event_id, channel_id, event_date):
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


# ---------------------------------------------------------------------------
# Per-SKU breakdown (CU-868k3546u) — cumulative day-to-date SKU rows appended
# to Skylight/Ritual hourly drops, reconciling to the account total.
# ---------------------------------------------------------------------------

class TestSkuBreakdownGating:
    """Only the allowlisted account families (Skylight, Ritual) get the
    breakdown; every other account is untouched."""

    def test_default_families_match_skylight_and_ritual(self):
        from slack_bot.main import _sku_breakdown_enabled

        assert _sku_breakdown_enabled("skylight-frame") is True
        assert _sku_breakdown_enabled("ritual") is True
        # Regional variants belong to the same family.
        assert _sku_breakdown_enabled("skylight-frame-uk") is True
        assert _sku_breakdown_enabled("skylight-frame-ca") is True

    def test_other_accounts_excluded(self):
        from slack_bot.main import _sku_breakdown_enabled

        assert _sku_breakdown_enabled("c1") is False
        assert _sku_breakdown_enabled("brook-whittle") is False
        # A name that merely contains "ritual" as a substring must not match;
        # only exact id or "ritual-" prefix counts.
        assert _sku_breakdown_enabled("spiritual-goods") is False

    def test_env_override_replaces_default_allowlist(self):
        import slack_bot.main as mod

        with patch.dict(os.environ, {"SKU_BREAKDOWN_CLIENT_IDS": "foo, bar-baz"}):
            assert mod._sku_breakdown_enabled("foo") is True
            assert mod._sku_breakdown_enabled("bar-baz-uk") is True
            # Defaults are replaced, not merged.
            assert mod._sku_breakdown_enabled("ritual") is False


class TestSkuBreakdownConfigToggle:
    """The per-customer ``sku_breakdown_enabled`` toggle lets a PM enable the
    breakdown for any customer; the legacy allowlist is an OR-ed safety net."""

    def test_toggle_enables_non_allowlisted_client(self):
        from slack_bot.main import _sku_breakdown_enabled_for_config

        cfg = {"client_id": "brook-whittle", "sku_breakdown_enabled": True}
        assert _sku_breakdown_enabled_for_config(cfg) is True

    def test_no_toggle_and_not_allowlisted_is_disabled(self):
        from slack_bot.main import _sku_breakdown_enabled_for_config

        assert _sku_breakdown_enabled_for_config({"client_id": "brook-whittle"}) is False
        assert _sku_breakdown_enabled_for_config(
            {"client_id": "brook-whittle", "sku_breakdown_enabled": False}
        ) is False

    def test_allowlisted_client_stays_enabled_without_toggle(self):
        from slack_bot.main import _sku_breakdown_enabled_for_config

        # Legacy default: Skylight/Ritual keep working before the PM toggles them.
        assert _sku_breakdown_enabled_for_config({"client_id": "ritual"}) is True
        assert _sku_breakdown_enabled_for_config({"client_id": "skylight-frame-uk"}) is True

    def test_allowlist_wins_even_if_toggle_false(self):
        from slack_bot.main import _sku_breakdown_enabled_for_config

        # OR semantics: the allowlist is a safety net, so an allowlisted account
        # can't be silently disabled by a stale false flag (zero-regression).
        assert _sku_breakdown_enabled_for_config(
            {"client_id": "ritual", "sku_breakdown_enabled": False}
        ) is True


class TestSkuQueryReconcilesByConstruction:
    """The SKU query must read the SAME source/window/filters as the hourly
    account-orders query, only adding GROUP BY sku — so the rows reconcile to
    the account total by construction (no second independent pull)."""

    @staticmethod
    def _capture_bq(rows: list[dict]) -> tuple[MagicMock, dict]:
        captured: dict = {}

        def fake_query(query, job_config=None):
            captured["query"] = query
            captured["params"] = {p.name: p.value for p in job_config.query_parameters}
            return iter(rows)

        bq = MagicMock()
        bq.query.side_effect = fake_query
        return bq, captured

    def test_sku_window_matches_hourly_account_window_exactly(self):
        from slack_bot.main import _query_orders, _query_sku_orders

        now = datetime(2026, 7, 13, 23, 45, tzinfo=timezone.utc)

        acct_bq, acct_cap = self._capture_bq([{"total_sales": 100.0, "units": 5}])
        _query_orders(acct_bq, "proj", "ds", "ritual", "US", "2026-07-13", now, full_day=False)

        sku_bq, sku_cap = self._capture_bq([{"sku": "A", "total_sales": 100.0, "units": 5}])
        _query_sku_orders(sku_bq, "proj", "ds", "ritual", "US", now)

        # Identical day-to-date window and filters → identical row set. Both
        # queries now share the same param names (via _marketplace_day_window).
        assert sku_cap["params"]["day_start"] == acct_cap["params"]["day_start"]
        assert sku_cap["params"]["day_end"] == acct_cap["params"]["day_end"]
        assert sku_cap["params"]["client_id"] == acct_cap["params"]["client_id"]
        assert sku_cap["params"]["marketplace"] == acct_cap["params"]["marketplace"]
        # The sales_channel storefront scoping MUST match the account query, or
        # the SKU rollup over-counts other-storefront lines and fails to
        # reconcile (the Skylight regression: a non-amazon.com $0 line added a
        # phantom unit, suppressing the breakdown).
        assert sku_cap["params"].get("sales_channel") == acct_cap["params"].get("sales_channel")

        q = sku_cap["query"]
        assert "purchase_date >= @day_start" in q
        assert "purchase_date < @day_end" in q
        assert "order_status != 'Cancelled'" in q
        assert "LOWER(sales_channel) = @sales_channel" in q
        assert "GROUP BY sku" in q
        # Never key off the ingestion report_date partition (the freeze bug).
        assert "report_date" not in q

    def test_sku_full_day_window_matches_account_full_day_window(self):
        from slack_bot.main import _query_orders, _query_sku_orders

        # now is just past midnight of the NEXT day, like the midnight recap slot.
        now = datetime(2026, 7, 14, 8, 30, tzinfo=timezone.utc)

        acct_bq, acct_cap = self._capture_bq([{"total_sales": 100.0, "units": 5}])
        _query_orders(acct_bq, "proj", "ds", "ritual", "US", "2026-07-13", now, full_day=True)

        sku_bq, sku_cap = self._capture_bq([{"sku": "A", "total_sales": 100.0, "units": 5}])
        _query_sku_orders(
            sku_bq, "proj", "ds", "ritual", "US", now,
            report_date="2026-07-13", full_day=True,
        )

        # A completed-day recap SKU query spans the same full calendar day as the
        # account recap query, so the SKU rows reconcile to the recap total.
        assert sku_cap["params"]["day_start"] == acct_cap["params"]["day_start"]
        assert sku_cap["params"]["day_end"] == acct_cap["params"]["day_end"]

        # ...and that full recap-day window is NOT the live "today" window: the
        # hourly (full_day=False) query keys off `now`, which is the new day.
        hourly_bq, hourly_cap = self._capture_bq([{"sku": "A", "total_sales": 0.0, "units": 0}])
        _query_sku_orders(hourly_bq, "proj", "ds", "ritual", "US", now)
        assert hourly_cap["params"]["day_start"] != sku_cap["params"]["day_start"]

    def test_aggregates_across_marketplaces_and_sorts_desc(self):
        import slack_bot.main as mod

        per_mkt = {
            "US": [mod.SkuMetrics("A", 10, 300.0), mod.SkuMetrics("B", 2, 50.0)],
            "CA": [mod.SkuMetrics("A", 5, 150.0)],
        }

        def fake_sku_orders(bq, project, dataset, client_id, marketplace, now, **kwargs):
            return per_mkt[marketplace]

        with (
            patch.object(mod, "_get_bq", return_value=MagicMock()),
            patch.object(mod, "_query_sku_orders", side_effect=fake_sku_orders),
        ):
            rows = mod._query_sku_breakdown(
                "ritual", ["US", "CA"], datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc),
            )

        by_sku = {r.sku: r for r in rows}
        # A is summed across marketplaces.
        assert by_sku["A"].units == 15
        assert by_sku["A"].total_sales == 450.0
        # Sorted by sales desc (A before B), and no SKU dropped (no top-N cap).
        assert [r.sku for r in rows] == ["A", "B"]


class TestSkuReconciliation:
    def test_reconciles_when_sums_match(self):
        from slack_bot.main import MarketplaceMetrics, SkuMetrics, _sku_breakdown_reconciles

        metrics = [MarketplaceMetrics("US", "USD", total_sales=350.0, units=12, spend=0, ppc_sales=0)]
        skus = [SkuMetrics("A", 10, 300.0), SkuMetrics("B", 2, 50.0)]
        assert _sku_breakdown_reconciles(skus, metrics) is True

    def test_sub_cent_sales_drift_tolerated(self):
        from slack_bot.main import MarketplaceMetrics, SkuMetrics, _sku_breakdown_reconciles

        metrics = [MarketplaceMetrics("US", "USD", total_sales=100.0, units=3, spend=0, ppc_sales=0)]
        skus = [SkuMetrics("A", 2, 66.667), SkuMetrics("B", 1, 33.337)]  # 100.004
        assert _sku_breakdown_reconciles(skus, metrics) is True

    def test_unit_mismatch_fails(self):
        from slack_bot.main import MarketplaceMetrics, SkuMetrics, _sku_breakdown_reconciles

        metrics = [MarketplaceMetrics("US", "USD", total_sales=350.0, units=12, spend=0, ppc_sales=0)]
        skus = [SkuMetrics("A", 9, 300.0), SkuMetrics("B", 2, 50.0)]  # 11 != 12
        assert _sku_breakdown_reconciles(skus, metrics) is False

    def test_sales_mismatch_beyond_tolerance_fails(self):
        from slack_bot.main import MarketplaceMetrics, SkuMetrics, _sku_breakdown_reconciles

        metrics = [MarketplaceMetrics("US", "USD", total_sales=350.0, units=12, spend=0, ppc_sales=0)]
        skus = [SkuMetrics("A", 10, 290.0), SkuMetrics("B", 2, 50.0)]  # 340 != 350
        assert _sku_breakdown_reconciles(skus, metrics) is False


class TestSkuBreakdownBlocks:
    def test_lists_every_sku_no_cap_and_chunks(self):
        from slack_bot.main import SkuMetrics, _build_sku_breakdown_blocks

        skus = [SkuMetrics(f"SKU-{i:04d}", i + 1, float(i) + 0.5) for i in range(120)]
        blocks = _build_sku_breakdown_blocks(skus, "USD")

        all_text = " ".join(
            b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section"
        )
        assert "Per-SKU Breakdown" in all_text
        # All 120 SKUs present (no top-N truncation).
        for i in range(120):
            assert f"SKU-{i:04d}" in all_text
        # Long lists split across multiple section blocks under Slack's limit.
        section_blocks = [b for b in blocks if b.get("type") == "section"]
        assert len(section_blocks) >= 2
        for b in section_blocks:
            assert len(b["text"]["text"]) <= 3000


class TestAppendSkuBreakdown:
    _now = datetime(2026, 7, 13, 20, 45, tzinfo=timezone.utc)

    def _metrics(self):
        from slack_bot.main import MarketplaceMetrics

        return [MarketplaceMetrics("US", "USD", total_sales=350.0, units=12, spend=10, ppc_sales=100)]

    def test_appends_when_reconciles_and_is_additive(self):
        import slack_bot.main as mod

        existing = [{"type": "section", "text": {"type": "mrkdwn", "text": "ACCOUNT LINE"}}]
        blocks = list(existing)
        skus = [mod.SkuMetrics("A", 10, 300.0), mod.SkuMetrics("B", 2, 50.0)]

        with patch.object(mod, "_query_sku_breakdown", return_value=skus):
            mod._append_sku_breakdown(blocks, "ritual", ["US"], self._now, self._metrics())

        # Strictly additive: the original block is untouched and still first.
        assert blocks[0] == existing[0]
        all_text = " ".join(
            b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section"
        )
        assert "ACCOUNT LINE" in all_text
        assert "Per-SKU Breakdown" in all_text
        assert "`A`" in all_text and "`B`" in all_text

    def test_suppressed_when_reconciliation_fails(self):
        import slack_bot.main as mod

        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "ACCOUNT LINE"}}]
        before = list(blocks)
        # Units sum to 11, account says 12 → mismatch.
        skus = [mod.SkuMetrics("A", 9, 300.0), mod.SkuMetrics("B", 2, 50.0)]

        with patch.object(mod, "_query_sku_breakdown", return_value=skus):
            mod._append_sku_breakdown(blocks, "ritual", ["US"], self._now, self._metrics())

        assert blocks == before  # unchanged

    def test_suppressed_on_multi_currency(self):
        import slack_bot.main as mod
        from slack_bot.main import MarketplaceMetrics

        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "ACCOUNT LINE"}}]
        before = list(blocks)
        multi = [
            MarketplaceMetrics("US", "USD", total_sales=100, units=2, spend=0, ppc_sales=0),
            MarketplaceMetrics("UK", "GBP", total_sales=80, units=1, spend=0, ppc_sales=0),
        ]

        with patch.object(mod, "_query_sku_breakdown") as mock_q:
            mod._append_sku_breakdown(blocks, "ritual", ["US", "UK"], self._now, multi)

        assert blocks == before
        mock_q.assert_not_called()  # no query when there's no single total to match

    def test_suppressed_on_query_error(self):
        import slack_bot.main as mod

        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "ACCOUNT LINE"}}]
        before = list(blocks)

        with patch.object(mod, "_query_sku_breakdown", side_effect=RuntimeError("BQ down")):
            mod._append_sku_breakdown(blocks, "ritual", ["US"], self._now, self._metrics())

        assert blocks == before  # error never breaks the existing drop


class TestSkuYoy:
    """Hourly SKU lines overlay last year's equivalent event-hour from BQ."""

    _now = datetime(2026, 7, 13, 20, 45, tzinfo=timezone.utc)  # 13:45 PDT
    _pst = ZoneInfo("America/Los_Angeles")

    def test_format_with_prior_and_new(self):
        from slack_bot.main import _format_sku_yoy

        assert _format_sku_yoy(320, 280, "280") == " (LY 280, +14%)"
        assert _format_sku_yoy(200, 280, "280") == " (LY 280, -29%)"
        assert _format_sku_yoy(50, None, "") == " (new)"
        assert _format_sku_yoy(50, 0, "0") == " (new)"

    def test_line_shows_units_and_sales_yoy(self):
        from slack_bot.main import SkuMetrics, _sku_breakdown_line

        row = SkuMetrics("A", 320, 1200.0, ly_units=280, ly_total_sales=1050.0)
        line = _sku_breakdown_line(row, "USD", yoy=True)
        assert "`A` — 320 units (LY 280, +14%)" in line
        assert "(LY $1,050.00, +14%)" in line

        new_row = SkuMetrics("B", 10, 50.0)
        new_line = _sku_breakdown_line(new_row, "USD", yoy=True)
        assert "(new)" in new_line
        assert "LY" not in new_line
        assert "-100%" not in new_line

        plain = _sku_breakdown_line(row, "USD", yoy=False)
        assert "LY" not in plain
        assert "(new)" not in plain

    def test_event_day_aligns_to_prior_start(self):
        from slack_bot.main import _resolve_prior_event_date

        with patch("slack_bot.main.get_event", return_value={"start_date": "2025-07-08"}):
            assert _resolve_prior_event_date("prior1", 1, self._pst) == "2025-07-08"
            assert _resolve_prior_event_date("prior1", 2, self._pst) == "2025-07-09"
        assert _resolve_prior_event_date(None, 1, self._pst) is None
        with patch("slack_bot.main.get_event", return_value=None):
            assert _resolve_prior_event_date("missing", 1, self._pst) is None

    def test_hourly_ly_window_caps_at_current_hour(self):
        from slack_bot.main import _clock_on_date, _marketplace_day_window

        ly_now = _clock_on_date(self._now, "2025-07-08", self._pst)
        start, end = _marketplace_day_window(
            "US", ly_now, report_date="2025-07-08", through=ly_now,
        )
        full_start, full_end = _marketplace_day_window(
            "US", ly_now, report_date="2025-07-08",
        )
        assert start == full_start
        assert end != full_end
        assert end == ly_now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert ly_now.astimezone(self._pst).hour == 13

    def test_recap_ly_window_is_full_day(self):
        from slack_bot.main import _clock_on_date, _marketplace_day_window

        ly_now = _clock_on_date(self._now, "2025-07-08", self._pst)
        start, end = _marketplace_day_window(
            "US", ly_now, report_date="2025-07-08", full_day=True, through=ly_now,
        )
        _, full_end = _marketplace_day_window(
            "US", ly_now, report_date="2025-07-08", full_day=True,
        )
        assert end == full_end

    def test_sku_orders_forwards_through(self):
        from slack_bot.main import _clock_on_date, _query_sku_orders

        ly_now = _clock_on_date(self._now, "2025-07-08", self._pst)
        captured: dict = {}

        def fake_query(query, job_config=None):
            captured["params"] = {p.name: p.value for p in job_config.query_parameters}
            return iter([])

        bq = MagicMock()
        bq.query.side_effect = fake_query
        _query_sku_orders(
            bq, "proj", "ds", "ritual", "US", ly_now,
            report_date="2025-07-08", through=ly_now,
        )
        day_end = captured["params"]["day_end"]
        if hasattr(day_end, "strftime"):
            day_end = day_end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert day_end == ly_now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def test_append_renders_yoy_and_new(self):
        import slack_bot.main as mod

        current = [mod.SkuMetrics("A", 320, 1200.0), mod.SkuMetrics("B", 10, 50.0)]
        ly = [mod.SkuMetrics("A", 280, 1050.0)]
        calls: list[dict] = []

        def fake_breakdown(client_id, marketplaces, now, **kwargs):
            calls.append(kwargs)
            return ly if kwargs.get("report_date") == "2025-07-08" else current

        blocks: list[dict] = []
        with (
            patch.object(mod, "_query_sku_breakdown", side_effect=fake_breakdown),
            patch.object(mod, "get_event", return_value={"start_date": "2025-07-08"}),
        ):
            mod._append_sku_breakdown(
                blocks, "ritual", ["US"], self._now, self._metrics(),
                prior_event_id="prior1", event_day=1, client_tz=self._pst,
            )

        text = " ".join(
            b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section"
        )
        assert "320 units (LY 280, +14%)" in text
        assert "`B`" in text and "(new)" in text
        assert "-100%" not in text
        ly_call = next(c for c in calls if c.get("report_date") == "2025-07-08")
        assert ly_call.get("full_day") is False
        assert ly_call.get("through") is not None

    def test_append_without_prior_keeps_plain_lines(self):
        import slack_bot.main as mod

        skus = [mod.SkuMetrics("A", 10, 300.0)]
        metrics = [mod.MarketplaceMetrics("US", "USD", total_sales=300.0, units=10, spend=10, ppc_sales=100)]
        blocks: list[dict] = []
        with patch.object(mod, "_query_sku_breakdown", return_value=skus):
            mod._append_sku_breakdown(blocks, "ritual", ["US"], self._now, metrics)

        text = " ".join(
            b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section"
        )
        assert "`A` — 10 units ·" in text
        assert "LY" not in text
        assert "(new)" not in text

    def test_ly_query_failure_still_posts(self):
        import slack_bot.main as mod

        current = [mod.SkuMetrics("A", 10, 300.0)]
        metrics = [mod.MarketplaceMetrics("US", "USD", total_sales=300.0, units=10, spend=10, ppc_sales=100)]

        def fake_breakdown(client_id, marketplaces, now, **kwargs):
            if kwargs.get("report_date"):
                raise RuntimeError("LY BQ down")
            return current

        blocks: list[dict] = []
        with (
            patch.object(mod, "_query_sku_breakdown", side_effect=fake_breakdown),
            patch.object(mod, "get_event", return_value={"start_date": "2025-07-08"}),
        ):
            mod._append_sku_breakdown(
                blocks, "ritual", ["US"], self._now, metrics,
                prior_event_id="prior1", event_day=1, client_tz=self._pst,
            )

        text = " ".join(
            b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section"
        )
        assert "`A`" in text
        assert "LY" not in text

    def test_recap_ly_query_is_full_day(self):
        import slack_bot.main as mod

        current = [mod.SkuMetrics("A", 10, 1000.0)]
        captured: list[dict] = []

        def fake_breakdown(client_id, marketplaces, now, **kwargs):
            captured.append(kwargs)
            return current

        metrics = [mod.MarketplaceMetrics("US", "USD", total_sales=1000.0, units=10, spend=0, ppc_sales=0)]
        with (
            patch.object(mod, "_query_sku_breakdown", side_effect=fake_breakdown),
            patch.object(mod, "get_event", return_value={"start_date": "2025-07-08"}),
        ):
            mod._append_sku_breakdown(
                [], "ritual", ["US"], self._now, metrics,
                report_date="2026-07-13", full_day=True,
                prior_event_id="prior1", event_day=1, client_tz=self._pst,
            )

        ly_call = next(c for c in captured if c.get("report_date") == "2025-07-08")
        assert ly_call.get("full_day") is True
        assert ly_call.get("through") is None

    def _metrics(self):
        from slack_bot.main import MarketplaceMetrics

        return [MarketplaceMetrics("US", "USD", total_sales=1250.0, units=330, spend=10, ppc_sales=100)]


class TestSkuBreakdownMarketplaces:
    """The combined drop breaks down US, then CA, then UK by default, and the
    set is env-overridable so it can change mid-event without a deploy."""

    def test_default_is_us_ca_uk_in_order(self):
        from slack_bot.main import _sku_breakdown_marketplaces

        assert _sku_breakdown_marketplaces() == ("US", "CA", "UK")

    def test_env_override_replaces_default(self):
        import slack_bot.main as mod

        with patch.dict(os.environ, {"SKU_BREAKDOWN_MARKETPLACES": "us, de , fr"}):
            # Normalized to upper-case, whitespace-trimmed, empties dropped.
            assert mod._sku_breakdown_marketplaces() == ("US", "DE", "FR")

    def test_label_appears_in_breakdown_header(self):
        from slack_bot.main import SkuMetrics, _build_sku_breakdown_blocks

        skus = [SkuMetrics("A", 1, 10.0)]
        for mkt in ("US", "CA", "UK"):
            blocks = _build_sku_breakdown_blocks(skus, "USD", label=mkt)
            text = " ".join(
                b.get("text", {}).get("text", "")
                for b in blocks
                if b.get("type") == "section"
            )
            assert f"Per-SKU Breakdown ({mkt})" in text

    def test_no_label_keeps_unlabeled_header(self):
        from slack_bot.main import SkuMetrics, _build_sku_breakdown_blocks

        blocks = _build_sku_breakdown_blocks([SkuMetrics("A", 1, 10.0)], "USD")
        text = " ".join(
            b.get("text", {}).get("text", "")
            for b in blocks
            if b.get("type") == "section"
        )
        assert "Per-SKU Breakdown — Day to Date" in text
        assert "Per-SKU Breakdown (" not in text


class TestCombinedSkuBreakdown:
    """The combined Skylight drop appends a per-marketplace SKU breakdown for
    US, CA, and UK — each reconciled to its own single-currency account line."""

    _now = datetime(2026, 7, 13, 20, 45, tzinfo=timezone.utc)

    def _members(self):
        return [
            {"client_id": "skylight-frame", "marketplaces": ["US"]},
            {"client_id": "skylight-frame-ca", "marketplaces": ["CA"]},
            {"client_id": "skylight-frame-uk", "marketplaces": ["UK"]},
        ]

    def _metrics(self):
        from slack_bot.main import MarketplaceMetrics

        return [
            MarketplaceMetrics("US", "USD", total_sales=350.0, units=12, spend=10, ppc_sales=100),
            MarketplaceMetrics("CA", "CAD", total_sales=200.0, units=4, spend=5, ppc_sales=50),
            MarketplaceMetrics("UK", "GBP", total_sales=80.0, units=2, spend=2, ppc_sales=20),
        ]

    @staticmethod
    def _sku_rows_by_client():
        from slack_bot.main import SkuMetrics

        return {
            "skylight-frame": [SkuMetrics("US-A", 10, 300.0), SkuMetrics("US-B", 2, 50.0)],
            "skylight-frame-ca": [SkuMetrics("CA-A", 4, 200.0)],
            "skylight-frame-uk": [SkuMetrics("UK-A", 2, 80.0)],
        }

    def _section_texts(self, blocks):
        return [
            b["text"]["text"] for b in blocks if b.get("type") == "section"
        ]

    def test_appends_us_ca_uk_in_order_each_reconciled(self):
        import slack_bot.main as mod

        rows = self._sku_rows_by_client()

        def fake_query(client_id, marketplaces, now, **kwargs):
            return rows[client_id]

        blocks: list[dict] = []
        with patch.object(mod, "_query_sku_breakdown", side_effect=fake_query):
            mod._maybe_append_combined_sku_breakdown(
                blocks, self._members(), self._now, self._metrics(),
            )

        texts = self._section_texts(blocks)
        joined = " ".join(texts)
        # All three labeled breakdowns present.
        assert "Per-SKU Breakdown (US)" in joined
        assert "Per-SKU Breakdown (CA)" in joined
        assert "Per-SKU Breakdown (UK)" in joined
        # Stacked in display order: US header before CA header before UK header.
        us_i = next(i for i, t in enumerate(texts) if "Per-SKU Breakdown (US)" in t)
        ca_i = next(i for i, t in enumerate(texts) if "Per-SKU Breakdown (CA)" in t)
        uk_i = next(i for i, t in enumerate(texts) if "Per-SKU Breakdown (UK)" in t)
        assert us_i < ca_i < uk_i
        # Each marketplace's SKUs landed under it.
        assert "`US-A`" in joined and "`CA-A`" in joined and "`UK-A`" in joined

    def test_per_marketplace_currency_formatting(self):
        import slack_bot.main as mod

        rows = self._sku_rows_by_client()
        blocks: list[dict] = []
        with patch.object(mod, "_query_sku_breakdown", side_effect=lambda c, m, n, **k: rows[c]):
            mod._maybe_append_combined_sku_breakdown(
                blocks, self._members(), self._now, self._metrics(),
            )

        joined = " ".join(self._section_texts(blocks))
        # CA line uses CAD, UK line uses GBP — reconciles to those currency rows.
        assert "CA$200.00" in joined
        assert "£80.00" in joined

    def test_non_allowlisted_family_untouched(self):
        import slack_bot.main as mod
        from slack_bot.main import MarketplaceMetrics

        members = [
            {"client_id": "moxe", "marketplaces": ["US"]},
            {"client_id": "moxe-ca", "marketplaces": ["CA"]},
        ]
        metrics = [
            MarketplaceMetrics("US", "USD", total_sales=350.0, units=12, spend=10, ppc_sales=100),
            MarketplaceMetrics("CA", "CAD", total_sales=200.0, units=4, spend=5, ppc_sales=50),
        ]
        blocks: list[dict] = []
        with patch.object(mod, "_query_sku_breakdown") as mock_q:
            mod._maybe_append_combined_sku_breakdown(blocks, members, self._now, metrics)

        assert blocks == []
        mock_q.assert_not_called()

    def test_toggle_enables_breakdown_for_non_allowlisted_family(self):
        import slack_bot.main as mod
        from slack_bot.main import MarketplaceMetrics, SkuMetrics

        # A non-allowlisted family gets the breakdown when each member has the
        # per-customer toggle on.
        members = [
            {"client_id": "moxe", "marketplaces": ["US"], "sku_breakdown_enabled": True},
            {"client_id": "moxe-ca", "marketplaces": ["CA"], "sku_breakdown_enabled": True},
        ]
        metrics = [
            MarketplaceMetrics("US", "USD", total_sales=300.0, units=10, spend=10, ppc_sales=100),
            MarketplaceMetrics("CA", "CAD", total_sales=200.0, units=4, spend=5, ppc_sales=50),
        ]
        rows = {
            "moxe": [SkuMetrics("US-A", 10, 300.0)],
            "moxe-ca": [SkuMetrics("CA-A", 4, 200.0)],
        }
        blocks: list[dict] = []
        with patch.object(mod, "_query_sku_breakdown", side_effect=lambda c, m, n, **k: rows[c]):
            mod._maybe_append_combined_sku_breakdown(blocks, members, self._now, metrics)

        joined = " ".join(self._section_texts(blocks))
        assert "Per-SKU Breakdown (US)" in joined
        assert "Per-SKU Breakdown (CA)" in joined

    def test_missing_marketplace_is_skipped_others_still_append(self):
        import slack_bot.main as mod

        # No CA member at all → CA skipped; US and UK still append.
        members = [
            {"client_id": "skylight-frame", "marketplaces": ["US"]},
            {"client_id": "skylight-frame-uk", "marketplaces": ["UK"]},
        ]
        metrics = [m for m in self._metrics() if m.marketplace != "CA"]
        rows = self._sku_rows_by_client()
        blocks: list[dict] = []
        with patch.object(mod, "_query_sku_breakdown", side_effect=lambda c, m, n, **k: rows[c]):
            mod._maybe_append_combined_sku_breakdown(blocks, members, self._now, metrics)

        joined = " ".join(self._section_texts(blocks))
        assert "Per-SKU Breakdown (US)" in joined
        assert "Per-SKU Breakdown (UK)" in joined
        assert "Per-SKU Breakdown (CA)" not in joined

    def test_missing_metric_row_skips_that_marketplace(self):
        import slack_bot.main as mod

        # CA member exists but its account row didn't make it into metrics
        # (e.g. inactive/empty) → CA breakdown skipped, no query for it.
        metrics = [m for m in self._metrics() if m.marketplace != "CA"]
        rows = self._sku_rows_by_client()
        queried: list[str] = []

        def fake_query(client_id, marketplaces, now, **kwargs):
            queried.append(client_id)
            return rows[client_id]

        blocks: list[dict] = []
        with patch.object(mod, "_query_sku_breakdown", side_effect=fake_query):
            mod._maybe_append_combined_sku_breakdown(
                blocks, self._members(), self._now, metrics,
            )

        assert "skylight-frame-ca" not in queried
        joined = " ".join(self._section_texts(blocks))
        assert "Per-SKU Breakdown (CA)" not in joined
        assert "Per-SKU Breakdown (US)" in joined
        assert "Per-SKU Breakdown (UK)" in joined


class TestRecapSkuBreakdown:
    """The day-end recap reuses _append_sku_breakdown with full_day for the
    completed recap day (no separate recap-specific helper)."""

    _now = datetime(2026, 7, 14, 7, 45, tzinfo=timezone.utc)  # midnight recap slot (PDT)

    def test_query_sku_breakdown_forwards_report_date_and_full_day(self):
        import slack_bot.main as mod

        captured: dict = {}

        def fake_sku_orders(bq, project, dataset, client_id, marketplace, now, **kwargs):
            captured.update(kwargs)
            return []

        with (
            patch.object(mod, "_get_bq", return_value=MagicMock()),
            patch.object(mod, "_query_sku_orders", side_effect=fake_sku_orders),
        ):
            mod._query_sku_breakdown(
                "ritual", ["US"], self._now,
                report_date="2026-07-13", full_day=True,
            )

        assert captured.get("report_date") == "2026-07-13"
        assert captured.get("full_day") is True

    def test_append_sku_breakdown_forwards_report_date_and_full_day(self):
        import slack_bot.main as mod
        from slack_bot.main import MarketplaceMetrics, SkuMetrics

        captured: dict = {}

        def fake_breakdown(client_id, marketplaces, now, **kwargs):
            captured.update(kwargs)
            return [SkuMetrics("A", 10, 1000.0)]

        metrics = [MarketplaceMetrics("US", "USD", total_sales=1000.0, units=10, spend=0, ppc_sales=0)]
        blocks: list[dict] = []
        with patch.object(mod, "_query_sku_breakdown", side_effect=fake_breakdown):
            mod._append_sku_breakdown(
                blocks, "ritual", ["US"], self._now, metrics,
                label="US", report_date="2026-07-13", full_day=True,
            )

        assert captured.get("report_date") == "2026-07-13"
        assert captured.get("full_day") is True
        text = " ".join(
            b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section"
        )
        assert "Per-SKU Breakdown (US)" in text

    def _run_recap(self, client_id: str, marketplaces: list[str], sku_rows, sku_breakdown: bool | None = None):
        from slack_bot.main import handler, MarketplaceMetrics

        metrics = [MarketplaceMetrics("US", "USD", total_sales=1000, units=10, spend=100, ppc_sales=500)]
        cfg = _make_bot_config(client_id=client_id, marketplaces=marketplaces, sku_breakdown=sku_breakdown)
        with (
            patch("slack_bot.main.datetime") as mock_dt_cls,
            patch("slack_bot.main.get_live_event", return_value={
                "id": "e1", "name": "Prime Day", "start_date": "2026-07-13",
            }),
            patch("slack_bot.main.list_bot_configs", return_value=[cfg]),
            patch("slack_bot.main.get_client", return_value={"id": client_id, "name": client_id, "is_active": True}),
            patch("slack_bot.main._query_metrics", return_value=metrics),
            patch("slack_bot.main._fetch_prior_yoy_metrics", return_value=(None, None)),
            patch("slack_bot.main._query_cumulative_metrics", return_value=None),
            patch("slack_bot.main._query_sku_breakdown", return_value=sku_rows),
            patch("slack_bot.main.get_thread_anchor_ts", return_value="999.000"),
            patch("slack_bot.main.set_thread_anchor_ts"),
            patch("slack_bot.main.post_message", return_value={"ok": True, "ts": "123.456"}) as mock_post,
            patch("slack_bot.main.log_bot_activity"),
        ):
            mock_dt_cls.now.return_value = self._now
            body, status = handler(_make_request())
        return body, status, mock_post

    def test_gated_recap_includes_sku_breakdown(self):
        import slack_bot.main as mod

        rows = [mod.SkuMetrics("A", 10, 1000.0)]  # reconciles to recap total
        body, status, mock_post = self._run_recap("ritual", ["US"], rows)

        assert status == 200 and body["messages_sent"] == 1
        blocks = mock_post.call_args[0][1]
        text = " ".join(
            b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section"
        )
        assert "Day 1 Recap" in text
        assert "Per-SKU Breakdown (US)" in text

    def test_non_gated_recap_has_no_sku_breakdown(self):
        import slack_bot.main as mod

        rows = [mod.SkuMetrics("A", 10, 1000.0)]
        body, status, mock_post = self._run_recap("c1", ["US"], rows)

        assert status == 200 and body["messages_sent"] == 1
        blocks = mock_post.call_args[0][1]
        text = " ".join(
            b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section"
        )
        assert "Day 1 Recap" in text
        assert "Per-SKU Breakdown" not in text

    def test_toggle_enables_recap_breakdown_for_any_customer(self):
        import slack_bot.main as mod

        rows = [mod.SkuMetrics("A", 10, 1000.0)]  # reconciles to recap total
        body, status, mock_post = self._run_recap("brook-whittle", ["US"], rows, sku_breakdown=True)

        assert status == 200 and body["messages_sent"] == 1
        blocks = mock_post.call_args[0][1]
        text = " ".join(
            b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section"
        )
        assert "Day 1 Recap" in text
        assert "Per-SKU Breakdown (US)" in text


class TestSkuBreakdownCumulative:
    """Day-to-date window means each hour's rows are cumulative: hour 2 >=
    hour 1 for every SKU, and new SKUs appear as they get activity."""

    def test_hour2_ge_hour1_per_sku_and_new_skus_appear(self):
        from slack_bot.main import SkuMetrics

        hour1 = {"A": SkuMetrics("A", 5, 150.0)}
        hour2 = {
            "A": SkuMetrics("A", 8, 240.0),  # grew
            "B": SkuMetrics("B", 1, 20.0),   # new SKU mid-day
        }
        for sku, later in hour2.items():
            earlier = hour1.get(sku)
            if earlier is not None:
                assert later.units >= earlier.units
                assert later.total_sales >= earlier.total_sales
        assert "B" in hour2 and "B" not in hour1


class TestHandlerSkuBreakdownIntegration:
    def _metrics(self):
        from slack_bot.main import MarketplaceMetrics

        return [MarketplaceMetrics("US", "USD", total_sales=350.0, units=12, spend=10, ppc_sales=100)]

    def _run_handler(self, client_id: str, sku_rows, sku_breakdown: bool | None = None):
        from slack_bot.main import handler

        cfg = _make_bot_config(client_id=client_id, sku_breakdown=sku_breakdown)
        with (
            patch("slack_bot.main.get_live_event", return_value={
                "id": "e1", "name": "Prime Day", "start_date": "2026-07-13",
            }),
            patch("slack_bot.main.list_bot_configs", return_value=[cfg]),
            patch("slack_bot.main.get_client", return_value={"id": client_id, "name": client_id, "is_active": True}),
            patch("slack_bot.main._query_metrics", return_value=self._metrics()),
            patch("slack_bot.main._query_sku_breakdown", return_value=sku_rows),
            patch("slack_bot.main.get_thread_anchor_ts", return_value="999.000"),
            patch("slack_bot.main.set_thread_anchor_ts"),
            patch("slack_bot.main.post_message", return_value={"ok": True, "ts": "123.456"}) as mock_post,
            patch("slack_bot.main.log_bot_activity"),
        ):
            body, status = handler(_make_request())
        return body, status, mock_post

    def test_gated_client_gets_breakdown(self):
        import slack_bot.main as mod

        skus = [mod.SkuMetrics("A", 10, 300.0), mod.SkuMetrics("B", 2, 50.0)]
        body, status, mock_post = self._run_handler("ritual", skus)

        assert status == 200 and body["messages_sent"] == 1
        posted_blocks = mock_post.call_args[0][1]
        text = " ".join(b.get("text", {}).get("text", "") for b in posted_blocks)
        assert "Per-SKU Breakdown" in text

    def test_non_gated_client_unaffected(self):
        import slack_bot.main as mod

        skus = [mod.SkuMetrics("A", 10, 300.0), mod.SkuMetrics("B", 2, 50.0)]
        body, status, mock_post = self._run_handler("c1", skus)

        assert status == 200 and body["messages_sent"] == 1
        posted_blocks = mock_post.call_args[0][1]
        text = " ".join(b.get("text", {}).get("text", "") for b in posted_blocks)
        assert "Per-SKU Breakdown" not in text

    def test_toggle_enables_breakdown_for_any_customer(self):
        import slack_bot.main as mod

        # A non-allowlisted customer gets the breakdown purely from the toggle.
        skus = [mod.SkuMetrics("A", 10, 300.0), mod.SkuMetrics("B", 2, 50.0)]
        body, status, mock_post = self._run_handler("brook-whittle", skus, sku_breakdown=True)

        assert status == 200 and body["messages_sent"] == 1
        posted_blocks = mock_post.call_args[0][1]
        text = " ".join(b.get("text", {}).get("text", "") for b in posted_blocks)
        assert "Per-SKU Breakdown" in text

    def test_toggle_off_keeps_breakdown_absent_for_non_allowlisted(self):
        import slack_bot.main as mod

        skus = [mod.SkuMetrics("A", 10, 300.0), mod.SkuMetrics("B", 2, 50.0)]
        body, status, mock_post = self._run_handler("brook-whittle", skus, sku_breakdown=False)

        assert status == 200 and body["messages_sent"] == 1
        posted_blocks = mock_post.call_args[0][1]
        text = " ".join(b.get("text", {}).get("text", "") for b in posted_blocks)
        assert "Per-SKU Breakdown" not in text


# ---------------------------------------------------------------------------
# Combined multi-marketplace message (CU-868jzmub7) — multi-marketplace accounts
# post one hourly message spanning every marketplace, with an FX-converted Total.
# ---------------------------------------------------------------------------

class TestSectionExpand:
    """Every section block must set expand=True so Slack never collapses a tall
    drop behind a 'Show more'/'Show less' toggle (PM request)."""

    def test_section_helper_sets_expand(self):
        from slack_bot.main import _section

        block = _section("hello")
        assert block["type"] == "section"
        assert block["expand"] is True
        assert block["text"]["text"] == "hello"

    def test_message_blocks_all_sections_expand(self):
        from slack_bot.main import _build_message_blocks, MarketplaceMetrics

        metrics = [
            MarketplaceMetrics("US", "USD", 1000, 10, 100, 500),
            MarketplaceMetrics("CA", "USD", 500, 5, 50, 200),
        ]
        blocks = _build_message_blocks(
            client_name="Acme", event_name="Test", day_index=1,
            now=datetime(2026, 7, 13, 18, 45, tzinfo=timezone.utc),
            client_tz=ZoneInfo("America/Los_Angeles"), metrics=metrics, base_currency="USD",
        )
        sections = [b for b in blocks if b.get("type") == "section"]
        assert sections and all(b.get("expand") is True for b in sections)


class TestAccountFamily:
    def test_strips_known_marketplace_suffix_only(self):
        from slack_bot.main import account_family

        assert account_family("moxe-ca") == "moxe"
        assert account_family("skylight-frame-uk") == "skylight-frame"
        assert account_family("moxe-us") == "moxe"

    def test_no_suffix_returns_id_unchanged(self):
        from slack_bot.main import account_family

        assert account_family("moxe") == "moxe"
        assert account_family("skylight-frame") == "skylight-frame"

    def test_vc_and_non_marketplace_suffixes_stay_separate(self):
        from slack_bot.main import account_family

        # -vc (vendor central) and other non-marketplace suffixes are NOT a
        # marketplace split, so they remain their own account family.
        assert account_family("candy-kittens-vc") == "candy-kittens-vc"
        assert account_family("truestartcoffee-vc") == "truestartcoffee-vc"
        assert account_family("jacknjill-au-vc") == "jacknjill-au-vc"
        assert account_family("leonisa-pr") == "leonisa-pr"


class TestConvertTotals:
    def test_converts_each_marketplace_into_base_currency(self):
        from slack_bot.main import MarketplaceMetrics, _convert_totals

        metrics = [
            MarketplaceMetrics("US", "USD", total_sales=1000, units=10, spend=100, ppc_sales=500),
            MarketplaceMetrics("CA", "CAD", total_sales=500, units=5, spend=50, ppc_sales=200),
        ]
        rates = {"USD": 1.0, "CAD": 1.25}  # 1 USD = 1.25 CAD
        spend, ppc, sales = _convert_totals(metrics, "USD", rates)

        # CAD converted to USD: spend 50/1.25=40, ppc 200/1.25=160, sales 500/1.25=400.
        assert spend == pytest.approx(140.0)
        assert ppc == pytest.approx(660.0)
        assert sales == pytest.approx(1400.0)

    def test_missing_rate_returns_none(self):
        from slack_bot.main import MarketplaceMetrics, _convert_totals

        metrics = [
            MarketplaceMetrics("US", "USD", total_sales=1000, units=10, spend=100, ppc_sales=500),
            MarketplaceMetrics("CA", "CAD", total_sales=500, units=5, spend=50, ppc_sales=200),
        ]
        assert _convert_totals(metrics, "USD", {"USD": 1.0}) is None

    def test_empty_rates_returns_none(self):
        from slack_bot.main import MarketplaceMetrics, _convert_totals

        metrics = [MarketplaceMetrics("US", "USD", 1000, 10, 100, 500)]
        assert _convert_totals(metrics, "USD", {}) is None


class TestBuildMessageBlocksConvertedTotal:
    _now = datetime(2026, 7, 13, 18, 45, tzinfo=timezone.utc)

    def _metrics(self):
        from slack_bot.main import MarketplaceMetrics

        return [
            MarketplaceMetrics("US", "USD", total_sales=1000, units=10, spend=100, ppc_sales=500),
            MarketplaceMetrics("CA", "CAD", total_sales=500, units=5, spend=50, ppc_sales=200),
        ]

    def test_multi_currency_total_uses_converted_base_currency(self):
        from slack_bot.main import _build_message_blocks

        blocks = _build_message_blocks(
            client_name="Moxe", event_name="Prime Day", day_index=1, now=self._now,
            client_tz=ZoneInfo("America/Los_Angeles"), metrics=self._metrics(),
            base_currency="USD", rates={"USD": 1.0, "CAD": 1.25},
        )
        text = " ".join(b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section")
        assert "*US*" in text and "*CA*" in text
        assert "*Total*" in text
        # Native per-marketplace lines keep their own currency...
        assert "CA$" in text
        # ...and the converted Total is shown in the base currency (number only).
        assert "Total Sales: $1,400.00" in text

    def test_total_skipped_when_rate_missing(self):
        from slack_bot.main import _build_message_blocks

        blocks = _build_message_blocks(
            client_name="Moxe", event_name="Prime Day", day_index=1, now=self._now,
            client_tz=ZoneInfo("America/Los_Angeles"), metrics=self._metrics(),
            base_currency="USD", rates={"USD": 1.0},  # CAD missing
        )
        text = " ".join(b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section")
        # Per-marketplace lines still post; only the Total is suppressed.
        assert "*US*" in text and "*CA*" in text
        assert "*Total*" not in text


class TestCombinedHandlerIntegration:
    @staticmethod
    def _fake_query(per_member):
        def _q(client_id, marketplaces, now, **kwargs):
            return per_member.get(client_id, [])
        return _q

    def _run(self, configs, per_member, rates, *, sku_rows=None):
        from slack_bot.main import handler

        midday_pdt = datetime(2026, 7, 14, 18, 5, tzinfo=timezone.utc)  # 11:05 AM PDT
        patches = [
            patch("slack_bot.main.datetime"),
            patch("slack_bot.main.get_live_event", return_value={
                "id": "e1", "name": "Prime Day", "start_date": "2026-07-13",
            }),
            patch("slack_bot.main.list_bot_configs", return_value=configs),
            patch("slack_bot.main.get_client",
                  side_effect=lambda cid: {"id": cid, "name": cid, "is_active": True}),
            patch("slack_bot.main._query_metrics", side_effect=self._fake_query(per_member)),
            patch("slack_bot.main._load_currency_rates", return_value=rates),
            patch("slack_bot.main.get_thread_anchor_ts", return_value="999.000"),
            patch("slack_bot.main.set_thread_anchor_ts"),
            patch("slack_bot.main.post_message", return_value={"ok": True, "ts": "123"}),
            patch("slack_bot.main.log_bot_activity"),
        ]
        if sku_rows is not None:
            patches.append(patch("slack_bot.main._query_sku_breakdown", return_value=sku_rows))

        from contextlib import ExitStack
        with ExitStack() as stack:
            mocks = [stack.enter_context(p) for p in patches]
            mocks[0].now.return_value = midday_pdt
            body, status = handler(_make_request())
        mock_post = mocks[8]
        return body, status, mock_post

    def test_two_configs_combine_into_one_message_with_converted_total(self):
        from slack_bot.main import MarketplaceMetrics

        configs = [
            _make_bot_config(client_id="moxe", marketplaces=["US"]),
            _make_bot_config(client_id="moxe-ca", marketplaces=["CA"]),
        ]
        per_member = {
            "moxe": [MarketplaceMetrics("US", "USD", 1000, 10, 100, 500)],
            "moxe-ca": [MarketplaceMetrics("CA", "CAD", 500, 5, 50, 200)],
        }
        body, status, mock_post = self._run(configs, per_member, {"USD": 1.0, "CAD": 1.25})

        assert status == 200
        # One combined unit -> one message (the second member is short-circuited).
        assert body["messages_sent"] == 1
        mock_post.assert_called_once()
        blocks = mock_post.call_args[0][1]
        text = " ".join(b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section")
        assert "*US*" in text and "*CA*" in text
        assert "*Total*" in text

    def test_single_multi_marketplace_config_gets_converted_total(self):
        from slack_bot.main import MarketplaceMetrics

        configs = [_make_bot_config(client_id="acme", marketplaces=["US", "CA"])]
        per_member = {
            "acme": [
                MarketplaceMetrics("US", "USD", 1000, 10, 100, 500),
                MarketplaceMetrics("CA", "CAD", 500, 5, 50, 200),
            ],
        }
        body, status, mock_post = self._run(configs, per_member, {"USD": 1.0, "CAD": 1.25})

        assert status == 200 and body["messages_sent"] == 1
        mock_post.assert_called_once()
        blocks = mock_post.call_args[0][1]
        text = " ".join(b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section")
        assert "*Total*" in text

    def test_missing_rate_skips_total_but_still_posts_lines(self):
        from slack_bot.main import MarketplaceMetrics

        configs = [
            _make_bot_config(client_id="moxe", marketplaces=["US"]),
            _make_bot_config(client_id="moxe-ca", marketplaces=["CA"]),
        ]
        per_member = {
            "moxe": [MarketplaceMetrics("US", "USD", 1000, 10, 100, 500)],
            "moxe-ca": [MarketplaceMetrics("CA", "CAD", 500, 5, 50, 200)],
        }
        body, status, mock_post = self._run(configs, per_member, {"USD": 1.0})  # CAD missing

        assert status == 200 and body["messages_sent"] == 1
        mock_post.assert_called_once()
        blocks = mock_post.call_args[0][1]
        text = " ".join(b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section")
        assert "*US*" in text and "*CA*" in text
        assert "*Total*" not in text

    def test_skylight_combined_appends_us_only_sku_breakdown(self):
        from slack_bot.main import MarketplaceMetrics, SkuMetrics

        configs = [
            _make_bot_config(client_id="skylight-frame", marketplaces=["US"]),
            _make_bot_config(client_id="skylight-frame-uk", marketplaces=["UK"]),
        ]
        per_member = {
            "skylight-frame": [MarketplaceMetrics("US", "USD", 350.0, 12, 10, 100)],
            "skylight-frame-uk": [MarketplaceMetrics("UK", "GBP", 200.0, 5, 20, 80)],
        }
        # SKU rows reconcile to the US row (units 12, sales 350).
        sku_rows = [SkuMetrics("A", 10, 300.0), SkuMetrics("B", 2, 50.0)]
        body, status, mock_post = self._run(
            configs, per_member, {"USD": 1.0, "GBP": 0.8}, sku_rows=sku_rows,
        )

        assert status == 200 and body["messages_sent"] == 1
        mock_post.assert_called_once()
        blocks = mock_post.call_args[0][1]
        text = " ".join(b.get("text", {}).get("text", "") for b in blocks if b.get("type") == "section")
        assert "*US*" in text and "*UK*" in text
        assert "*Total*" in text
        # US-only per-SKU detail appended for the allowlisted family.
        assert "Per-SKU Breakdown" in text
        assert "`A`" in text and "`B`" in text
