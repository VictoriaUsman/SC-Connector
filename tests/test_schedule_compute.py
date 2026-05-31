"""Tests for shared.schedule_compute — date range computation and report date conversion."""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))

os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("ENVIRONMENT", "staging")


# ---------------------------------------------------------------------------
# compute_date_range — strategy tests
# ---------------------------------------------------------------------------

class TestComputeDateRange:

    def _utc(self, year: int, month: int, day: int, hour: int = 12) -> datetime:
        return datetime(year, month, day, hour, 0, tzinfo=timezone.utc)

    # -- yesterday (default) -------------------------------------------------

    def test_yesterday_default(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 3, 20)
        start, end = compute_date_range("US", {"strategy": "yesterday"}, now)
        # US is America/Los_Angeles, UTC 12:00 Mar 20 → local Mar 20 04:00
        assert start == date(2026, 3, 19)
        assert end == date(2026, 3, 19)

    def test_yesterday_is_default_for_empty_timeframe(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 3, 20)
        start, end = compute_date_range("US", {}, now)
        assert start == date(2026, 3, 19)

    # -- today ---------------------------------------------------------------

    def test_today_strategy(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 3, 20)
        start, end = compute_date_range("US", {"strategy": "today"}, now)
        assert start == date(2026, 3, 20)
        assert end == date(2026, 3, 20)

    # -- last_n_days ---------------------------------------------------------

    def test_last_n_days_no_delay(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 3, 20)
        start, end = compute_date_range("US", {"strategy": "last_n_days", "days": 30}, now)
        assert end == date(2026, 3, 19)
        assert start == date(2026, 2, 18)
        assert (end - start).days == 29  # 30 inclusive days

    def test_last_n_days_with_delay(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 3, 20)
        start, end = compute_date_range(
            "US", {"strategy": "last_n_days", "days": 30, "end_offset_days": 3}, now
        )
        assert end == date(2026, 3, 16)
        assert start == date(2026, 2, 15)

    def test_last_n_days_single_day(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 3, 20)
        start, end = compute_date_range("US", {"strategy": "last_n_days", "days": 1}, now)
        assert start == end == date(2026, 3, 19)

    # -- rolling_window ------------------------------------------------------

    def test_rolling_window(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 3, 20)
        start, end = compute_date_range(
            "US", {"strategy": "rolling_window", "start_offset": -7, "end_offset": -1}, now
        )
        assert start == date(2026, 3, 13)
        assert end == date(2026, 3, 19)

    def test_rolling_window_same_day(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 3, 20)
        start, end = compute_date_range(
            "US", {"strategy": "rolling_window", "start_offset": -1, "end_offset": -1}, now
        )
        assert start == end == date(2026, 3, 19)

    # -- last_calendar_week --------------------------------------------------

    def test_last_calendar_week_monday_start(self):
        from shared.schedule_compute import compute_date_range

        # 2026-03-20 is a Friday
        now = self._utc(2026, 3, 20)
        start, end = compute_date_range(
            "US", {"strategy": "last_calendar_week", "week_start": 0}, now
        )
        assert start == date(2026, 3, 9)   # previous Monday
        assert end == date(2026, 3, 15)     # previous Sunday
        assert start.weekday() == 0        # Monday
        assert end.weekday() == 6          # Sunday

    def test_last_calendar_week_thursday_start(self):
        from shared.schedule_compute import compute_date_range

        # 2026-03-20 is a Friday. Week starts Thu. Current week started Thu Mar 19.
        now = self._utc(2026, 3, 20)
        start, end = compute_date_range(
            "US", {"strategy": "last_calendar_week", "week_start": 3}, now
        )
        # Current week starts Thu Mar 19 → previous week: Thu Mar 12 – Wed Mar 18
        assert start == date(2026, 3, 12)
        assert end == date(2026, 3, 18)
        assert start.weekday() == 3        # Thursday
        assert end.weekday() == 2          # Wednesday

    def test_last_calendar_week_default_monday(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 3, 20)
        start1, end1 = compute_date_range(
            "US", {"strategy": "last_calendar_week"}, now
        )
        start2, end2 = compute_date_range(
            "US", {"strategy": "last_calendar_week", "week_start": 0}, now
        )
        assert (start1, end1) == (start2, end2)

    # -- last_calendar_month -------------------------------------------------

    def test_last_calendar_month(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 3, 15)
        start, end = compute_date_range("US", {"strategy": "last_calendar_month"}, now)
        assert start == date(2026, 2, 1)
        assert end == date(2026, 2, 28)

    def test_last_calendar_month_january(self):
        """Running in January should pull December of the previous year."""
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 1, 10)
        start, end = compute_date_range("US", {"strategy": "last_calendar_month"}, now)
        assert start == date(2025, 12, 1)
        assert end == date(2025, 12, 31)

    def test_last_calendar_month_leap_year(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2028, 3, 5)  # 2028 is a leap year
        start, end = compute_date_range("US", {"strategy": "last_calendar_month"}, now)
        assert start == date(2028, 2, 1)
        assert end == date(2028, 2, 29)

    # -- prior_year_window ----------------------------------------------------

    def test_prior_year_window_basic(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 5, 21)
        start, end = compute_date_range(
            "US",
            {"strategy": "prior_year_window", "days_before": 30, "days_after": 30},
            now,
        )
        # Anchor = May 21, 2025; range = Apr 21 – Jun 20, 2025
        assert start == date(2025, 4, 21)
        assert end == date(2025, 6, 20)

    def test_prior_year_window_two_years_back(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 5, 21)
        start, end = compute_date_range(
            "US",
            {"strategy": "prior_year_window", "days_before": 30, "days_after": 30, "years_back": 2},
            now,
        )
        # Anchor = May 21, 2024; range = Apr 21 – Jun 20, 2024
        assert start == date(2024, 4, 21)
        assert end == date(2024, 6, 20)

    def test_prior_year_window_leap_year_fallback(self):
        """Feb 29 in a leap year → shifted to non-leap year falls back to Feb 28."""
        from shared.schedule_compute import compute_date_range

        # 2028 is a leap year, Feb 29 exists
        now = self._utc(2028, 2, 29)
        start, end = compute_date_range(
            "US",
            {"strategy": "prior_year_window", "days_before": 5, "days_after": 5, "years_back": 1},
            now,
        )
        # 2027 is not a leap year, anchor falls back to Feb 28
        assert start == date(2027, 2, 23)
        assert end == date(2027, 3, 5)

    def test_prior_year_window_zero_days_after(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 5, 21)
        start, end = compute_date_range(
            "US",
            {"strategy": "prior_year_window", "days_before": 30, "days_after": 0},
            now,
        )
        assert start == date(2025, 4, 21)
        assert end == date(2025, 5, 21)  # anchor itself

    def test_prior_year_window_defaults(self):
        """Without explicit days_before/days_after, defaults to 30/30."""
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 5, 21)
        start, end = compute_date_range("US", {"strategy": "prior_year_window"}, now)
        assert start == date(2025, 4, 21)
        assert end == date(2025, 6, 20)

    def test_prior_year_window_anchor_offset(self):
        """anchor_offset_days shifts the anchor back before applying year shift."""
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 5, 25)
        start, end = compute_date_range(
            "US",
            {"strategy": "prior_year_window", "days_before": 30, "days_after": 0, "anchor_offset_days": 2},
            now,
        )
        # ref_date = May 25 - 2 = May 23, 2026; anchor = May 23, 2025
        # range = Apr 23, 2025 – May 23, 2025
        assert start == date(2025, 4, 23)
        assert end == date(2025, 5, 23)

    def test_prior_year_window_anchor_offset_zero_is_noop(self):
        """anchor_offset_days=0 is the same as not providing it."""
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 5, 25)
        no_offset = compute_date_range(
            "US",
            {"strategy": "prior_year_window", "days_before": 30, "days_after": 30},
            now,
        )
        zero_offset = compute_date_range(
            "US",
            {"strategy": "prior_year_window", "days_before": 30, "days_after": 30, "anchor_offset_days": 0},
            now,
        )
        assert no_offset == zero_offset

    # -- unknown strategy fallback -------------------------------------------

    def test_unknown_strategy_falls_back_to_yesterday(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 3, 20)
        start, end = compute_date_range("US", {"strategy": "something_new"}, now)
        assert start == date(2026, 3, 19)
        assert end == date(2026, 3, 19)

    # -- timezone sensitivity ------------------------------------------------

    def test_jp_marketplace_timezone(self):
        """JP is Asia/Tokyo (UTC+9). At UTC 20:00 Mar 19, JP local time is Mar 20 05:00."""
        from shared.schedule_compute import compute_date_range

        now = datetime(2026, 3, 19, 20, 0, tzinfo=timezone.utc)
        start, end = compute_date_range("JP", {"strategy": "yesterday"}, now)
        assert start == date(2026, 3, 19)  # JP "today" is Mar 20, yesterday = Mar 19

    def test_us_dst_boundary(self):
        """US DST starts second Sunday of March. Test near that boundary."""
        from shared.schedule_compute import compute_date_range

        # 2026-03-08 is DST start. At UTC 09:00, PST is still Mar 7 at 1am (PST, not PDT yet).
        # Actually on Mar 8 2026 at 2am PST, clocks spring forward.
        # At UTC 10:01 on Mar 8, PDT would be 3:01am Mar 8.
        now = datetime(2026, 3, 8, 10, 1, tzinfo=timezone.utc)
        start, end = compute_date_range("US", {"strategy": "yesterday"}, now)
        assert start == date(2026, 3, 7)


# ---------------------------------------------------------------------------
# compute_report_dates — single day and range
# ---------------------------------------------------------------------------

class TestComputeReportDates:

    def test_sp_api_single_day(self):
        from shared.schedule_compute import compute_report_dates

        result = compute_report_dates("US", "sp_api", date(2026, 3, 19))
        assert result["dataStartTime"] == "2026-03-19T07:00:00Z"  # PDT midnight
        assert result["dataEndTime"] == "2026-03-20T07:00:00Z"

    def test_sp_api_range(self):
        from shared.schedule_compute import compute_report_dates

        result = compute_report_dates("US", "sp_api", date(2026, 3, 1), date(2026, 3, 31))
        assert result["dataStartTime"] == "2026-03-01T08:00:00Z"  # PST before DST
        assert result["dataEndTime"] == "2026-04-01T07:00:00Z"    # PDT after DST

    def test_sp_api_backward_compat_no_end(self):
        from shared.schedule_compute import compute_report_dates

        single = compute_report_dates("US", "sp_api", date(2026, 3, 19))
        explicit = compute_report_dates("US", "sp_api", date(2026, 3, 19), date(2026, 3, 19))
        assert single == explicit

    def test_ads_api_single_day(self):
        from shared.schedule_compute import compute_report_dates

        result = compute_report_dates("US", "ads_api", date(2026, 3, 19))
        assert result == {"startDate": "2026-03-19", "endDate": "2026-03-19"}

    def test_ads_api_range(self):
        from shared.schedule_compute import compute_report_dates

        result = compute_report_dates("US", "ads_api", date(2026, 3, 1), date(2026, 3, 31))
        assert result == {"startDate": "2026-03-01", "endDate": "2026-03-31"}

    def test_jp_marketplace_utc_offset(self):
        from shared.schedule_compute import compute_report_dates

        result = compute_report_dates("JP", "sp_api", date(2026, 3, 19))
        # JP is UTC+9, midnight = 15:00 UTC previous day
        assert result["dataStartTime"] == "2026-03-18T15:00:00Z"
        assert result["dataEndTime"] == "2026-03-19T15:00:00Z"
