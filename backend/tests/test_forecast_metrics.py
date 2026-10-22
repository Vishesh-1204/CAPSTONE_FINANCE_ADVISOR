# tests/test_forecast_metrics.py
#
# Forecast accuracy metrics (pure functions).

import pytest

from app.services.forecasting.metrics import mae, mape, rmse, smape


def test_mae_and_rmse_on_a_known_example():
    actual, predicted = [100, 200, 300], [110, 190, 330]
    assert mae(actual, predicted) == pytest.approx((10 + 10 + 30) / 3)
    assert rmse(actual, predicted) == pytest.approx(((100 + 100 + 900) / 3) ** 0.5)


def test_mape_skips_zero_actuals():
    assert mape([0, 100], [50, 110]) == pytest.approx(10.0)


def test_mape_is_none_when_every_actual_is_zero():
    assert mape([0, 0], [1, 2]) is None


def test_smape_is_safe_with_zeros():
    assert smape([0, 0], [0, 0]) == 0.0
    assert smape([0], [100]) == pytest.approx(200.0)


def test_length_mismatch_is_rejected():
    with pytest.raises(ValueError):
        mae([1, 2], [1])
