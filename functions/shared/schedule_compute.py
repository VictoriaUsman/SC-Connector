"""Schedule computation — timezone-aware report dates and flexible next_run_at logic."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from shared.config import MARKETPLACE_TIMEZONES

VALID_TIMEFRAME_STRATEGIES = {
    "yesterday",
    "today",
    "last_n_days",
    "rolling_window",
    "last_calendar_week",
    "last_calendar_month",
}


def get_marketplace_tz(marketplace: str) -> ZoneInfo:
    tz_name = MARKETPLACE_TIMEZONES.get(marketplace, "America/Los_Angeles")
    return ZoneInfo(tz_name)


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
) -> tuple[date, date]:
    """Resolve a timeframe config dict into (start_date, end_date) in marketplace local time.

    Strategies:
      yesterday         — T-1 to T-1 (single day, default)
      today             — T-0 to T-0 (for hourly/intraday)
      last_n_days       — trailing N days with optional end_offset_days
      rolling_window    — explicit start_offset / end_offset from today
      last_calendar_week — most recent completed week, configurable week_start
      last_calendar_month — first to last day of previous calendar month
    """
    strategy = timeframe.get("strategy", "yesterday")
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
        return today + timedelta(days=start_offset), today + timedelta(days=end_offset)

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

    d = today - timedelta(days=1)
    return d, d


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
