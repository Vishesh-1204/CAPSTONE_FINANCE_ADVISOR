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

from dataclasses import dataclass
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


def prediction_interval(
    point: float, residuals: Sequence[float], coverage: float = 0.8,
    min_residuals: int = 3, fallback_relative_width: float = 0.25,
) -> tuple[float, float, bool]:
    """
    Builds an interval around `point` from the model's own past errors.
    Returns (lower, upper, low_confidence). With too few residuals it falls back to a
    fixed percentage band and flags low_confidence=True so the user is told honestly.
    The lower bound never goes below 0 (spending cannot be negative).
    Pass horizon_sum_errors(...) as `residuals` to get the interval of a horizon total.
    """
    if len(residuals) < min_residuals:
        spread = abs(point) * fallback_relative_width
        return max(0.0, point - spread), point + spread, True
    ordered = sorted(residuals)
    tail = (1 - coverage) / 2
    lower = point + _quantile(ordered, tail)
    upper = point + _quantile(ordered, 1 - tail)
    return max(0.0, lower), max(lower, upper), False
