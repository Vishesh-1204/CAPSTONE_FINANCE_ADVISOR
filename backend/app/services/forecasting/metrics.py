# app/services/forecasting/metrics.py
#
# Purpose: forecast accuracy metrics (the ones named in the project plan: MAE, RMSE, MAPE),
# plus sMAPE because plain MAPE explodes when an actual value is zero (common for
# categories with no spending in a month).

import math
from typing import Sequence


def _check(actual: Sequence[float], predicted: Sequence[float]) -> None:
    if len(actual) != len(predicted) or not actual:
        raise ValueError("actual and predicted must be non-empty and the same length")


def mae(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """Mean absolute error, in rupees."""
    _check(actual, predicted)
    return sum(abs(a - p) for a, p in zip(actual, predicted)) / len(actual)


def rmse(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """Root mean squared error, in rupees. Punishes big misses harder than MAE."""
    _check(actual, predicted)
    return math.sqrt(sum((a - p) ** 2 for a, p in zip(actual, predicted)) / len(actual))


def mape(actual: Sequence[float], predicted: Sequence[float]) -> float | None:
    """Mean absolute percentage error. Rows where actual is 0 are skipped; None if none remain."""
    _check(actual, predicted)
    pairs = [(a, p) for a, p in zip(actual, predicted) if a != 0]
    if not pairs:
        return None
    return 100 * sum(abs((a - p) / a) for a, p in pairs) / len(pairs)


def smape(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """Symmetric MAPE (0 to 200). Safe when actual or predicted is zero."""
    _check(actual, predicted)
    total = 0.0
    for a, p in zip(actual, predicted):
        denominator = abs(a) + abs(p)
        total += 0.0 if denominator == 0 else 2 * abs(a - p) / denominator
    return 100 * total / len(actual)
