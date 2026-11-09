# tests/test_risk_metrics.py

import random

import pytest

from app.services.stocks.risk_metrics import beta, correlation, historical_var, sharpe_ratio


def test_sharpe_is_none_for_zero_volatility():
    assert sharpe_ratio([0.001] * 30) is None


def test_sharpe_is_positive_for_positive_drift_and_falls_with_risk_free_rate():
    rng = random.Random(1)
    returns = [0.001 + rng.gauss(0, 0.01) for _ in range(500)]
    assert sharpe_ratio(returns) > 0
    assert sharpe_ratio(returns, risk_free_annual=0.10) < sharpe_ratio(returns, risk_free_annual=0.0)


def test_var_is_a_positive_loss_near_the_empirical_percentile():
    returns = [i / 1000 for i in range(-50, 50)]    # -5% ... +4.9%
    var95 = historical_var(returns, 0.95)
    assert 0.04 < var95 < 0.05


def test_var_is_zero_when_there_are_no_losses_in_the_tail():
    assert historical_var([0.01] * 30) == 0.0


def test_higher_confidence_means_larger_var():
    rng = random.Random(2)
    returns = [rng.gauss(0, 0.02) for _ in range(500)]
    assert historical_var(returns, 0.99) > historical_var(returns, 0.90)


def test_var_needs_enough_observations():
    with pytest.raises(ValueError):
        historical_var([0.01] * 5)


def test_beta_of_the_benchmark_with_itself_is_one_and_doubling_gives_two():
    bench = [0.01, -0.02, 0.015, -0.01, 0.02, -0.005]
    assert beta(bench, bench) == pytest.approx(1.0)
    assert beta([2 * r for r in bench], bench) == pytest.approx(2.0)


def test_beta_of_an_independent_constant_is_zero():
    assert beta([0.01] * 6, [0.01, -0.02, 0.015, -0.01, 0.02, -0.005]) == pytest.approx(0.0, abs=1e-12)


def test_correlation_extremes():
    a = [0.01, -0.02, 0.015, -0.01, 0.02]
    assert correlation(a, a) == pytest.approx(1.0)
    assert correlation(a, [-x for x in a]) == pytest.approx(-1.0)


def test_misaligned_series_are_rejected():
    with pytest.raises(ValueError):
        beta([0.01, 0.02], [0.01])
