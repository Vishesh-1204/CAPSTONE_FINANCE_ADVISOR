# tests/test_month_utils.py
#
# Unit tests for the 'YYYY-MM' helpers shared by the summary and budget code.

import re
from datetime import datetime, timezone

import pytest

from app.core.month_utils import MONTH_PATTERN, lookback_window, month_bounds, month_key, shift_month


def test_month_bounds_regular_month():
    start, end = month_bounds("2026-10")
    assert start == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert end == datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_month_bounds_december_rolls_into_next_year():
    _, end = month_bounds("2026-12")
    assert end == datetime(2027, 1, 1, tzinfo=timezone.utc)


def test_shift_month_crosses_year_boundary():
    start, _ = month_bounds("2026-02")
    assert shift_month(start, -3) == datetime(2025, 11, 1, tzinfo=timezone.utc)


def test_lookback_window_is_the_full_months_before_the_target():
    start, end = lookback_window("2026-02", 3)
    assert start == datetime(2025, 11, 1, tzinfo=timezone.utc)    # Nov, Dec, Jan
    assert end == datetime(2026, 2, 1, tzinfo=timezone.utc)       # the target month is excluded


def test_lookback_must_be_positive():
    with pytest.raises(ValueError):
        lookback_window("2026-02", 0)


@pytest.mark.parametrize("bad", ["2026-13", "2026-00", "26-09", "2026-9", "2026/09", ""])
def test_bad_months_are_rejected(bad):
    assert not re.match(MONTH_PATTERN, bad)
    with pytest.raises(ValueError):
        month_bounds(bad)


def test_month_key_works_for_aware_and_naive_datetimes():
    assert month_key(datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc)) == "2026-09"
    assert month_key(datetime(2026, 1, 1)) == "2026-01"
