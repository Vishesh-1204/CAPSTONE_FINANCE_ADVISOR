# app/services/forecasting/horizon.py
#
# Purpose: turn a forecast window in DAYS into calendar MONTHS with weights.
# The models forecast whole months, but "the next 30 days from 22 October" is
# 9 days of October and 21 days of November. Each month gets
#     weight = days of the window inside that month / days in that month
# so the horizon total is  sum(month forecast x weight)  (Oct x 9/31 + Nov x 21/30).
#
# Months between the end of the history and the window start (statements that
# lag behind today) are included with weight 0: the model still has to step
# over them, but they do not count toward the total.

import calendar
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class MonthSlice:
    """One calendar month touched (or stepped over) by the forecast."""
    first_day: date             # always the 1st of the month
    days_in_month: int
    days_in_window: int         # 0 for a month before the window starts

    @property
    def key(self) -> str:
        """'YYYY-MM', the same label core/month_utils uses."""
        return f"{self.first_day.year:04d}-{self.first_day.month:02d}"

    @property
    def weight(self) -> float:
        return self.days_in_window / self.days_in_month


def month_start(day: date) -> date:
    return day.replace(day=1)


def next_month(first_day: date) -> date:
    """The 1st of the month after `first_day`'s month."""
    return date(first_day.year + (first_day.month == 12), first_day.month % 12 + 1, 1)


def horizon_slices(first_month: date, start: date, end: date) -> list[MonthSlice]:
    """
    Every calendar month from `first_month` (the month right after the history
    ends) up to `end`'s month, with the number of window days [start, end] in each.
    Raises ValueError when the window is empty or starts before `first_month`.
    """
    if end < start:
        raise ValueError("forecast window is empty")
    if start < month_start(first_month):
        raise ValueError("the window must not start inside the history")
    slices: list[MonthSlice] = []
    current = month_start(first_month)
    while current <= end:
        days = calendar.monthrange(current.year, current.month)[1]
        month_end = current.replace(day=days)
        overlap_start, overlap_end = max(start, current), min(end, month_end)
        in_window = max(0, (overlap_end - overlap_start).days + 1)
        slices.append(MonthSlice(current, days, in_window))
        current = next_month(current)
    return slices
