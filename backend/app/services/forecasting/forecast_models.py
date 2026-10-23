# app/services/forecasting/forecast_models.py
#
# Purpose: the simple, explainable forecasting models used by the Forecast Agent.
# Every model has the same signature, (history, horizon) -> list of point forecasts,
# so the backtester can compare them fairly and the agent can pick the best one per user.
# Pure Python: no database, no LLM, no third-party libraries, so it is trivially unit-testable.
#
# Why no LSTM/Transformer: a personal history is 3-12 monthly values. Deep models
# overfit that; these four baselines are the honest comparison for the paper.

from dataclasses import dataclass
from typing import Callable, Sequence

# A forecaster takes the past values (oldest first) and returns `horizon` future values.
Forecaster = Callable[[Sequence[float], int], list[float]]


def naive_last(history: Sequence[float], horizon: int) -> list[float]:
    """Baseline B0: next values equal the last observed value."""
    return [float(history[-1])] * horizon


def make_moving_average(window: int) -> Forecaster:
    """
    Baseline B1: next values equal the mean of the last `window` observations.
    With fewer than `window` observations it averages what exists (used for 1-2 month histories).
    """

    def moving_average(history: Sequence[float], horizon: int) -> list[float]:
        recent = history[-window:]
        return [sum(recent) / len(recent)] * horizon

    return moving_average


def make_seasonal_naive(season: int = 12) -> Forecaster:
    """Baseline B2: repeat the value from one season ago (same month last year)."""

    def seasonal_naive(history: Sequence[float], horizon: int) -> list[float]:
        if len(history) < season:
            raise ValueError(f"seasonal_naive needs at least {season} observations")
        return [float(history[len(history) - season + (step % season)]) for step in range(horizon)]

    return seasonal_naive


# Smoothing factors tried by exponential smoothing (small grid, so it stays fast and explainable)
_ALPHA_GRID = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


def _ses_level(history: Sequence[float], alpha: float) -> tuple[float, float]:
    """Runs simple exponential smoothing and returns (final level, in-sample squared error)."""
    level = float(history[0])
    squared_error = 0.0
    for value in history[1:]:
        squared_error += (value - level) ** 2   # one-step-ahead error before updating
        level = alpha * value + (1 - alpha) * level
    return level, squared_error


def simple_exp_smoothing(history: Sequence[float], horizon: int) -> list[float]:
    """
    Model B3: simple exponential smoothing. The smoothing factor alpha is chosen from a
    small grid by the lowest in-sample one-step squared error. Recent months weigh more
    than old ones, which suits spending that drifts slowly.
    """
    if len(history) < 2:
        return naive_last(history, horizon)
    best_level = min((_ses_level(history, a) for a in _ALPHA_GRID), key=lambda pair: pair[1])[0]
    return [best_level] * horizon


@dataclass(frozen=True)
class ModelSpec:
    """A forecaster plus the minimum history it needs before it may be used."""
    name: str
    forecaster: Forecaster
    min_history: int


# The model used when the history is too short to backtest (also the Phase 4 B1 baseline)
FALLBACK_MODEL = "moving_average_3"


def default_model_registry() -> dict[str, ModelSpec]:
    """The candidate models the agent may choose from. Add new models here only."""
    return {
        "naive_last": ModelSpec("naive_last", naive_last, 1),
        "moving_average_3": ModelSpec("moving_average_3", make_moving_average(3), 3),
        "exp_smoothing": ModelSpec("exp_smoothing", simple_exp_smoothing, 3),
        "seasonal_naive_12": ModelSpec("seasonal_naive_12", make_seasonal_naive(12), 12),
    }
