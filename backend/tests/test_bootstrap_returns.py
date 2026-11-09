# tests/test_bootstrap_returns.py

import pytest

from app.services.investing.sip_planner import future_value_sip
from app.services.stocks.bootstrap_returns import block_bootstrap_sip

HISTORY = [0.02, -0.03, 0.01, 0.04, -0.06, 0.03, 0.015, -0.01, 0.025, 0.0, -0.02, 0.05]


def test_same_seed_same_result():
    a = block_bootstrap_sip(HISTORY, 5000, 5, n_paths=200, seed=3)
    assert a == block_bootstrap_sip(HISTORY, 5000, 5, n_paths=200, seed=3)


def test_percentiles_are_ordered():
    r = block_bootstrap_sip(HISTORY, 5000, 5, n_paths=300)
    assert r.p10 <= r.p50 <= r.p90


def test_constant_history_matches_the_closed_form():
    monthly = (1.10) ** (1 / 12) - 1
    r = block_bootstrap_sip([monthly] * 24, 5000, 5, n_paths=20, block_length=6)
    assert r.p50 == pytest.approx(future_value_sip(5000, 0.10, 5), rel=1e-6)
    assert r.p10 == r.p90


def test_zero_returns_give_just_the_deposits():
    assert block_bootstrap_sip([0.0] * 12, 1000, 2, n_paths=10).p50 == pytest.approx(24000)


def test_goal_probability_falls_as_the_goal_rises():
    easy = block_bootstrap_sip(HISTORY, 5000, 5, n_paths=300, goal=100_000).probability_of_goal
    hard = block_bootstrap_sip(HISTORY, 5000, 5, n_paths=300, goal=2_000_000).probability_of_goal
    assert easy > hard


def test_history_shorter_than_one_block_is_rejected():
    with pytest.raises(ValueError):
        block_bootstrap_sip([0.01, 0.02], 1000, 2, block_length=6)
