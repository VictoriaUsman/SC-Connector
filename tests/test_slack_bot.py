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

    def test_hourly_still_filters_purchases_since_midnight(self):
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
            patch("slack_bot.main.post_message", return_value={"ok": True, "ts": "123.456"}) as mock_post,
            patch("slack_bot.main.log_bot_activity"),
        ):
            body, status = handler(_make_request())

        assert status == 200
        assert body["messages_sent"] == 1
        mock_post.assert_called_once()

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
        fallback = mock_post.call_args[0][2]
        assert "Day 1 Recap" in fallback
        blocks = mock_post.call_args[0][1]
        assert "Day 1 Recap" in blocks[0]["text"]["text"]

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
