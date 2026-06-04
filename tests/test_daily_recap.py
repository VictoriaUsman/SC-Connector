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

        # Queried the previous full calendar day in the client's timezone.
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
