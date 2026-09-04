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
        # Inclusive of both boundary days: end lands on today-1 (Mar 19) and the
        # start day (today-7) is fully included, so the window opens on Mar 12.
        assert start == date(2026, 3, 12)
        assert end == date(2026, 3, 19)

    def test_rolling_window_same_day(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 3, 20)
        start, end = compute_date_range(
            "US", {"strategy": "rolling_window", "start_offset": -1, "end_offset": -1}, now
        )
        # Both boundaries name today-1 (Mar 19); the window is inclusive of its
        # start day, so it spans Mar 18–19.
        assert start == date(2026, 3, 18)
        assert end == date(2026, 3, 19)

    def test_rolling_window_single_unit_offset_is_one_day_shift(self):
        """A one-unit change to either offset must move the date exactly one day."""
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 3, 20)

        def rng(so, eo):
            return compute_date_range(
                "US", {"strategy": "rolling_window", "start_offset": so, "end_offset": eo}, now
            )

        s0, _ = rng(-7, -1)
        s1, _ = rng(-6, -1)  # start one day later
        s2, _ = rng(-8, -1)  # start one day earlier
        assert (s1 - s0).days == 1
        assert (s0 - s2).days == 1
        _, e0 = rng(-7, -1)
        _, e1 = rng(-7, -2)  # end one day earlier
        assert (e0 - e1).days == 1

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

    # -- custom_range ----------------------------------------------------------

    def test_custom_range_basic(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 5, 21)
        start, end = compute_date_range(
            "US",
            {"strategy": "custom_range", "start_date": "2026-01-01", "end_date": "2026-01-15"},
            now,
        )
        assert start == date(2026, 1, 1)
        assert end == date(2026, 1, 15)

    def test_custom_range_single_day(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 5, 21)
        start, end = compute_date_range(
            "US",
            {"strategy": "custom_range", "start_date": "2026-01-01", "end_date": "2026-01-01"},
            now,
        )
        assert start == date(2026, 1, 1)
        assert end == date(2026, 1, 1)

    def test_custom_range_ignores_today(self):
        """The range is fixed regardless of when 'today' falls."""
        from shared.schedule_compute import compute_date_range

        tf = {"strategy": "custom_range", "start_date": "2025-06-01", "end_date": "2025-06-30"}
        early = compute_date_range("US", tf, self._utc(2026, 1, 1))
        late = compute_date_range("US", tf, self._utc(2027, 12, 31))
        assert early == late == (date(2025, 6, 1), date(2025, 6, 30))

    # -- unknown strategy fallback -------------------------------------------

    def test_unknown_strategy_falls_back_to_yesterday(self):
        from shared.schedule_compute import compute_date_range

        now = self._utc(2026, 3, 20)
        start, end = compute_date_range("US", {"strategy": "something_new"}, now)
        assert start == date(2026, 3, 19)
        assert end == date(2026, 3, 19)

    # -- timezone sensitivity ------------------------------------------------

    def test_sg_marketplace_timezone(self):
        """SG is Asia/Singapore (UTC+8). At UTC 20:00 Mar 19, SG local time is Mar 20 04:00."""
        from shared.schedule_compute import compute_date_range

        now = datetime(2026, 3, 19, 20, 0, tzinfo=timezone.utc)
        start, end = compute_date_range("SG", {"strategy": "yesterday"}, now)
        assert start == date(2026, 3, 19)  # SG "today" is Mar 20, yesterday = Mar 19

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
# compute_date_range — anchor_tz override
# ---------------------------------------------------------------------------

class TestComputeDateRangeAnchorTz:

    def test_anchor_tz_overrides_marketplace_today(self):
        """With anchor_tz, 'today' is resolved in that zone, not the marketplace's."""
        from shared.schedule_compute import compute_date_range

        # 6am PHT on Jun 11 == 22:00 UTC Jun 10. In Asia/Manila (UTC+8) the
        # calendar date is Jun 11; in America/Los_Angeles it's still Jun 10.
        now = datetime(2026, 6, 10, 22, 0, tzinfo=timezone.utc)
        manila = ZoneInfo("Asia/Manila")

        anchored = compute_date_range(
            "US", {"strategy": "last_n_days", "days": 30}, now, anchor_tz=manila
        )
        marketplace = compute_date_range("US", {"strategy": "last_n_days", "days": 30}, now)

        # Anchored to Manila (Jun 11) → end Jun 10; marketplace-local (Jun 10) → end Jun 9
        assert anchored[1] == date(2026, 6, 10)
        assert marketplace[1] == date(2026, 6, 9)


# ---------------------------------------------------------------------------
# compute_sales_traffic_date_range — consistent, lagged end date
# ---------------------------------------------------------------------------

class TestComputeSalesTrafficDateRange:

    # 6am PHT on Jun 11 2026 == 22:00 UTC Jun 10 — the incident's run instant.
    _RUN_6AM_PHT = datetime(2026, 6, 10, 22, 0, tzinfo=timezone.utc)

    def test_end_date_is_consistent_across_marketplaces(self):
        """Every marketplace must get the SAME end date for the same run."""
        from shared.schedule_compute import compute_sales_traffic_date_range

        tf = {"strategy": "last_n_days", "days": 30, "end_offset_days": 0}
        ends = {
            mkt: compute_sales_traffic_date_range(mkt, tf, self._RUN_6AM_PHT)[1]
            for mkt in ["US", "CA", "MX", "UK", "DE", "FR", "AU", "SG"]
        }
        assert len(set(ends.values())) == 1, f"end dates diverged by marketplace: {ends}"

    def test_zero_delay_end_is_d_minus_2_from_run_date(self):
        """0-day delay → end date is D-2 from the operations (PHT) run date.

        Regression for the incident: a 6am-PHT Jun-11 run must end at Jun 9, and
        no account may land a day short (Jun 8) or a day ahead (Jun 10).
        """
        from shared.schedule_compute import compute_sales_traffic_date_range

        tf = {"strategy": "last_n_days", "days": 30, "end_offset_days": 0}
        for mkt in ["US", "CA", "MX", "UK", "DE", "AU", "SG"]:
            start, end = compute_sales_traffic_date_range(mkt, tf, self._RUN_6AM_PHT)
            assert end == date(2026, 6, 9), f"{mkt} ended at {end}, expected 2026-06-09"
            assert start == date(2026, 5, 11), f"{mkt} started at {start}"
            assert (end - start).days == 29  # 30 inclusive days

    def test_explicit_delay_composes_with_data_lag(self):
        from shared.schedule_compute import compute_sales_traffic_date_range

        tf = {"strategy": "last_n_days", "days": 30, "end_offset_days": 3}
        # end = ops_today(Jun 11) - 1 (yesterday) - 3 (delay) - 1 (S&T lag) = Jun 6
        start, end = compute_sales_traffic_date_range("US", tf, self._RUN_6AM_PHT)
        assert end == date(2026, 6, 6)

    def test_yesterday_strategy_is_lagged_and_consistent(self):
        from shared.schedule_compute import compute_sales_traffic_date_range

        us = compute_sales_traffic_date_range("US", {"strategy": "yesterday"}, self._RUN_6AM_PHT)
        de = compute_sales_traffic_date_range("DE", {"strategy": "yesterday"}, self._RUN_6AM_PHT)
        # yesterday end = ops_today(Jun 11) - 1 - 1 (lag) = Jun 9, single day, all marketplaces
        assert us == (date(2026, 6, 9), date(2026, 6, 9))
        assert de == us

    def test_midday_run_applies_data_lag(self):
        """Away from the day boundary, S&T still lags one day behind a normal report."""
        from shared.schedule_compute import compute_date_range, compute_sales_traffic_date_range

        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)
        tf = {"strategy": "last_n_days", "days": 30}
        _, st_end = compute_sales_traffic_date_range("US", tf, now)
        _, normal_end = compute_date_range("US", tf, now)
        assert st_end == normal_end - timedelta(days=1)

    def test_calendar_month_falls_back_to_marketplace_range(self):
        """Fixed calendar periods must not be shifted by the S&T data lag."""
        from shared.schedule_compute import compute_date_range, compute_sales_traffic_date_range

        now = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
        tf = {"strategy": "last_calendar_month"}
        assert (
            compute_sales_traffic_date_range("US", tf, now)
            == compute_date_range("US", tf, now)
        )

    def test_calendar_week_falls_back_to_marketplace_range(self):
        from shared.schedule_compute import compute_date_range, compute_sales_traffic_date_range

        now = datetime(2026, 3, 20, 12, 0, tzinfo=timezone.utc)
        tf = {"strategy": "last_calendar_week", "week_start": 0}
        assert (
            compute_sales_traffic_date_range("US", tf, now)
            == compute_date_range("US", tf, now)
        )

    def test_custom_range_falls_back_to_marketplace_range(self):
        """A fixed absolute range must not be shifted by the S&T data lag."""
        from shared.schedule_compute import compute_date_range, compute_sales_traffic_date_range

        now = datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc)
        tf = {"strategy": "custom_range", "start_date": "2026-01-01", "end_date": "2026-01-15"}
        assert (
            compute_sales_traffic_date_range("US", tf, now)
            == compute_date_range("US", tf, now)
            == (date(2026, 1, 1), date(2026, 1, 15))
        )


# ---------------------------------------------------------------------------
# BR last-year (LY) comparison windows — CU-868kdyjc1
#
# The BR "LY30D" / "LY N30D" schedules pull Sales & Traffic over a rolling
# window ~a year in the past for year-over-year comparison. Two bugs made the
# resolved window a day short / shifted:
#   1. rolling_window dropped its first day (start boundary off by one).
#   2. the Sales & Traffic trailing-data lag was wrongly applied to these
#      historical windows, shifting the whole range back a day.
# Both the plain and the Sales & Traffic resolution paths must now produce the
# exact windows the ops tracking sheet expects, with the stored offsets
# unchanged, and adjacent offsets must move the range by exactly one day.
# ---------------------------------------------------------------------------

class TestBenchmarkLastYearWindows:

    # A midday run so both the marketplace clock and the ops clock read
    # 2026-07-20 (the ticket's fixed run date).
    _RUN_2026_07_20 = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)

    def test_ly30d_resolves_to_intended_30_day_window(self):
        """AC1: offsets (-394, -366) → 2025-06-20…2025-07-19 (30 days)."""
        from shared.schedule_compute import (
            compute_date_range,
            compute_sales_traffic_date_range,
        )

        tf = {"strategy": "rolling_window", "start_offset": -394, "end_offset": -366}
        expected = (date(2025, 6, 20), date(2025, 7, 19))
        assert compute_date_range("US", tf, self._RUN_2026_07_20) == expected
        # These schedules are Sales & Traffic — the actual production path.
        assert compute_sales_traffic_date_range("US", tf, self._RUN_2026_07_20) == expected
        start, end = expected
        assert (end - start).days + 1 == 30

    def test_ly_n30d_regression_still_correct(self):
        """AC3: offsets (-364, -336) → 2025-07-20…2025-08-18 (30 days), unchanged."""
        from shared.schedule_compute import (
            compute_date_range,
            compute_sales_traffic_date_range,
        )

        tf = {"strategy": "rolling_window", "start_offset": -364, "end_offset": -336}
        expected = (date(2025, 7, 20), date(2025, 8, 18))
        assert compute_date_range("US", tf, self._RUN_2026_07_20) == expected
        assert compute_sales_traffic_date_range("US", tf, self._RUN_2026_07_20) == expected
        start, end = expected
        assert (end - start).days + 1 == 30

    def test_single_unit_start_offset_shift_is_one_clean_day(self):
        """AC2: -395/-394/-393 produce start dates one clean day apart, no skips."""
        from shared.schedule_compute import compute_sales_traffic_date_range

        starts = {
            off: compute_sales_traffic_date_range(
                "US",
                {"strategy": "rolling_window", "start_offset": off, "end_offset": -366},
                self._RUN_2026_07_20,
            )[0]
            for off in (-395, -394, -393)
        }
        assert starts[-395] == date(2025, 6, 19)
        assert starts[-394] == date(2025, 6, 20)  # the previously-skipped target day
        assert starts[-393] == date(2025, 6, 21)

    def test_ly_window_not_shifted_by_sales_traffic_data_lag(self):
        """Historical rolling windows must match the plain marketplace range."""
        from shared.schedule_compute import (
            compute_date_range,
            compute_sales_traffic_date_range,
        )

        tf = {"strategy": "rolling_window", "start_offset": -394, "end_offset": -366}
        assert (
            compute_sales_traffic_date_range("US", tf, self._RUN_2026_07_20)
            == compute_date_range("US", tf, self._RUN_2026_07_20)
        )


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

    def test_sg_marketplace_utc_offset(self):
        from shared.schedule_compute import compute_report_dates

        result = compute_report_dates("SG", "sp_api", date(2026, 3, 19))
        # SG is UTC+8 (no DST), midnight = 16:00 UTC previous day
        assert result["dataStartTime"] == "2026-03-18T16:00:00Z"
        assert result["dataEndTime"] == "2026-03-19T16:00:00Z"
