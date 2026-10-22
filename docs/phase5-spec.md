# Phase 5 Spec: Financial Forecast Agent

Repo path (commit it here): `docs/phase5-spec.md`
Builds on Phase 4 (budgets, `budget_runs`, explanation guard, synthetic history, smoke test) and Phase 3B v3 (`txn_type`, `payee_key`, statement `balance`).
Source: project plan, Module 6: forecast future savings, monthly balance, expenses and cash flow for 30, 60 and 90 days.
The pure-Python reference code in the appendix passed its 28 unit tests.

## 0. Ground rules for Claude Code (read first)

1. Read CLAUDE.md and this file. INSPECT the real code first (models, `txn_type` values, `payee_key`, `balance`, Phase 4 services, test fixtures, synthetic history script) and report differences before editing.
2. Plan mode first. One step at a time (section 10). After each step run `docker compose exec backend python -m pytest tests -q`, all green, one commit. Stop at every GATE and report.
3. Branch is `feature/vishesh-forecast` (already created from DEV; verify it contains Phase 4).
4. DEPENDENCIES: the pure modules need none. For the optional ML tier (step F9) only `scikit-learn` and `lightgbm` may be added, with pinned versions compatible with the pinned numpy. Do NOT add xgboost or torch (very large wheels; this team's PyPI downloads are slow), and do NOT add statsmodels or pandas without asking me first with a one-line reason.
5. Python does ALL the maths. The LLM only explains finished numbers, ONE call per forecast, reusing `explanation_guard` (every rupee amount in the text must come from the forecast). Forecasts must still work with no LLM key (template text).
6. Privacy: the LLM receives only aggregate numbers and category names, never descriptions, payees, UPI IDs or account details.
7. Layering as in CLAUDE.md. `user_id` from the JWT only; every query filters by `user_id`. Inject the clock (`as_of: date`) into services so tests never depend on today's date.
8. Teach as you go in beginner-friendly language; file path and purpose comment at the top of each new file; type hints, logging, no hardcoded tunables (config). Do not touch `.env`. Synthetic data only. Update CLAUDE.md status at the end.

## 1. Overview and design choice (read this, it is the research story)

A personal finance history is SHORT: a user may have 3 to 12 months of data. Deep models (LSTM, Transformer) overfit that. So Phase 5 uses a decomposition that is explainable and honest about uncertainty:

1. Recurring money (salary, rent, subscriptions, SIPs) is detected from history and projected EXACTLY on its dates (`recurrence_detector`). It is not "predicted".
2. The remaining variable spending is forecast statistically, per user, from a monthly series, with a small set of simple models (naive, 3-month moving average, exponential smoothing, seasonal naive when 12+ months exist).
3. The model for each user is chosen by a rolling-origin BACKTEST on that user's own history (never uses the future), and the same backtest errors give an 80% prediction interval. Too little history is reported as `low_confidence`, not hidden.
4. A daily cash-flow trajectory combines: opening balance, scheduled income, scheduled commitments (recurring debits), and variable spending spread over the days. Output: projected balance with bands, the first date the balance may go below zero ("cash crunch"), and savings.
5. The agent links to Phase 4: projected variable spending is compared with the month's budget limits and projected overspends are listed.
6. The LLM writes the explanation, and the guard rejects any invented number.

Research question for the paper: on sparse personal-finance histories, does a recurrence-aware decomposition with per-user backtest-driven model selection (plus honest intervals) beat a single fixed model or a global ML model?

## 2. Why it is needed

- Phases 6 (investments), 8 (goals) and 10 (coach) all need "how much will be left over".
- Backtest selection and calibrated intervals give you measurable claims (accuracy and interval coverage) instead of a black box.
- Recurring detection is what makes the forecast explainable to a user and to a panel.

## 3. Inputs and data preparation

- History source: the user's transactions with `txn_type`. Variable series = monthly totals of `txn_type='expense'` rows MINUS rows matched to detected recurring debits (so rent is not counted twice). Investments (`investment_out`) are projected separately as scheduled outflows when recurring (SIPs), and reported separately.
- Use only COMPLETE calendar months before `as_of`. The current partial month is excluded from model fitting. Months with no data inside the span count as 0.
- Income: detected recurring credits (`txn_type='income'`). If none detected, use the profile `monthly_income` from Phase 4 and say so (`income_source`).
- Opening balance, in this order: request parameter `opening_balance`; else the latest non-null statement `balance`; else HTTP 400 asking for it. Return `opening_balance_source` ('request' | 'statement').
- Minimum history rules: fewer than 1 complete month of expenses -> HTTP 422 with a clear message. 1 to 2 months -> moving average only, interval fallback, `low_confidence=true`. 3 or more -> backtest selection. 12 or more -> seasonal naive becomes eligible.

## 4. Database (Alembic revision 0004)

- `forecast_runs`: id, user_id FK indexed, as_of date, horizon_days (30|60|90), model_name, history_months int, low_confidence bool, opening_balance Numeric(14,2), opening_balance_source, income_source, expected_income, expected_fixed_expenses, expected_variable_expenses (point, lower, upper), expected_investments, net_cashflow, projected_end_balance (point, lower, upper), cash_crunch_date date NULL, llm_used bool, explanation_faithful bool NULL, trajectory JSON (daily date, balance, lower, upper), recurring_items JSON, budget_warnings JSON, created_at. All money columns Numeric(14,2).
- Add the model to `app/db/base.py`. Verify the migration applies on an empty DB and on the current DB.
- Config (env-overridable): `FORECAST_INTERVAL_COVERAGE=0.8`, `FORECAST_MIN_HISTORY_MONTHS=1`, `FORECAST_RECURRING_MIN_OCCURRENCES=3`, `FORECAST_RECURRING_AMOUNT_TOLERANCE=0.15`, `FORECAST_FALLBACK_INTERVAL_WIDTH=0.25`.

## 5. Behaviour details

- Horizon h in days maps to months by rounding up (30->1, 60->2, 90->3); the variable forecast for the horizon is the sum of the next months (partial first month prorated by remaining days).
- Interval for the horizon sum: from backtest residuals of the chosen model (sum of per-step residual quantiles is NOT valid; use the empirical distribution of horizon-sum errors from the backtest, and fall back to the fixed width when there are too few folds).
- Daily trajectory: balance(day) = opening + scheduled credits up to day - scheduled debits up to day - variable spend per day * elapsed days. Variable spend per day = variable forecast of that month / days in that month (an optional day-of-week weighting can be a later ablation). The band grows with elapsed days (use the horizon interval scaled by elapsed fraction).
- Cash crunch: first date where the LOWER band (or point, say which in the response) is below zero. If none, null.
- Budget link: for the current/next budget month that has a Phase 4 budget, split projected variable spend across categories by the user's trailing 3-month category share, compare to `limit_amount`, and list categories projected to exceed (with projected amount and limit). No budget -> empty list.
- Deterministic template explanation always exists; LLM text is used only if `is_faithful(...)` passes for the summary against the allowed numbers (all amounts in the response).

## 6. API (JWT-protected; Pydantic schemas; `horizon_days` validated to 30, 60 or 90)

- `POST /api/forecast/generate?horizon_days=30&opening_balance=` -> the full run (also saved).
- `GET /api/forecast/latest?horizon_days=`; `GET /api/forecast/history` (summary list, newest first).
- `GET /api/forecast/recurring` -> detected recurring income/commitments with typical amount, day of month, occurrences (explainability; read-only in this phase).
- `GET /api/forecast/accuracy` (step F8): compares past runs whose horizon has ended with what actually happened (net cashflow and variable spend), so accuracy is measured on real use over time.

## 7. Tests

- Pure modules (appendix files, extend them): models, metrics, backtest, recurrence; add tests for the horizon-sum interval and the trajectory/cash-crunch projector.
- Service/API (SQLite + fake LLM, same fixtures as Phase 4): investments and credits handled per section 3; recurring rent is not double-counted; user isolation (404 on others' runs); fewer than 1 month -> 422; 1-2 months -> `low_confidence`; opening balance order of precedence and the 400 case; LLM down -> template and `llm_used=false`; LLM invents an amount -> template and `explanation_faithful=false`; invalid `horizon_days` -> 422; clock injection so results are reproducible.
- Migration test: upgrade head on an empty DB, and 0004 keeps existing data.
- Extend `backend/scripts/smoke_test.py` with a forecast step against the live API.

## 8. Research plan (your IEEE contribution for Phase 5)

- Synthetic panel: extend the Phase 4 generator to 24 months, about 30 users, 4 personas (steady salaried, freelancer with irregular income, high-spender, saver), fixed seeds, with festival-season spikes (Oct/Nov), one salary hike, and one-off shocks (a medical bill). Always evaluate with TIME-BASED rolling-origin splits, never random splits.
- Methods: B0 naive-last, B1 3-month moving average, B2 seasonal naive (12+ months), B3 exponential smoothing, S the full agent (recurrence-aware decomposition + backtest selection), optional M1 global LightGBM (step F9).
- Metrics: MAE, RMSE, MAPE and sMAPE of horizon-sum variable spend and of net cashflow at 30/60/90 days; interval coverage (target 80%, report actual) and mean interval width; cash-crunch detection precision and recall; model-selection regret (selected model error minus best fixed model error); faithfulness rate of the LLM explanation; LLM calls per forecast (1).
- Ablations: with/without recurrence-aware decomposition (forecast the raw total instead); backtest selection vs always-moving-average; selection window sizes; with/without interval calibration (fixed width vs residual-based); history length 3/6/12 months (shows when each model starts to win).
- Honest limits to state in the paper: results come from synthetic data; for real evidence collect a small consented set of anonymised statements from team members (6-12 months each) and report it separately. Do not claim real-world accuracy from synthetic results. Search for licensed public anonymised transaction datasets and verify licences before using any.
- Output: `evaluation/forecast_metrics.csv` plus a short markdown table for the paper.

## 9. Frontend (Test Console, separate session)

New `/forecast` page: horizon toggle (30/60/90); opening-balance input prefilled from the latest statement balance; Recharts line chart of projected balance with the lower/upper band; cards for expected income, fixed and variable expenses, investments, net cashflow and end balance; a cash-crunch warning banner; table of detected recurring items; budget-overspend warnings; the explanation text with a "template / AI" badge; a low-confidence notice; collapsible raw-response panel. Approved stack only.

## 10. Steps (stop at each GATE)

- F0: inspect the code; report differences; verify Phase 4 is in the branch. GATE.
- F1: migration 0004, model, schemas, config. GATE.
- F2: add the pure modules and tests from the appendix (adapt imports), then add the horizon-sum interval helper and the cash-flow projector with tests. GATE.
- F3: data preparation service (monthly variable series, recurring detection from the DB, opening balance rules) with tests. GATE.
- F4: forecast agent/explainer with the guard and template fallback (reuse Phase 4 guard). GATE.
- F5: forecast service, routes, saved runs, budget link, API tests, smoke-test step. GATE.
- F6: synthetic panel generator (24 months, 4 personas) and evaluation script producing `evaluation/forecast_metrics.csv`. GATE.
- F7: docs (purpose, architecture, flow diagram, input, output, API, tables, testing) under `docs/modules/`, CLAUDE.md status, full tests, smoke test, PR commands. GATE.
- F8 (optional): `GET /api/forecast/accuracy`. GATE.
- F9 (optional, ask me first): global LightGBM/scikit-learn model trained on the synthetic panel, compared in the evaluation. Report honestly if it does not beat the simple models on sparse histories.
- F10 (separate session): frontend `/forecast` page.

## Appendix: verified reference code (adapt, do not copy blindly)

All files below passed their tests (28 passing). Adapt imports to the real project.

### `backend/app/services/forecasting/forecast_models.py`
```python
# app/services/forecasting/forecast_models.py
#
# Purpose: the simple, explainable forecasting models used by the Forecast Agent.
# Every model has the same signature, (history, horizon) -> list of point forecasts,
# so the backtester can compare them fairly and the agent can pick the best one per user.
# Pure Python: no database, no LLM, no third-party libraries, so it is trivially unit-testable.

from dataclasses import dataclass
from typing import Callable, Sequence

# A forecaster takes the past values (oldest first) and returns `horizon` future values.
Forecaster = Callable[[Sequence[float], int], list[float]]


def naive_last(history: Sequence[float], horizon: int) -> list[float]:
    """Baseline B0: next values equal the last observed value."""
    return [float(history[-1])] * horizon


def make_moving_average(window: int) -> Forecaster:
    """Baseline B1: next values equal the mean of the last `window` observations."""

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


def default_model_registry() -> dict[str, ModelSpec]:
    """The candidate models the agent may choose from. Add new models here only."""
    return {
        "naive_last": ModelSpec("naive_last", naive_last, 1),
        "moving_average_3": ModelSpec("moving_average_3", make_moving_average(3), 3),
        "exp_smoothing": ModelSpec("exp_smoothing", simple_exp_smoothing, 3),
        "seasonal_naive_12": ModelSpec("seasonal_naive_12", make_seasonal_naive(12), 12),
    }
```

### `backend/app/services/forecasting/metrics.py`
```python
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
```

### `backend/app/services/forecasting/backtest.py`
```python
# app/services/forecasting/backtest.py
#
# Purpose: decide WHICH model to trust for a given user, and how uncertain it is.
# Rolling-origin backtest: pretend it is month t, forecast the next `horizon` months,
# compare with what really happened, move t forward, repeat. Never uses the future
# to predict the past (no leakage). The model with the lowest error wins, and its
# past errors define the prediction interval.

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
    series: Sequence[float], registry: Mapping[str, ModelSpec], horizon: int, min_train: int = 3
) -> Selection | None:
    """
    Backtests every eligible model and returns the one with the lowest MAE.
    Ties go to the simpler (earlier-registered) model. None if nothing could be tested.
    """
    results: list[BacktestResult] = []
    for spec in registry.values():
        if len(series) < spec.min_history + horizon:
            continue
        result = rolling_origin_backtest(series, spec.forecaster, spec.name, horizon, max(min_train, spec.min_history))
        if result is not None:
            results.append(result)
    if not results:
        return None
    best = min(results, key=lambda r: r.score)   # min() keeps the first of equal scores
    return Selection(best=best, all_results=results)


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
    """
    if len(residuals) < min_residuals:
        spread = abs(point) * fallback_relative_width
        return max(0.0, point - spread), point + spread, True
    ordered = sorted(residuals)
    tail = (1 - coverage) / 2
    lower = point + _quantile(ordered, tail)
    upper = point + _quantile(ordered, 1 - tail)
    return max(0.0, lower), max(lower, upper), False
```

### `backend/app/services/forecasting/recurrence_detector.py`
```python
# app/services/forecasting/recurrence_detector.py
#
# Purpose: find money that repeats on a schedule (salary, rent, subscriptions, SIPs).
# These are NOT forecast statistically. They are projected exactly on their dates,
# which makes the cash-flow forecast explainable ("rent of 18,000 leaves on the 5th").
# Only the leftover "variable" spending goes through the statistical models.

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Iterable, Iterator
import calendar


@dataclass(frozen=True)
class Event:
    """One historical transaction, reduced to what recurrence detection needs."""
    payee_key: str
    on: date
    amount: float          # always positive; `direction` tells the sign
    direction: str         # 'debit' or 'credit'


@dataclass(frozen=True)
class RecurringItem:
    """A payment that repeats about monthly."""
    payee_key: str
    direction: str
    typical_amount: float
    typical_day: int        # day of month it usually lands on
    occurrences: int
    last_seen: date


def detect_recurring(
    events: Iterable[Event], min_occurrences: int = 3, amount_tolerance: float = 0.15,
    min_gap_days: int = 25, max_gap_days: int = 35,
) -> list[RecurringItem]:
    """
    A payee counts as monthly-recurring when it appears at least `min_occurrences` times,
    consecutive payments are 25-35 days apart (median), and each amount is within
    `amount_tolerance` of the median amount. Variable bills (e.g. a changing electricity
    bill) fail the amount test on purpose, and stay in the statistical variable bucket.
    """
    groups: dict[tuple[str, str], list[Event]] = {}
    for event in events:
        groups.setdefault((event.payee_key, event.direction), []).append(event)

    items: list[RecurringItem] = []
    for (payee_key, direction), group in groups.items():
        if len(group) < min_occurrences:
            continue
        group = sorted(group, key=lambda e: e.on)
        gaps = [(b.on - a.on).days for a, b in zip(group, group[1:])]
        if not min_gap_days <= median(gaps) <= max_gap_days:
            continue
        typical = median(e.amount for e in group)
        if typical <= 0 or any(abs(e.amount - typical) / typical > amount_tolerance for e in group):
            continue
        items.append(RecurringItem(
            payee_key=payee_key, direction=direction, typical_amount=round(typical, 2),
            typical_day=int(median(e.on.day for e in group)), occurrences=len(group),
            last_seen=group[-1].on,
        ))
    return sorted(items, key=lambda item: (item.direction, -item.typical_amount))


def project_occurrences(item: RecurringItem, start: date, end: date) -> Iterator[date]:
    """
    Yields each future date (start <= date <= end) the item is expected on.
    A day that does not exist in a short month (e.g. the 31st in April) moves to month-end.
    """
    year, month = start.year, start.month
    while date(year, month, 1) <= end:
        day = min(item.typical_day, calendar.monthrange(year, month)[1])
        occurrence = date(year, month, day)
        if start <= occurrence <= end and occurrence > item.last_seen:
            yield occurrence
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
```

### `backend/tests/test_forecast_models.py`
```python
# tests/test_forecast_models.py

import pytest

from app.services.forecasting.forecast_models import (
    default_model_registry, make_moving_average, make_seasonal_naive, naive_last, simple_exp_smoothing,
)


def test_naive_repeats_last_value():
    assert naive_last([10, 20, 30], 3) == [30.0, 30.0, 30.0]


def test_moving_average_uses_only_the_window():
    assert make_moving_average(3)([1, 100, 20, 30, 40], 2) == [30.0, 30.0]


def test_seasonal_naive_repeats_last_season():
    history = list(range(1, 13)) + [20]        # 13 months
    forecast = make_seasonal_naive(12)(history, 3)
    assert forecast == [2.0, 3.0, 4.0]


def test_seasonal_naive_needs_a_full_season():
    with pytest.raises(ValueError):
        make_seasonal_naive(12)([1, 2, 3], 1)


def test_exp_smoothing_on_constant_series_is_that_constant():
    assert simple_exp_smoothing([5000] * 6, 2) == pytest.approx([5000.0, 5000.0])


def test_exp_smoothing_leans_toward_recent_values():
    rising = [1000, 1000, 1000, 1000, 2000, 2000]
    assert 1000 < simple_exp_smoothing(rising, 1)[0] <= 2000


def test_registry_lists_the_candidate_models():
    assert set(default_model_registry()) == {"naive_last", "moving_average_3", "exp_smoothing", "seasonal_naive_12"}
```

### `backend/tests/test_forecast_metrics.py`
```python
# tests/test_forecast_metrics.py

import pytest

from app.services.forecasting.metrics import mae, mape, rmse, smape


def test_mae_and_rmse_on_a_known_example():
    actual, predicted = [100, 200, 300], [110, 190, 330]
    assert mae(actual, predicted) == pytest.approx((10 + 10 + 30) / 3)
    assert rmse(actual, predicted) == pytest.approx(((100 + 100 + 900) / 3) ** 0.5)


def test_mape_skips_zero_actuals():
    assert mape([0, 100], [50, 110]) == pytest.approx(10.0)


def test_mape_is_none_when_every_actual_is_zero():
    assert mape([0, 0], [1, 2]) is None


def test_smape_is_safe_with_zeros():
    assert smape([0, 0], [0, 0]) == 0.0
    assert smape([0], [100]) == pytest.approx(200.0)


def test_length_mismatch_is_rejected():
    with pytest.raises(ValueError):
        mae([1, 2], [1])
```

### `backend/tests/test_forecast_backtest.py`
```python
# tests/test_forecast_backtest.py

import pytest

from app.services.forecasting.backtest import prediction_interval, rolling_origin_backtest, select_best_model
from app.services.forecasting.forecast_models import default_model_registry, naive_last


def test_backtest_never_peeks_at_the_future():
    seen_lengths: list[int] = []

    def spy(history, horizon):
        seen_lengths.append(len(history))
        return [history[-1]] * horizon

    series = [1, 2, 3, 4, 5, 6]
    rolling_origin_backtest(series, spy, "spy", horizon=1, min_train=3)
    assert seen_lengths == [3, 4, 5]          # origins 3, 4, 5; each sees only the past


def test_backtest_returns_none_for_a_too_short_series():
    assert rolling_origin_backtest([1, 2], naive_last, "naive", horizon=1, min_train=3) is None


def test_best_model_wins_on_a_trending_series():
    series = [1000 + 100 * i for i in range(10)]          # steady rise
    selection = select_best_model(series, default_model_registry(), horizon=1)
    assert selection is not None
    assert selection.best.model_name in {"naive_last", "exp_smoothing"}   # follow the rise, not the old average
    ma = next(r for r in selection.all_results if r.model_name == "moving_average_3")
    assert selection.best.score < ma.score


def test_seasonal_model_is_not_tried_without_enough_history():
    series = [1000.0] * 8
    selection = select_best_model(series, default_model_registry(), horizon=1)
    assert "seasonal_naive_12" not in {r.model_name for r in selection.all_results}


def test_no_selection_when_history_is_too_short_for_any_model():
    assert select_best_model([1000.0, 1100.0], default_model_registry(), horizon=1) is None


def test_interval_comes_from_past_errors_and_brackets_the_point():
    lower, upper, low_conf = prediction_interval(1000, [-100, -50, 0, 50, 100, 150], coverage=0.8)
    assert not low_conf
    assert lower < 1000 < upper


def test_interval_is_flagged_low_confidence_with_few_residuals():
    lower, upper, low_conf = prediction_interval(1000, [10], fallback_relative_width=0.25)
    assert low_conf and (lower, upper) == (750.0, 1250.0)


def test_interval_lower_bound_never_goes_negative():
    lower, _, _ = prediction_interval(100, [-500, -400, -300, 10, 20], coverage=0.9)
    assert lower == 0.0
```

### `backend/tests/test_recurrence_detector.py`
```python
# tests/test_recurrence_detector.py

from datetime import date

from app.services.forecasting.recurrence_detector import Event, RecurringItem, detect_recurring, project_occurrences


def _monthly(payee, amounts, day=5, direction="debit", year=2026, first_month=5):
    return [Event(payee, date(year, first_month + i, day), amount, direction) for i, amount in enumerate(amounts)]


def test_rent_is_detected_as_recurring():
    items = detect_recurring(_monthly("landlord@upi", [18000, 18000, 18000, 18000]))
    assert len(items) == 1
    assert items[0].typical_amount == 18000 and items[0].typical_day == 5


def test_two_occurrences_are_not_enough():
    assert detect_recurring(_monthly("netflix", [649, 649])) == []


def test_a_changing_bill_is_not_recurring():
    assert detect_recurring(_monthly("bescom", [1200, 2600, 900, 3100])) == []


def test_irregular_timing_is_not_recurring():
    events = [Event("shop", date(2026, 5, 1), 500, "debit"), Event("shop", date(2026, 5, 9), 500, "debit"),
              Event("shop", date(2026, 5, 20), 500, "debit")]
    assert detect_recurring(events) == []


def test_salary_is_recurring_credit():
    items = detect_recurring(_monthly("acme payroll", [60000] * 4, day=1, direction="credit"))
    assert items[0].direction == "credit"


def test_projection_lists_future_dates_only():
    item = RecurringItem("rent", "debit", 18000, 5, 4, last_seen=date(2026, 8, 5))
    assert list(project_occurrences(item, date(2026, 8, 20), date(2026, 11, 30))) == [
        date(2026, 9, 5), date(2026, 10, 5), date(2026, 11, 5)]


def test_projection_moves_day_31_to_month_end():
    item = RecurringItem("emi", "debit", 5000, 31, 4, last_seen=date(2026, 8, 31))
    assert list(project_occurrences(item, date(2026, 9, 1), date(2026, 11, 30))) == [
        date(2026, 9, 30), date(2026, 10, 31), date(2026, 11, 30)]


def test_projection_crosses_the_year_boundary():
    item = RecurringItem("sip", "debit", 3000, 10, 5, last_seen=date(2026, 11, 10))
    assert list(project_occurrences(item, date(2026, 12, 1), date(2027, 1, 31))) == [
        date(2026, 12, 10), date(2027, 1, 10)]
```
