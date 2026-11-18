# app/services/stocks/walk_forward.py
#
# Purpose: honest evaluation of price-direction models. Time series must never be shuffled:
# a model may only be tested on data that comes AFTER everything it trained on (walk-forward /
# expanding window). Also gives the metrics and an exact binomial test so we can say whether a
# hit rate like 53% is distinguishable from a coin flip. Pure Python.

import math


def walk_forward_splits(n: int, initial_train: int, test_size: int = 1, step: int = 1) -> list[tuple[int, int, int]]:
    """
    Returns (train_end, test_start, test_end) index triples. Train = [0, train_end) and
    test = [test_start, test_end), with test_start == train_end, so no test row is ever
    used for training (no look-ahead leakage).
    """
    if initial_train < 1 or test_size < 1 or step < 1:
        raise ValueError("initial_train, test_size and step must be >= 1")
    splits: list[tuple[int, int, int]] = []
    train_end = initial_train
    while train_end + test_size <= n:
        splits.append((train_end, train_end, train_end + test_size))
        train_end += step
    return splits


def directional_accuracy(actual: list[float], predicted: list[float]) -> tuple[float, int]:
    """
    Share of periods where the predicted sign (up/down) matched the actual sign.
    Periods where the actual change is exactly zero are skipped. Returns (accuracy, n_used).
    """
    if len(actual) != len(predicted):
        raise ValueError("actual and predicted must have the same length")
    pairs = [(a, p) for a, p in zip(actual, predicted) if a != 0]
    if not pairs:
        raise ValueError("no non-zero actual changes to compare")
    hits = sum(1 for a, p in pairs if (a > 0) == (p > 0))
    return hits / len(pairs), len(pairs)


def binomial_p_value_greater(successes: int, n: int, p: float = 0.5) -> float:
    """
    One-sided exact probability of getting at least `successes` hits in n tries by luck alone
    (each try succeeding with probability p). Small values mean the hit rate is unlikely to be
    a coin flip. Uses log-gamma so large n does not overflow.
    """
    if not 0 <= successes <= n or n < 1 or not 0 < p < 1:
        raise ValueError("need 0 <= successes <= n, n >= 1 and 0 < p < 1")

    def log_pmf(k: int) -> float:
        return (math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
                + k * math.log(p) + (n - k) * math.log(1 - p))

    return min(1.0, sum(math.exp(log_pmf(k)) for k in range(successes, n + 1)))


def mean_absolute_error(actual: list[float], predicted: list[float]) -> float:
    if len(actual) != len(predicted) or not actual:
        raise ValueError("need two non-empty series of the same length")
    return sum(abs(a - p) for a, p in zip(actual, predicted)) / len(actual)


def strategy_return(actual_returns: list[float], signals: list[int]) -> float:
    """
    Total return of a long/flat rule: hold (signal 1) or stay out (signal 0) each period.
    Compare with buy-and-hold (all signals 1) to see whether a model adds anything.
    """
    if len(actual_returns) != len(signals):
        raise ValueError("returns and signals must have the same length")
    growth = 1.0
    for r, s in zip(actual_returns, signals):
        growth *= 1 + (r if s else 0.0)
    return growth - 1
