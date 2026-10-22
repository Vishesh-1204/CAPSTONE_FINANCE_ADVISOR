# app/services/forecasting/variable_forecast.py
#
# Purpose: forecast the VARIABLE spending of the forecast window, with its 80%
# interval, from the monthly variable series. Pure (no DB, no settings import:
# the caller passes the tunables), so the API service and the F6 evaluation run
# exactly the same logic.
#
#   1. Model choice: one-step rolling-origin backtest of every candidate
#      (select_best_model). Only trusted with at least `min_folds` folds;
#      otherwise the 3-month moving average (over what exists) is used and the
#      result is flagged low_confidence.
#   2. Point: the chosen model forecasts one value per month slice; the window
#      total is sum(month forecast x month weight).
#   3. Interval: from that model's backtest errors of the SAME weighted total
#      (horizon_sum_errors). Fewer than `min_interval_errors` -> the fixed
#      fallback width, and low_confidence.

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from app.services.forecasting.backtest import (
    horizon_sum_errors, prediction_interval, select_best_model, weighted_sum,
)
from app.services.forecasting.forecast_models import FALLBACK_MODEL, ModelSpec, default_model_registry

MIN_TRAIN_MONTHS = 3     # the first backtest origin (a model sees at least 3 months)


@dataclass(frozen=True)
class VariableForecast:
    model_name: str
    selected_by_backtest: bool        # False = fallback model (not enough folds to compare)
    monthly: list[float]              # full-month forecast for each month slice
    point: float                      # weighted total over the window
    lower: float
    upper: float
    low_confidence: bool
    scores: list[dict] = field(default_factory=list)   # [{model_name, mae, folds}] for explainability


def forecast_variable(
    series: Sequence[float],
    weights: Sequence[float],
    coverage: float = 0.8,
    min_folds: int = 2,
    min_interval_errors: int = 3,
    fallback_width: float = 0.25,
    registry: Mapping[str, ModelSpec] | None = None,
) -> VariableForecast:
    """`series` = monthly variable spending (oldest first); `weights` = one per month slice."""
    if not series:
        raise ValueError("the variable series is empty")
    registry = registry or default_model_registry()
    selection = select_best_model(series, registry, horizon=1, min_train=MIN_TRAIN_MONTHS, min_folds=min_folds)
    if selection is not None:
        spec = registry[selection.best.model_name]
        scores = [{"model_name": r.model_name, "mae": round(r.score, 2), "folds": r.n_folds}
                  for r in selection.all_results]
    else:
        spec = registry[FALLBACK_MODEL]
        scores = []

    monthly = [max(0.0, value) for value in spec.forecaster(series, len(weights))]
    point = weighted_sum(monthly, weights)
    errors = horizon_sum_errors(series, spec.forecaster, weights, max(MIN_TRAIN_MONTHS, spec.min_history))
    lower, upper, interval_is_fallback = prediction_interval(
        point, errors, coverage=coverage, min_residuals=min_interval_errors, fallback_relative_width=fallback_width,
    )
    return VariableForecast(
        model_name=spec.name,
        selected_by_backtest=selection is not None,
        monthly=monthly,
        point=point,
        lower=lower,
        upper=upper,
        low_confidence=selection is None or interval_is_fallback,
        scores=scores,
    )
