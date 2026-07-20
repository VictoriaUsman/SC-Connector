"""Schedule computation — timezone-aware report dates and flexible next_run_at logic."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from shared.config import MARKETPLACE_TIMEZONES, OPERATIONS_TIMEZONE

VALID_TIMEFRAME_STRATEGIES = {
    "yesterday",
    "today",
    "last_n_days",
    "rolling_window",
    "last_calendar_week",
    "last_calendar_month",
    "prior_year_window",
}

# Sales & Traffic is the only daily SP report whose end date must be identical
# across every marketplace in a single scheduler run (ops requirement) and must
# respect Amazon's data-finalization lag. ``SALES_TRAFFIC_DATA_LAG_DAYS`` is the
# extra trailing-day offset applied on top of the normal "yesterday" end so the
# last requested day is always complete: with a 0-day delay this lands the end
# date at D-2 from the operations run date.
SALES_TRAFFIC_REPORT_TYPE = "GET_SALES_AND_TRAFFIC_REPORT"
SALES_TRAFFIC_DATA_LAG_DAYS = 1

# Strategies whose end is a moving "trailing" boundary (i.e. relative to today),
# for which the Sales & Traffic ops-anchoring + data lag is meaningful. Fixed
# calendar-period strategies (last_calendar_week/month) and historical windows
# (rolling_window, prior_year_window) are already aligned and must not be
# shifted — e.g. the BR last-year (LY) rolling windows point ~a year into the
# past, where the data is long finalized, so applying the trailing data lag
# would only shift the intended comparison range by a day.
_SALES_TRAFFIC_TRAILING_STRATEGIES = {"yesterday", "today", "last_n_days"}


def get_marketplace_tz(marketplace: str) -> ZoneInfo:
    tz_name = MARKETPLACE_TIMEZONES.get(marketplace, "America/Los_Angeles")
    return ZoneInfo(tz_name)


def get_operations_tz() -> ZoneInfo:
    """Return the team's operating timezone (used for run-date anchoring)."""
    return ZoneInfo(OPERATIONS_TIMEZONE)


def marketplace_today(marketplace: str, utc_now: datetime | None = None) -> date:
    """The current calendar date in a marketplace's local timezone."""
    if utc_now is None:
        utc_now = datetime.now(timezone.utc)
    return utc_now.astimezone(get_marketplace_tz(marketplace)).date()


def marketplace_yesterday(marketplace: str, utc_now: datetime | None = None) -> date:
    return marketplace_today(marketplace, utc_now) - timedelta(days=1)


def is_marketplace_day_ended(marketplace: str, utc_now: datetime | None = None) -> bool:
    """True if the marketplace's current local time is past midnight (i.e. yesterday is finalized)."""
    if utc_now is None:
        utc_now = datetime.now(timezone.utc)
    local_now = utc_now.astimezone(get_marketplace_tz(marketplace))
    return local_now.hour >= 0


# ---------------------------------------------------------------------------
# Timeframe → date range resolution
# ---------------------------------------------------------------------------

def compute_date_range(
    marketplace: str,
    timeframe: dict,
    utc_now: datetime | None = None,
    *,
    anchor_tz: ZoneInfo | None = None,
) -> tuple[date, date]:
    """Resolve a timeframe config dict into (start_date, end_date) in marketplace local time.

    Strategies:
      yesterday         — T-1 to T-1 (single day, default)
      today             — T-0 to T-0 (for hourly/intraday)
      last_n_days       — trailing N days with optional end_offset_days
      rolling_window    — explicit start_offset / end_offset from today
                          (inclusive of both boundary days)
      last_calendar_week — most recent completed week, configurable week_start
      last_calendar_month — first to last day of previous calendar month
      prior_year_window — window around today's date shifted back N years

    When ``anchor_tz`` is provided, "today" is resolved in that timezone instead
    of the marketplace's local timezone. This is used to compute a single,
    marketplace-independent run date (see ``compute_sales_traffic_date_range``).
    """
    strategy = timeframe.get("strategy", "yesterday")
    if anchor_tz is not None:
        ref = utc_now if utc_now is not None else datetime.now(timezone.utc)
        today = ref.astimezone(anchor_tz).date()
    else:
        today = marketplace_today(marketplace, utc_now)

    if strategy == "yesterday":
        d = today - timedelta(days=1)
        return d, d

    if strategy == "today":
        return today, today

    if strategy == "last_n_days":
        days = timeframe.get("days", 30)
        end_offset = timeframe.get("end_offset_days", 0)
        end = today - timedelta(days=1 + end_offset)
        start = end - timedelta(days=days - 1)
        return start, end

    if strategy == "rolling_window":
        start_offset = timeframe.get("start_offset", -7)
        end_offset = timeframe.get("end_offset", -1)
        # A rolling window is inclusive of BOTH boundary days. ``end_offset``
        # already lands on the last day (``today + end_offset``); the start
        # boundary must include the day it names too. Previously the start was
        # computed as ``today + start_offset``, which dropped the first day and
        # made the resolved window one day short — so the BR last-year (LY)
        # comparison windows came back 29 days starting one day late instead of
        # the intended 30-day span. Anchor the start on the full first day so a
        # single-unit change to either offset is always a clean one-day shift.
        start = today + timedelta(days=start_offset) - timedelta(days=1)
        end = today + timedelta(days=end_offset)
        return start, end

    if strategy == "last_calendar_week":
        week_start_dow = timeframe.get("week_start", 0)  # 0=Mon
        days_since_week_start = (today.weekday() - week_start_dow) % 7
        current_week_start = today - timedelta(days=days_since_week_start)
        end = current_week_start - timedelta(days=1)
        start = end - timedelta(days=6)
        return start, end

    if strategy == "last_calendar_month":
        first_of_this_month = today.replace(day=1)
        last_of_prev = first_of_this_month - timedelta(days=1)
        first_of_prev = last_of_prev.replace(day=1)
        return first_of_prev, last_of_prev

    if strategy == "prior_year_window":
        days_before = timeframe.get("days_before", 30)
        days_after = timeframe.get("days_after", 30)
        years_back = timeframe.get("years_back", 1)
        anchor_offset = timeframe.get("anchor_offset_days", 0)
        ref_date = today - timedelta(days=anchor_offset)
        try:
            anchor = ref_date.replace(year=ref_date.year - years_back)
        except ValueError:
            # Feb 29 in a non-leap year — fall back to Feb 28
            anchor = ref_date.replace(year=ref_date.year - years_back, day=28)
        return anchor - timedelta(days=days_before), anchor + timedelta(days=days_after)

    d = today - timedelta(days=1)
    return d, d


def compute_sales_traffic_date_range(
    marketplace: str,
    timeframe: dict,
    utc_now: datetime | None = None,
) -> tuple[date, date]:
    """Resolve the (start, end) date range for a Sales & Traffic pull.

    Sales & Traffic differs from other reports in two ways that this function
    corrects:

    1. **Consistent end date across marketplaces.** The trailing window's "today"
       is anchored to the fixed operations timezone (OPERATIONS_TIMEZONE) rather
       than each marketplace's local timezone. Without this, a run firing near a
       day boundary (e.g. ~6am PHT, which is still the previous calendar day in
       UTC and in the western marketplaces) resolves to different calendar dates
       per marketplace, so some accounts end up one day short of the others.

    2. **Data-availability lag.** Amazon's Sales & Traffic data for the most
       recent day(s) is not finalized at run time. The trailing end is pulled
       back by ``SALES_TRAFFIC_DATA_LAG_DAYS`` so the last requested day is
       always complete. Combined with the standard "yesterday" end, a 0-day
       delay lands the end date at D-2 from the operations run date.

    Only trailing strategies (yesterday/today/last_n_days) get this treatment.
    Fixed calendar-period and historical strategies (rolling_window,
    last_calendar_week/month, prior_year_window) are already aligned and fall
    back to the standard marketplace-anchored computation.
    """
    strategy = timeframe.get("strategy", "yesterday")
    if strategy not in _SALES_TRAFFIC_TRAILING_STRATEGIES:
        return compute_date_range(marketplace, timeframe, utc_now)

    start, end = compute_date_range(
        marketplace, timeframe, utc_now, anchor_tz=get_operations_tz()
    )
    lag = timedelta(days=SALES_TRAFFIC_DATA_LAG_DAYS)
    return start - lag, end - lag


def compute_report_dates(
    marketplace: str,
    api_source: str,
    report_date: date,
    report_end_date: date | None = None,
) -> dict[str, str]:
    """Convert marketplace date(s) into API-specific date params.

    SP API: dataStartTime/dataEndTime as ISO 8601 UTC timestamps
    representing midnight of start_date to midnight after end_date
    in the marketplace timezone.

    Ads API: startDate/endDate as YYYY-MM-DD strings (the Ads API
    interprets these relative to the profile's timezone automatically).
    """
    if report_end_date is None:
        report_end_date = report_date

    tz = get_marketplace_tz(marketplace)

    if api_source == "sp_api":
        start_local = datetime(report_date.year, report_date.month, report_date.day, tzinfo=tz)
        end_day = report_end_date + timedelta(days=1)
        end_local = datetime(end_day.year, end_day.month, end_day.day, tzinfo=tz)
        return {
            "dataStartTime": start_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "dataEndTime": end_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    else:
        return {
            "startDate": report_date.isoformat(),
            "endDate": report_end_date.isoformat(),
        }


# ---------------------------------------------------------------------------
# Flexible next_run_at computation
# ---------------------------------------------------------------------------

_SIMPLE_DELTAS: dict[str, timedelta] = {
    "hourly": timedelta(hours=1),
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "monthly": timedelta(days=30),
}


def compute_next_run(
    from_time: datetime,
    schedule_config: dict,
) -> datetime:
    """Compute the next run time from a schedule_config dict.

    Supported schedule_config shapes:
      {"type": "daily",   "time": "03:00"}
      {"type": "weekly",  "time": "03:00", "days_of_week": [0, 2, 4]}  (0=Mon)
      {"type": "monthly", "time": "03:00", "day_of_month": 15}
      {"type": "hourly"}

    Falls back to simple delta if 'type' is a legacy frequency string.
    """
    stype = schedule_config.get("type", "daily")
    run_time = schedule_config.get("time", "03:00")

    if stype == "hourly":
        return from_time + timedelta(hours=1)

    hour, minute = (int(p) for p in run_time.split(":"))

    if stype == "daily":
        candidate = from_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= from_time:
            candidate += timedelta(days=1)
        return candidate

    if stype == "weekly":
        days_of_week: list[int] = schedule_config.get("days_of_week", [0])
        if not days_of_week:
            days_of_week = [0]
        current_dow = from_time.weekday()
        sorted_days = sorted(set(days_of_week))

        for d in sorted_days:
            offset = (d - current_dow) % 7
            candidate = from_time.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=offset)
            if candidate > from_time:
                return candidate

        first = sorted_days[0]
        offset = (first - current_dow) % 7 + 7
        return from_time.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=offset)

    if stype == "monthly":
        day_of_month: int = schedule_config.get("day_of_month", 1)
        year, month = from_time.year, from_time.month
        max_day = calendar.monthrange(year, month)[1]
        actual_day = min(day_of_month, max_day)
        candidate = from_time.replace(day=actual_day, hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= from_time:
            month += 1
            if month > 12:
                month = 1
                year += 1
            max_day = calendar.monthrange(year, month)[1]
            actual_day = min(day_of_month, max_day)
            candidate = candidate.replace(year=year, month=month, day=actual_day)
        return candidate

    delta = _SIMPLE_DELTAS.get(stype, timedelta(days=1))
    return from_time + delta
