# tests/test_walk_forward.py

import pytest

from app.services.stocks.walk_forward import (
    binomial_p_value_greater, directional_accuracy, mean_absolute_error, strategy_return, walk_forward_splits,
)


def test_splits_never_let_the_test_window_overlap_the_training_data():
    splits = walk_forward_splits(n=10, initial_train=5, test_size=2, step=1)
    assert splits[0] == (5, 5, 7)
    assert all(test_start == train_end and test_end <= 10 for train_end, test_start, test_end in splits)
    assert [s[0] for s in splits] == [5, 6, 7, 8]


def test_training_window_only_grows():
    ends = [s[0] for s in walk_forward_splits(30, 10, 1, 3)]
    assert ends == sorted(ends) and ends[0] == 10


def test_not_enough_data_gives_no_splits():
    assert walk_forward_splits(5, 5, 1) == []


def test_directional_accuracy_counts_sign_matches_and_skips_flat_days():
    accuracy, used = directional_accuracy([0.01, -0.02, 0.0, 0.03], [0.5, -0.1, 0.2, -0.4])
    assert used == 3 and accuracy == pytest.approx(2 / 3)


def test_directional_accuracy_rejects_bad_input():
    with pytest.raises(ValueError):
        directional_accuracy([0.01], [0.01, 0.02])
    with pytest.raises(ValueError):
        directional_accuracy([0.0, 0.0], [1, 1])


def test_binomial_p_value_known_cases():
    assert binomial_p_value_greater(0, 10) == pytest.approx(1.0)
    assert binomial_p_value_greater(10, 10) == pytest.approx(0.5 ** 10)
    assert binomial_p_value_greater(5, 10) == pytest.approx(0.623046875)   # P(X >= 5) for a fair coin


def test_53_percent_over_100_days_is_not_significant_but_65_percent_is():
    assert binomial_p_value_greater(53, 100) > 0.05
    assert binomial_p_value_greater(65, 100) < 0.01


def test_binomial_p_value_handles_large_n():
    assert 0 <= binomial_p_value_greater(2600, 5000) <= 1


def test_mae():
    assert mean_absolute_error([1, 2, 3], [2, 2, 5]) == pytest.approx(1.0)


def test_strategy_return_matches_buy_and_hold_when_always_in():
    returns = [0.1, -0.05, 0.02]
    assert strategy_return(returns, [1, 1, 1]) == pytest.approx(1.1 * 0.95 * 1.02 - 1)
    assert strategy_return(returns, [0, 0, 0]) == 0
    assert strategy_return(returns, [1, 0, 1]) == pytest.approx(1.1 * 1.02 - 1)
