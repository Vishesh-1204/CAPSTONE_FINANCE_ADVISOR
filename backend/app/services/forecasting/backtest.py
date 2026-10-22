# app/services/forecasting/backtest.py
#
# Purpose: decide WHICH model to trust for a given user, and how uncertain it is.
# Rolling-origin backtest: pretend it is month t, forecast the next `horizon` months,
# compare with what really happened, move t forward, repeat. Never uses the future
# to predict the past (no leakage). The model with the lowest error wins, and its
# past errors define the prediction interval.
#
# Two kinds of backtest live here:
#   * rolling_origin_backtest / select_best_model: one error per forecast STEP;
#     used to choose the model (with horizon=1, the standard one-step comparison).
#   * horizon_sum_errors: one error per FOLD for the weighted SUM over the whole
#     horizon (e.g. "9 days of October + 21 days of November"). The interval for
#     a horizon total must come from these, not from adding up per-month
#     quantiles: monthly errors are correlated (a user who overspends in one
#     month often does in the next), so summing quantiles gives the wrong width.
#
# Interval methods (FORECAST_INTERVAL_METHOD; F6.1 added the first and third after the
# evaluation showed the original "empirical" intervals covered 47% instead of 80%):
#   * student_t  (default) point + mean error +/- t(n-1) x sd x sqrt(1 + 1/n): the
#                textbook interval for ONE new value from n past errors. With few
#                errors t and sqrt(1 + 1/n) are large, so the band honestly widens.
#   * empirical  the 10th/90th percentile of the errors. Too narrow with 3-10
#                errors: the extremes of a small sample understate the real spread.
#   * conformal  finite-sample order statistics (split-conformal ranks); needs about
#                9 errors at 80% coverage, otherwise the fixed fallback width is used.

import math
import statistics
from dataclasses import dataclass
from statistics import NormalDist
from typing import Mapping, Sequence

from app.services.forecasting.forecast_models import Forecaster, ModelSpec
from app.services.forecasting.metrics import mae


@dataclass(frozen=True)
class BacktestResult:
    """Backtest outcome for one model."""
    model_name: str
    score: float                 # mean absolute error over all backtest folds
    residuals: list[float]       # actual - predicted, one per forecast step (signed)
    n_folds: int


@dataclass(frozen=True)
class Selection:
    """The winning model, its backtest score, and how every candidate did."""
    best: BacktestResult
    all_results: list[BacktestResult]


def rolling_origin_backtest(
    series: Sequence[float], forecaster: Forecaster, model_name: str, horizon: int, min_train: int
) -> BacktestResult | None:
    """
    Returns None when the series is too short to run even one fold.
    A fold trains on series[:t] and predicts series[t:t+horizon].
    """
    actuals: list[float] = []
    predictions: list[float] = []
    folds = 0
    for origin in range(min_train, len(series) - horizon + 1):
        try:
            forecast = forecaster(series[:origin], horizon)
        except ValueError:
            return None      # model needs more history than we have
        actuals.extend(series[origin:origin + horizon])
        predictions.extend(forecast)
        folds += 1
    if folds == 0:
        return None
    residuals = [a - p for a, p in zip(actuals, predictions)]
    return BacktestResult(model_name, mae(actuals, predictions), residuals, folds)


def select_best_model(
    series: Sequence[float], registry: Mapping[str, ModelSpec], horizon: int, min_train: int = 3,
    min_folds: int = 1,
) -> Selection | None:
    """
    Backtests every eligible model and returns the one with the lowest MAE.
    Ties go to the simpler (earlier-registered) model. None if nothing could be tested.
    A model with fewer than `min_folds` folds is not trusted enough to compete
    (one lucky month must not decide the model).
    """
    results: list[BacktestResult] = []
    for spec in registry.values():
        if len(series) < spec.min_history + horizon:
            continue
        result = rolling_origin_backtest(series, spec.forecaster, spec.name, horizon, max(min_train, spec.min_history))
        if result is not None and result.n_folds >= min_folds:
            results.append(result)
    if not results:
        return None
    best = min(results, key=lambda r: r.score)   # min() keeps the first of equal scores
    return Selection(best=best, all_results=results)


def weighted_sum(values: Sequence[float], weights: Sequence[float]) -> float:
    """Sum of value x weight; used for 'full October forecast x 9/31 + full November x 21/30'."""
    if len(values) != len(weights):
        raise ValueError("values and weights must be the same length")
    return sum(v * w for v, w in zip(values, weights))


def horizon_sum_errors(
    series: Sequence[float], forecaster: Forecaster, weights: Sequence[float], min_train: int
) -> list[float]:
    """
    Rolling-origin errors of the weighted horizon TOTAL, one per fold:
        error = sum_k weights[k] * (actual[t+k] - forecast[k]).
    The weights are the same month weights the real forecast uses, so the
    errors describe exactly the quantity whose interval we want.
    Returns [] when no fold fits (or the model needs more history).
    """
    steps = len(weights)
    errors: list[float] = []
    for origin in range(min_train, len(series) - steps + 1):
        try:
            forecast = forecaster(series[:origin], steps)
        except ValueError:
            return []
        actual = series[origin:origin + steps]
        errors.append(weighted_sum([a - p for a, p in zip(actual, forecast)], weights))
    return errors


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile of an already sorted list."""
    position = q * (len(sorted_values) - 1)
    lower, upper = int(position), min(int(position) + 1, len(sorted_values) - 1)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


INTERVAL_METHODS: tuple[str, ...] = ("student_t", "empirical", "conformal")


def student_t_quantile(p: float, df: int) -> float:
    """
    The p-quantile of Student's t with `df` degrees of freedom (p > 0.5), by the
    Cornish-Fisher expansion around the normal quantile. Within 0.5% of the exact
    value for df >= 2, and needs no SciPy (no new dependency).
    """
    if df < 1:
        raise ValueError("df must be at least 1")
    z = NormalDist().inv_cdf(p)
    return (z + (z ** 3 + z) / (4 * df) + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * df ** 2)
            + (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / (384 * df ** 3))


def _offsets(residuals: Sequence[float], coverage: float, method: str) -> tuple[float, float] | None:
    """(lower, upper) offsets to add to the point, or None when the method cannot give one."""
    tail = (1 - coverage) / 2
    n = len(residuals)
    if method == "student_t":
        centre = statistics.fmean(residuals)
        half = student_t_quantile(1 - tail, n - 1) * statistics.stdev(residuals) * math.sqrt(1 + 1 / n)
        return centre - half, centre + half
    ordered = sorted(residuals)
    if method == "empirical":
        return _quantile(ordered, tail), _quantile(ordered, 1 - tail)
    if method == "conformal":
        # round() first: 10 x 0.9 is 9.000000000000002 in floats, and ceil() would make it 10
        low_rank, high_rank = math.floor(round((n + 1) * tail, 9)), math.ceil(round((n + 1) * (1 - tail), 9))
        if low_rank < 1 or high_rank > n:
            return None                       # too few errors to guarantee the coverage
        return ordered[low_rank - 1], ordered[high_rank - 1]
    raise ValueError(f"unknown interval method {method!r}; expected one of {INTERVAL_METHODS}")


def prediction_interval(
    point: float, residuals: Sequence[float], coverage: float = 0.8,
    min_residuals: int = 3, fallback_relative_width: float = 0.25, method: str = "student_t",
) -> tuple[float, float, bool]:
    """
    Builds an interval around `point` from the model's own past errors.
    Returns (lower, upper, low_confidence). With too few residuals (or too few for the
    conformal ranks) it falls back to a fixed percentage band and flags
    low_confidence=True so the user is told honestly.
    The lower bound never goes below 0 (spending cannot be negative).
    Pass horizon_sum_errors(...) as `residuals` to get the interval of a horizon total.
    """
    offsets = _offsets(residuals, coverage, method) if len(residuals) >= max(2, min_residuals) else None
    if offsets is None:
        spread = abs(point) * fallback_relative_width
        return max(0.0, point - spread), point + spread, True
    lower, upper = point + offsets[0], point + offsets[1]
    return max(0.0, lower), max(lower, upper), False
