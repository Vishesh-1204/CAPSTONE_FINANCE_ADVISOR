# tests/test_cashflow_projector.py
#
# The daily balance trajectory, its band, and the cash-crunch date.

from datetime import date

import pytest

from app.services.forecasting.cashflow_projector import ScheduledFlow, project_cashflow
from app.services.forecasting.horizon import horizon_slices

NOV_START, NOV_END = date(2026, 11, 1), date(2026, 11, 30)
NOVEMBER = horizon_slices(NOV_START, NOV_START, NOV_END)           # one full month, weight 1


def _project(opening=10_000.0, variable=3_000.0, lower=2_000.0, upper=4_500.0, scheduled=()):
    return project_cashflow(NOV_START, NOV_END, opening, NOVEMBER, [variable], lower, upper, scheduled)


def test_no_flows_spends_the_variable_forecast_evenly():
    projection = _project()
    assert len(projection.trajectory) == 30
    assert projection.trajectory[0].balance == pytest.approx(10_000 - 100)        # 3,000 / 30 days
    assert projection.end.balance == pytest.approx(7_000)
    assert projection.variable_point == pytest.approx(3_000)


def test_scheduled_flows_land_on_their_dates():
    projection = _project(scheduled=[
        ScheduledFlow(date(2026, 11, 1), 50_000, "credit"),     # salary
        ScheduledFlow(date(2026, 11, 5), 18_000, "debit"),      # rent
        ScheduledFlow(date(2026, 12, 5), 18_000, "debit"),      # outside the window: ignored
    ])
    by_day = {p.on: p.balance for p in projection.trajectory}
    assert by_day[date(2026, 11, 4)] == pytest.approx(10_000 + 50_000 - 400)
    assert by_day[date(2026, 11, 5)] == pytest.approx(10_000 + 50_000 - 18_000 - 500)
    assert projection.scheduled_in == 50_000 and projection.scheduled_out == 18_000
    assert projection.end.balance == pytest.approx(10_000 + 50_000 - 18_000 - 3_000)


def test_band_starts_narrow_and_ends_at_the_full_interval():
    projection = _project()
    first, last = projection.trajectory[0], projection.end
    assert first.upper - first.lower == pytest.approx((4_500 - 2_000) / 30)
    # More spending (upper 4,500) -> LOWER balance; less spending (2,000) -> UPPER balance
    assert last.lower == pytest.approx(10_000 - 4_500)
    assert last.upper == pytest.approx(10_000 - 2_000)


def test_cash_crunch_is_the_first_day_the_lower_band_is_negative():
    # No band (upper = point): balance on day k is 1,000 - 100k, then a 2,000 bill on the 10th
    no_band = _project(opening=1_000, upper=3_000, scheduled=[ScheduledFlow(date(2026, 11, 10), 2_000, "debit")])
    assert no_band.trajectory[8].lower == pytest.approx(100)    # 9 November still fine
    assert no_band.cash_crunch_date == date(2026, 11, 10)


def test_the_band_brings_the_cash_crunch_earlier():
    # Upper spending 4,500 adds 50/day to the downside: lower = 1,000 - 150k < 0 from day 7
    assert _project(opening=1_000).cash_crunch_date == date(2026, 11, 7)


def test_no_cash_crunch_when_the_lower_band_stays_positive():
    assert _project(opening=100_000).cash_crunch_date is None


def test_partial_first_month_is_prorated():
    start, end = date(2026, 10, 23), date(2026, 11, 21)        # 30 days from 22 October
    slices = horizon_slices(date(2026, 10, 1), start, end)
    projection = project_cashflow(start, end, 0.0, slices, [3_100, 3_000], 0, 10_000, [])
    assert projection.variable_point == pytest.approx(3_100 * 9 / 31 + 3_000 * 21 / 30)   # 900 + 2,100
    assert projection.trajectory[0].balance == pytest.approx(-100)                          # 3,100 / 31
    assert projection.end.balance == pytest.approx(-3_000)


def test_zero_weight_months_do_not_add_spending():
    start, end = date(2026, 11, 23), date(2026, 11, 30)
    slices = horizon_slices(date(2026, 10, 1), start, end)                          # October: weight 0
    projection = project_cashflow(start, end, 0.0, slices, [99_999, 3_000], 0, 1_000, [])
    assert projection.variable_point == pytest.approx(3_000 * 8 / 30)


def test_mismatched_forecasts_are_rejected():
    with pytest.raises(ValueError):
        project_cashflow(NOV_START, NOV_END, 0.0, NOVEMBER, [1.0, 2.0], 0, 0, [])
