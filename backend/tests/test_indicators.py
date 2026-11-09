# tests/test_indicators.py

import math

import pytest

from app.services.stocks.indicators import (
    annualized_volatility, cagr, ema, log_returns, max_drawdown, rolling_volatility, rsi, simple_returns, sma,
)


def test_simple_returns_are_percentage_changes():
    assert simple_returns([100, 110, 99]) == pytest.approx([0.10, -0.10])


def test_log_returns_add_up_to_total_log_growth():
    prices = [100, 105, 98, 120]
    assert sum(log_returns(prices)) == pytest.approx(math.log(120 / 100))


def test_bad_prices_are_rejected():
    with pytest.raises(ValueError):
        simple_returns([100])
    with pytest.raises(ValueError):
        simple_returns([100, 0, 50])


def test_sma_values_and_leading_nones():
    result = sma([1, 2, 3, 4, 5], 3)
    assert result[:2] == [None, None] and result[2:] == pytest.approx([2, 3, 4])


def test_ema_of_a_constant_is_that_constant_and_starts_after_window():
    result = ema([5.0] * 10, 4)
    assert result[:3] == [None] * 3 and all(v == pytest.approx(5.0) for v in result[3:])


def test_ema_reacts_faster_than_sma_to_a_jump():
    values = [10.0] * 10 + [20.0] * 3
    assert ema(values, 5)[-1] > sma(values, 5)[-1]


def test_rsi_is_100_when_only_gains_and_0_when_only_losses():
    assert rsi([float(p) for p in range(1, 30)], 14)[-1] == 100.0
    assert rsi([float(p) for p in range(30, 1, -1)], 14)[-1] == pytest.approx(0.0)


def test_rsi_is_50_for_a_flat_series_and_stays_in_range():
    assert rsi([10.0] * 20, 14)[-1] == 50.0
    noisy = [100 + ((i * 7) % 11) - 5 for i in range(60)]
    assert all(0 <= v <= 100 for v in rsi([float(p) for p in noisy], 14) if v is not None)


def test_rsi_needs_enough_history():
    assert all(v is None for v in rsi([1.0, 2.0, 3.0], 14))


def test_constant_returns_have_zero_volatility():
    assert annualized_volatility([0.01] * 30) == pytest.approx(0.0, abs=1e-12)


def test_volatility_scales_with_sqrt_of_time():
    returns = [0.01, -0.02, 0.015, -0.005, 0.02, -0.01]
    daily_std = annualized_volatility(returns, 1)
    assert annualized_volatility(returns, 252) == pytest.approx(daily_std * math.sqrt(252))


def test_rolling_volatility_has_leading_nones():
    result = rolling_volatility([0.01, -0.01, 0.02, -0.02, 0.01], 3)
    assert result[:2] == [None, None] and all(v is not None for v in result[2:])


def test_max_drawdown_finds_the_worst_fall():
    result = max_drawdown([100, 120, 90, 110, 80, 130])
    assert result.depth == pytest.approx(80 / 120 - 1)
    assert (result.peak_index, result.trough_index) == (1, 4)


def test_rising_series_has_no_drawdown():
    assert max_drawdown([1, 2, 3, 4]).depth == 0


def test_cagr_doubling_in_one_year_is_100_percent():
    prices = [100.0] + [100.0] * 251 + [200.0]
    assert cagr(prices, 252) == pytest.approx(1.0)
