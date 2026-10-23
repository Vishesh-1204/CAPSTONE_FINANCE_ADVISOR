# tests/test_monte_carlo.py

import pytest

from app.services.investing.monte_carlo import simulate_sip
from app.services.investing.sip_planner import future_value_sip


def test_same_seed_gives_identical_results():
    a = simulate_sip(5000, 5, 0.10, 0.15, n_paths=300, seed=7)
    b = simulate_sip(5000, 5, 0.10, 0.15, n_paths=300, seed=7)
    assert a == b


def test_percentiles_are_ordered():
    result = simulate_sip(5000, 5, 0.10, 0.18, n_paths=500)
    assert result.p10 <= result.p50 <= result.p90


def test_zero_volatility_matches_the_closed_form():
    result = simulate_sip(5000, 5, 0.10, 0.0, n_paths=10)
    assert result.p50 == pytest.approx(future_value_sip(5000, 0.10, 5), rel=1e-6)
    assert result.p10 == result.p90


def test_probability_falls_as_the_goal_rises():
    easy = simulate_sip(5000, 5, 0.10, 0.15, n_paths=500, goal=100_000).probability_of_goal
    hard = simulate_sip(5000, 5, 0.10, 0.15, n_paths=500, goal=1_000_000).probability_of_goal
    assert easy > hard


def test_no_goal_means_no_probability():
    assert simulate_sip(1000, 2, 0.08, 0.1, n_paths=50).probability_of_goal is None


def test_invalid_inputs_are_rejected():
    with pytest.raises(ValueError):
        simulate_sip(1000, 2, 0.08, -0.1)
