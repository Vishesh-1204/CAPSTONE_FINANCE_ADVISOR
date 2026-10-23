# tests/test_forecast_variable.py
#
# Variable-spending forecast: model choice, fallback, and the horizon interval (pure).

import pytest

from app.services.forecasting.variable_forecast import forecast_variable

OCT_NOV = [9 / 31, 21 / 30]           # 30 days from 22 October


def test_short_history_uses_the_moving_average_and_is_low_confidence():
    result = forecast_variable([5000, 6000, 5500, 6500], OCT_NOV, min_folds=2)
    assert result.model_name == "moving_average_3" and not result.selected_by_backtest
    assert result.scores == [] and result.low_confidence
    assert result.monthly == [6000.0, 6000.0]
    assert result.point == pytest.approx(6000 * (9 / 31 + 21 / 30))
    assert (result.lower, result.upper) == pytest.approx((result.point * 0.75, result.point * 1.25))


def test_one_month_of_history_still_forecasts():
    result = forecast_variable([4000], [1.0])
    assert result.point == 4000 and result.low_confidence


def test_enough_history_selects_by_backtest_with_a_residual_interval():
    series = [5000, 5200, 4800, 5100, 4900, 5300, 5000, 5100, 4950]
    result = forecast_variable(series, OCT_NOV, min_folds=2, min_interval_errors=3)
    assert result.selected_by_backtest and not result.low_confidence
    assert {s["model_name"] for s in result.scores} == {"naive_last", "moving_average_3", "exp_smoothing"}
    assert result.lower <= result.point <= result.upper
    assert result.upper - result.lower < result.point * 0.5        # tighter than the +/-25% fallback


def test_a_trend_is_followed_by_the_selected_model():
    rising = [3000 + 400 * i for i in range(10)]
    result = forecast_variable(rising, [1.0])
    assert result.model_name in {"naive_last", "exp_smoothing"}
    assert result.monthly[0] > sum(rising[-3:]) / 3                  # above the 3-month average


def test_empty_series_is_rejected():
    with pytest.raises(ValueError):
        forecast_variable([], [1.0])
