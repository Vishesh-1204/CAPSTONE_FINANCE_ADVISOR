# tests/test_forecast_horizon.py
#
# Turning "30/60/90 days from as_of" into calendar months with day weights.

from datetime import date, timedelta

import pytest

from app.services.forecasting.horizon import horizon_slices, next_month


def _window(as_of: date, days: int) -> tuple[date, date]:
    return as_of + timedelta(days=1), as_of + timedelta(days=days)


def test_30_days_from_22_october_is_9_october_days_and_21_november_days():
    start, end = _window(date(2026, 10, 22), 30)
    slices = horizon_slices(date(2026, 10, 1), start, end)
    assert [(s.key, s.days_in_window) for s in slices] == [("2026-10", 9), ("2026-11", 21)]
    assert slices[0].weight == pytest.approx(9 / 31) and slices[1].weight == pytest.approx(21 / 30)


@pytest.mark.parametrize("days", [30, 60, 90])
def test_window_days_always_add_up_to_the_horizon(days):
    start, end = _window(date(2026, 10, 22), days)
    assert sum(s.days_in_window for s in horizon_slices(date(2026, 10, 1), start, end)) == days


def test_90_days_touch_four_months_and_cross_the_year():
    start, end = _window(date(2026, 10, 22), 90)
    keys = [s.key for s in horizon_slices(date(2026, 10, 1), start, end)]
    assert keys == ["2026-10", "2026-11", "2026-12", "2027-01"]


def test_as_of_on_month_end_starts_cleanly_next_month():
    start, end = _window(date(2026, 10, 31), 30)
    slices = horizon_slices(date(2026, 11, 1), start, end)
    assert [(s.key, s.days_in_window) for s in slices] == [("2026-11", 30)]
    assert slices[0].weight == 1.0


def test_months_between_history_and_window_get_weight_zero():
    # Statements end in September, forecast made on 22 November
    start, end = _window(date(2026, 11, 22), 30)
    slices = horizon_slices(date(2026, 10, 1), start, end)
    assert [(s.key, s.weight) for s in slices][0] == ("2026-10", 0.0)
    assert [s.key for s in slices] == ["2026-10", "2026-11", "2026-12"]


def test_window_inside_the_history_is_rejected():
    with pytest.raises(ValueError):
        horizon_slices(date(2026, 10, 1), date(2026, 9, 20), date(2026, 10, 20))


def test_next_month_rolls_over_december():
    assert next_month(date(2026, 12, 1)) == date(2027, 1, 1)
