# app/core/month_utils.py
#
# Purpose: calendar-month helpers shared by the summary endpoint and the
# Budget Planning Agent. A month is always the string 'YYYY-MM' and is turned
# into a half-open UTC range [start, end), so filtering with
# start <= date < end never double-counts midnight on the 1st.

import re
from datetime import datetime, timezone

# Used by the API (Query(pattern=...)) to reject '2026-13' or '26-09' with a 422
MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
_MONTH_RE = re.compile(MONTH_PATTERN)


def month_bounds(month: str) -> tuple[datetime, datetime]:
    """
    Converts 'YYYY-MM' into a (start, end) pair of UTC datetimes.
    'start' is inclusive and 'end' is exclusive. Raises ValueError for a bad month.
    """
    if not isinstance(month, str) or not _MONTH_RE.match(month):
        raise ValueError(f"month must look like YYYY-MM, got {month!r}")
    year, mon = (int(part) for part in month.split("-"))
    start = datetime(year, mon, 1, tzinfo=timezone.utc)
    # December rolls over into January of the next year
    end = datetime(year + (mon == 12), (mon % 12) + 1, 1, tzinfo=timezone.utc)
    return start, end


def shift_month(start: datetime, delta_months: int) -> datetime:
    """Returns the first day of the month that is `delta_months` away from `start`."""
    # Counting months since year 0 turns the calendar into a plain integer
    total_months = start.year * 12 + (start.month - 1) + delta_months
    return datetime(total_months // 12, total_months % 12 + 1, 1, tzinfo=timezone.utc)


def lookback_window(month: str, months: int) -> tuple[datetime, datetime]:
    """
    The `months` FULL months before `month`, as [start, end).
    lookback_window('2026-10', 3) covers July, August and September 2026;
    the target month itself is never part of its own history.
    """
    if months < 1:
        raise ValueError("lookback must be at least 1 month")
    target_start, _ = month_bounds(month)
    return shift_month(target_start, -months), target_start


def month_key(moment: datetime) -> str:
    """
    The 'YYYY-MM' label of a datetime, read in UTC (the same clock month_bounds
    uses). Naive datetimes (SQLite in the tests) are taken as UTC already.
    """
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return f"{moment.year:04d}-{moment.month:02d}"
