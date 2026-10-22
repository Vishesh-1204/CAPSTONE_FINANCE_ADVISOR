# tests/test_forecast_models.py
#
# The four simple forecasting models (pure functions, no DB).

import pytest

from app.services.forecasting.forecast_models import (
    default_model_registry, make_moving_average, make_seasonal_naive, naive_last, simple_exp_smoothing,
)


def test_naive_repeats_last_value():
    assert naive_last([10, 20, 30], 3) == [30.0, 30.0, 30.0]


def test_moving_average_uses_only_the_window():
    assert make_moving_average(3)([1, 100, 20, 30, 40], 2) == [30.0, 30.0]


def test_moving_average_with_short_history_averages_what_exists():
    assert make_moving_average(3)([1000, 2000], 1) == [1500.0]


def test_seasonal_naive_repeats_last_season():
    history = list(range(1, 13)) + [20]        # 13 months
    forecast = make_seasonal_naive(12)(history, 3)
    assert forecast == [2.0, 3.0, 4.0]


def test_seasonal_naive_needs_a_full_season():
    with pytest.raises(ValueError):
        make_seasonal_naive(12)([1, 2, 3], 1)


def test_exp_smoothing_on_constant_series_is_that_constant():
    assert simple_exp_smoothing([5000] * 6, 2) == pytest.approx([5000.0, 5000.0])


def test_exp_smoothing_leans_toward_recent_values():
    rising = [1000, 1000, 1000, 1000, 2000, 2000]
    assert 1000 < simple_exp_smoothing(rising, 1)[0] <= 2000


def test_registry_lists_the_candidate_models():
    assert set(default_model_registry()) == {"naive_last", "moving_average_3", "exp_smoothing", "seasonal_naive_12"}
