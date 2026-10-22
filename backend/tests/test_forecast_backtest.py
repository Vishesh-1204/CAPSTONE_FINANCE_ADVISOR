# tests/test_forecast_backtest.py
#
# Rolling-origin backtest, model selection, and prediction intervals
# (including the horizon-SUM interval used for 30/60/90-day totals).

import pytest

from app.services.forecasting.backtest import (
    horizon_sum_errors, prediction_interval, rolling_origin_backtest, select_best_model, weighted_sum,
)
from app.services.forecasting.forecast_models import default_model_registry, naive_last


def test_backtest_never_peeks_at_the_future():
    seen_lengths: list[int] = []

    def spy(history, horizon):
        seen_lengths.append(len(history))
        return [history[-1]] * horizon

    series = [1, 2, 3, 4, 5, 6]
    rolling_origin_backtest(series, spy, "spy", horizon=1, min_train=3)
    assert seen_lengths == [3, 4, 5]          # origins 3, 4, 5; each sees only the past


def test_backtest_returns_none_for_a_too_short_series():
    assert rolling_origin_backtest([1, 2], naive_last, "naive", horizon=1, min_train=3) is None


def test_best_model_wins_on_a_trending_series():
    series = [1000 + 100 * i for i in range(10)]          # steady rise
    selection = select_best_model(series, default_model_registry(), horizon=1)
    assert selection is not None
    assert selection.best.model_name in {"naive_last", "exp_smoothing"}   # follow the rise, not the old average
    ma = next(r for r in selection.all_results if r.model_name == "moving_average_3")
    assert selection.best.score < ma.score


def test_naive_loses_on_a_noisy_flat_series():
    series = [1000, 1400, 800, 1300, 900, 1200, 1000, 1300]
    selection = select_best_model(series, default_model_registry(), horizon=1)
    assert selection.best.model_name in {"moving_average_3", "exp_smoothing"}   # naive chases the noise


def test_seasonal_model_is_not_tried_without_enough_history():
    series = [1000.0] * 8
    selection = select_best_model(series, default_model_registry(), horizon=1)
    assert "seasonal_naive_12" not in {r.model_name for r in selection.all_results}


def test_no_selection_when_history_is_too_short_for_any_model():
    assert select_best_model([1000.0, 1100.0], default_model_registry(), horizon=1) is None


def test_min_folds_blocks_a_selection_decided_by_one_month():
    series = [1000.0, 1100.0, 1050.0, 1200.0]                           # 4 months -> only 1 one-step fold
    assert select_best_model(series, default_model_registry(), horizon=1, min_folds=1) is not None
    assert select_best_model(series, default_model_registry(), horizon=1, min_folds=2) is None


def test_interval_comes_from_past_errors_and_brackets_the_point():
    lower, upper, low_conf = prediction_interval(1000, [-100, -50, 0, 50, 100, 150], coverage=0.8)
    assert not low_conf
    assert lower < 1000 < upper


def test_interval_is_flagged_low_confidence_with_few_residuals():
    lower, upper, low_conf = prediction_interval(1000, [10], fallback_relative_width=0.25)
    assert low_conf and (lower, upper) == (750.0, 1250.0)


def test_interval_lower_bound_never_goes_negative():
    lower, _, _ = prediction_interval(100, [-500, -400, -300, 10, 20], coverage=0.9)
    assert lower == 0.0


# --- horizon-sum interval ---

def test_weighted_sum_and_length_check():
    assert weighted_sum([3100, 3000], [9 / 31, 21 / 30]) == pytest.approx(900 + 2100)
    with pytest.raises(ValueError):
        weighted_sum([1, 2], [1])


def test_horizon_sum_errors_are_one_weighted_total_per_fold():
    series = [100, 100, 100, 200, 100, 300]
    errors = horizon_sum_errors(series, naive_last, weights=[0.5, 1.0], min_train=3)
    # Folds at origins 3 and 4 (origin 5 would need a 7th month):
    #   origin 3: forecast 100, actual [200, 100] -> 0.5*100 + 1.0*0    = 50
    #   origin 4: forecast 200, actual [100, 300] -> 0.5*-100 + 1.0*100 = 50
    assert errors == pytest.approx([50.0, 50.0])


def test_horizon_sum_errors_ignore_zero_weight_months():
    series = [100, 100, 100, 999, 100]
    # Weight 0 for the first step (a month before the window): the 999 must not matter
    assert horizon_sum_errors(series, naive_last, weights=[0.0, 1.0], min_train=3) == pytest.approx([0.0])


def test_horizon_sum_errors_are_empty_when_the_series_is_too_short():
    assert horizon_sum_errors([100, 100, 100], naive_last, weights=[1.0, 1.0], min_train=3) == []


def test_horizon_interval_brackets_the_total_and_widens_with_noise():
    calm = [1000, 1020, 990, 1010, 1000, 995, 1005, 1000, 1010, 990]
    noisy = [1000, 1500, 600, 1400, 700, 1300, 800, 1500, 600, 1200]
    weights = [0.3, 1.0, 1.0]
    widths = []
    for series in (calm, noisy):
        errors = horizon_sum_errors(series, naive_last, weights, min_train=3)
        point = weighted_sum([series[-1]] * 3, weights)
        lower, upper, low_conf = prediction_interval(point, errors, coverage=0.8)
        assert not low_conf and lower <= point <= upper
        widths.append(upper - lower)
    assert widths[1] > widths[0] * 5
