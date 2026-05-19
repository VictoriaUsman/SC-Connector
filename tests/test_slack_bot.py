"""Tests for the hourly Slack bot function — metrics, message formatting, and delivery."""

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
