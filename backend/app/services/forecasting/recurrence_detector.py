# app/services/forecasting/recurrence_detector.py
#
# Purpose: find money that repeats on a schedule (salary, rent, subscriptions, SIPs).
# These are NOT forecast statistically. They are projected exactly on their dates,
# which makes the cash-flow forecast explainable ("rent of 18,000 leaves on the 5th").
# Only the leftover "variable" spending goes through the statistical models.
#
# Adapted from the spec appendix:
#   * `recent_occurrences`: the amount test and the typical amount/day use only the
#     latest N payments, so a salary hike does not make the salary "irregular" forever.
#     The timing test (25-35 days apart) still uses every payment.
#   * is_active / is_occurrence_of: staleness and row matching, used by the service.
#   * project_occurrences skips a date too close to the last payment (an early
#     salary on the 30th must not be counted again on the 1st).

from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Iterable, Iterator
import calendar


@dataclass(frozen=True)
class Event:
    """One historical transaction, reduced to what recurrence detection needs."""
    payee_key: str
    on: date
    amount: float          # always positive; `direction` tells the sign
    direction: str         # 'debit' or 'credit'


@dataclass(frozen=True)
class RecurringItem:
    """A payment that repeats about monthly."""
    payee_key: str
    direction: str
    typical_amount: float
    typical_day: int        # day of month it usually lands on
    occurrences: int
    last_seen: date


def detect_recurring(
    events: Iterable[Event], min_occurrences: int = 3, amount_tolerance: float = 0.15,
    min_gap_days: int = 25, max_gap_days: int = 35, recent_occurrences: int | None = None,
) -> list[RecurringItem]:
    """
    A payee counts as monthly-recurring when it appears at least `min_occurrences` times,
    consecutive payments are 25-35 days apart (median), and each amount is within
    `amount_tolerance` of the median amount. Variable bills (e.g. a changing electricity
    bill) fail the amount test on purpose, and stay in the statistical variable bucket.
    With `recent_occurrences=N`, the amount test and the typical amount/day look only at
    the latest N payments (the CURRENT level of a salary that was raised).
    """
    groups: dict[tuple[str, str], list[Event]] = {}
    for event in events:
        groups.setdefault((event.payee_key, event.direction), []).append(event)

    items: list[RecurringItem] = []
    for (payee_key, direction), group in groups.items():
        if len(group) < min_occurrences:
            continue
        group = sorted(group, key=lambda e: e.on)
        gaps = [(b.on - a.on).days for a, b in zip(group, group[1:])]
        if not min_gap_days <= median(gaps) <= max_gap_days:
            continue
        recent = group[-recent_occurrences:] if recent_occurrences else group
        typical = median(e.amount for e in recent)
        if typical <= 0 or any(abs(e.amount - typical) / typical > amount_tolerance for e in recent):
            continue
        items.append(RecurringItem(
            payee_key=payee_key, direction=direction, typical_amount=round(typical, 2),
            typical_day=int(median(e.on.day for e in recent)), occurrences=len(group),
            last_seen=group[-1].on,
        ))
    return sorted(items, key=lambda item: (item.direction, -item.typical_amount))


def is_active(item: RecurringItem, reference: date, stale_days: int = 45) -> bool:
    """
    False when the item has not been seen for more than `stale_days` before `reference`
    (a cancelled subscription, a finished loan). Stale items are shown but not projected.
    `reference` should be the date of the user's latest data, not today: if statements
    stop in September, a September rent is still "recent".
    """
    return (reference - item.last_seen).days <= stale_days


def is_occurrence_of(item: RecurringItem, amount: float, amount_tolerance: float = 0.15) -> bool:
    """
    True when a payment to the item's payee is one of its regular payments (amount
    close to typical). A one-off deposit to the same landlord is NOT, so it stays in
    variable spending instead of silently disappearing.
    """
    return item.typical_amount > 0 and abs(amount - item.typical_amount) / item.typical_amount <= amount_tolerance


def project_occurrences(
    item: RecurringItem, start: date, end: date, min_days_after_last: int = 15
) -> Iterator[date]:
    """
    Yields each future date (start <= date <= end) the item is expected on.
    A day that does not exist in a short month (e.g. the 31st in April) moves to month-end.
    A date fewer than `min_days_after_last` days after the last payment is skipped
    (that month was already paid, early).
    """
    year, month = start.year, start.month
    while date(year, month, 1) <= end:
        day = min(item.typical_day, calendar.monthrange(year, month)[1])
        occurrence = date(year, month, day)
        if start <= occurrence <= end and (occurrence - item.last_seen).days >= min_days_after_last:
            yield occurrence
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
