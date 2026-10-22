# backend/evaluation/evaluate_forecast.py
#
# Purpose: MEASURE the Financial Forecast Agent for the paper (Phase 5, step F6).
# Like evaluate_budget.py it runs the REAL service (generate_forecast, with
# prepare_history, recurrence detection, the statement opening balance and the
# profile income) on a throwaway in-memory SQLite database, here loaded with the
# 24-month synthetic panel from scripts/make_synthetic_panel.py (30 users, 4 personas).
#
# Protocol: TIME-BASED rolling origin, never a random split.
#   origins   as_of = the 15th of every month that has >= 6 complete months before
#             it and whose 90-day window still ends inside the panel
#             (2025-04-15 ... 2026-06-15: 15 origins)
#   horizons  30, 60 and 90 days on the SAME origins, so horizons are comparable
#   history   only rows on or before as_of exist for the service (it filters by date);
#             the truth for each window is read from the panel rows after as_of
#   profile   at every origin the profile income = suggest_income() (Phase 4 P5), as
#             a real user would have it; the freelancer has no salary to detect, so
#             the forecast uses it
#   balance   NOT passed in: the service reads the latest statement balance
#
# What is compared (experiment / method):
#   variable_spend   B0 naive_last, B1 moving_average_3, B2 seasonal_naive_12 (>= 12 months),
#                    B3 exp_smoothing, each as a FIXED model with its own residual interval,
#                    vs S = per-user backtest selection (the agent). Same decomposed series,
#                    same target: window expenses that are not regular payments of a detected
#                    recurring fixed bill (the rule the series itself was built with)
#   total_expenses   S (recurring bills on their dates + variable) vs A (backtest selection
#                    on the RAW monthly expense total: no decomposition) vs B1 on the raw total
#   net_cashflow /   S (generate_forecast) vs A (monthly income and outflow forecast flat and
#   end_balance /    spread evenly over the days; no salary or rent dates)
#   cash_crunch
#   ablations        selection window 6/12 months; interval method (student_t default,
#                    empirical = the pre-F6.1 percentiles, conformal, fixed width);
#                    recurrence detector without the F6.1 frequency check (scored on targets
#                    that do not depend on detection: total expenses, net cash flow, crunch);
#                    history length 3/6/12 months (origins with >= 12 months only)
#   selection        regret vs the best fixed model, which model was chosen, low_confidence
#   recurrence       detected recurring items vs the generator's ground truth
#   llm              --llm none: template only (0 calls). --llm real: the guarded explainer
#                    on a SAMPLE (last origin x 30 days per user = 30 calls; synthetic data)
#
# Run (inside Docker):
#   docker compose exec backend python -m evaluation.evaluate_forecast
#   docker compose exec backend python -m evaluation.evaluate_forecast --llm real   (synthetic data only)
#
# Output (long format: llm_mode, experiment, method, config, horizon_days, persona, metric, value):
#   --llm none  evaluation/forecast_metrics.csv + forecast_metrics.md (committed; deterministic)
#   --llm real  evaluation/results/forecast_metrics_<timestamp>.csv + .md (gitignored)
#   always      evaluation/results/forecast_predictions.csv: one row per forecast (gitignored),
#               for significance tests later

import argparse
import bisect
import csv
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from statistics import mean

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registers every table)
from app.agents.forecast_explainer import ForecastExplainer
from app.core.categories import REFUND_CATEGORY
from app.core.config import settings
from app.core.money import to_money
from app.core.transaction_types import CREDIT, TXN_EXPENSE, TXN_INCOME, TXN_INVESTMENT_OUT
from app.db.base_class import Base
from app.models.transaction import Transaction
from app.models.user import User
from app.services import budget_service, forecast_service
from app.services.forecasting.backtest import (
    horizon_sum_errors, prediction_interval, select_best_model, weighted_sum,
)
from app.services.forecasting.cashflow_projector import ScheduledFlow, project_cashflow
from app.services.forecasting.data_preparation import (
    KIND_FIXED_EXPENSE, DetectedRecurring, HistoryRow, PreparedHistory, prepare_history, recurrence_key,
)
from app.services.forecasting.forecast_models import FALLBACK_MODEL, ModelSpec, default_model_registry
from app.services.forecasting.horizon import MonthSlice, horizon_slices
from app.services.forecasting.metrics import mae as series_mae
from app.services.forecasting.metrics import mape as series_mape
from app.services.forecasting.metrics import rmse as series_rmse
from app.services.forecasting.metrics import smape as series_smape
from app.services.forecasting.variable_forecast import MIN_TRAIN_MONTHS, forecast_variable
from app.services.narration_parser import parse_narration
from evaluation.evaluate_categorizer import overridden_settings
from evaluation.metrics import confusion, interval_coverage, mean_interval_width, selection_regret
from scripts.make_synthetic_panel import (
    DEFAULT_SEED, PanelRow, generate_panel, panel_months, panel_sha256, read_csv,
)

logger = logging.getLogger(__name__)

EVAL_DIR = Path(__file__).parent
RESULTS_DIR = EVAL_DIR / "results"
HORIZONS: tuple[int, ...] = (30, 60, 90)
ORIGIN_DAY = 15                       # forecasts are made mid-month, like a real user would
MIN_HISTORY_MONTHS = 6                # an origin needs this many complete months before it
LONG_HISTORY_MONTHS = 12              # origins used by the history-length ablation
HISTORY_LENGTHS: tuple[int, ...] = (3, 6, 12)
SELECT_WINDOWS: tuple[int, ...] = (6, 12)
INTERVAL_ABLATIONS: tuple[str, ...] = ("empirical", "conformal")      # the default is settings.FORECAST_INTERVAL_METHOD
OLD_DETECTOR = "detector=no_frequency_check"                          # config label of the pre-F6.1 detector
PROFILE_SAVINGS_PCT = Decimal("20")   # the profile needs one; the forecast does not use it
ALL = "all"                           # persona label for the pooled rows

# Fixed models (the baselines), in registry order, and their paper labels
FIXED_LABELS: dict[str, str] = {
    "naive_last": "B0", "moving_average_3": "B1", "seasonal_naive_12": "B2", "exp_smoothing": "B3",
}
ALWAYS_AVAILABLE = ("B0", "B1", "B3")  # need <= 3 months, so exist at every origin (regret uses these)
FIELDS = ("llm_mode", "experiment", "method", "config", "horizon_days", "persona", "metric", "value")


# ------------------------------------------------------------------ records of single forecasts


@dataclass(frozen=True)
class Prediction:
    """One interval forecast of one quantity, with what really happened."""
    experiment: str
    method: str
    config: str
    horizon_days: int
    persona: str
    user: str
    as_of: date
    actual: float
    point: float
    lower: float
    upper: float


@dataclass(frozen=True)
class Flag:
    """One yes/no forecast (cash crunch, recurring item) with the truth."""
    experiment: str
    method: str
    config: str
    horizon_days: int | str
    persona: str
    user: str
    as_of: date
    actual: bool
    predicted: bool


@dataclass(frozen=True)
class Choice:
    """Which model S chose for one forecast, and whether it was flagged low confidence."""
    horizon_days: int
    persona: str
    model_name: str
    low_confidence: bool
    llm_calls: int


# ------------------------------------------------------------------ origins


def panel_end(months: list[date]) -> date:
    last = months[-1]
    return date(last.year + (last.month == 12), last.month % 12 + 1, 1) - timedelta(days=1)


def default_origins(months: list[date] | None = None, min_history: int = MIN_HISTORY_MONTHS) -> list[date]:
    """
    The 15th of every panel month that has >= `min_history` complete months before
    it and whose longest window (90 days) ends inside the panel. At the 15th of
    month index m the history is exactly months 0..m-1, i.e. m months.
    """
    months = months or panel_months()
    end = panel_end(months)
    return [
        first.replace(day=ORIGIN_DAY)
        for index, first in enumerate(months)
        if index >= min_history and first.replace(day=ORIGIN_DAY) + timedelta(days=max(HORIZONS)) <= end
    ]


# ------------------------------------------------------------------ database


def load_db(rows: list[PanelRow]) -> tuple[Session, dict[str, int]]:
    """
    A fresh in-memory DB with one user per panel user. Rows are inserted in
    statement order, so on a day with several rows the highest id carries the
    end-of-day balance (what resolve_opening_balance reads). payee_key is set the
    way the import pipeline sets it (narration parser).
    """
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    users: dict[str, int] = {}
    for user in dict.fromkeys(r.user for r in rows):
        record = User(email=f"{user}@synthetic.example", full_name=f"Synthetic {user}", hashed_password="x")
        db.add(record)
        db.flush()
        users[user] = record.id
    for r in rows:
        db.add(Transaction(
            user_id=users[r.user], description=r.description, amount=r.amount, direction=r.direction,
            txn_type=r.txn_type, category=r.category, balance=r.balance,
            payee_key=parse_narration(r.description).payee_key,
            transaction_date=datetime(r.day.year, r.day.month, r.day.day, 12, tzinfo=timezone.utc),
        ))
    db.commit()
    return db, users


@contextmanager
def explainer(factory: Callable[[], ForecastExplainer]) -> Iterator[list[ForecastExplainer]]:
    """Makes generate_forecast build its explainer with `factory`; yields every explainer built (for LLM calls)."""
    built: list[ForecastExplainer] = []

    def recording_factory() -> ForecastExplainer:
        built.append(factory())
        return built[-1]

    original = forecast_service._build_explainer
    forecast_service._build_explainer = recording_factory
    try:
        yield built
    finally:
        forecast_service._build_explainer = original


def set_profile_income(db: Session, user_id: int, as_of: date) -> None:
    """Profile income = the Phase 4 suggestion from the months before as_of (kept if there is none)."""
    suggestion = budget_service.suggest_income(db, user_id, f"{as_of:%Y-%m}")
    if suggestion["suggested_monthly_income"] is not None:
        budget_service.upsert_profile(db, user_id, to_money(suggestion["suggested_monthly_income"]),
                                      PROFILE_SAVINGS_PCT)


# ------------------------------------------------------------------ ground truth


class Ledger:
    """One user's panel rows with fast 'end-of-day balance on day d' lookups."""

    def __init__(self, rows: list[PanelRow]) -> None:
        self.rows = rows
        self.persona = rows[0].persona
        self._days = [r.day for r in rows]                     # rows are in statement order
        self._keys = {r.description: recurrence_key(parse_narration(r.description).payee_key, r.description)
                      for r in rows}

    def balance_on(self, day: date) -> float:
        """Balance after the last row on or before `day`."""
        index = bisect.bisect_right(self._days, day) - 1
        if index < 0:
            raise ValueError(f"no row on or before {day}")
        return float(self.rows[index].balance)

    def key(self, row: PanelRow) -> str:
        return self._keys[row.description]

    def between(self, start: date, end: date) -> list[PanelRow]:
        lo, hi = bisect.bisect_left(self._days, start), bisect.bisect_right(self._days, end)
        return self.rows[lo:hi]


@dataclass(frozen=True)
class Actuals:
    """What really happened in one forecast window."""
    total_expenses: float
    variable: float
    income: float
    investments: float
    end_balance: float
    min_balance: float

    @property
    def net_cashflow(self) -> float:
        """Same definition as the agent: income - expenses - investments (transfers and refunds are neither)."""
        return self.income - self.total_expenses - self.investments

    @property
    def crunch(self) -> bool:
        return self.min_balance < 0


def is_income(row: PanelRow) -> bool:
    """INCOME_FILTER in Python: income credits, refunds excluded."""
    return row.txn_type == TXN_INCOME and row.direction == CREDIT and row.category != REFUND_CATEGORY


def window_actuals(ledger: Ledger, start: date, end: date, fixed: list[DetectedRecurring]) -> Actuals:
    """Truth for [start, end]. Variable = expenses that are not regular payments of a detected fixed item."""
    total = variable = income = investments = 0.0
    for row in ledger.between(start, end):
        amount = float(row.amount)
        if row.txn_type == TXN_EXPENSE:
            total += amount
            history_row = HistoryRow(row.day, amount, ledger.key(row), row.category)
            if not any(item.matches(history_row) for item in fixed):
                variable += amount
        elif row.txn_type == TXN_INVESTMENT_OUT:
            investments += amount
        elif is_income(row):
            income += amount
    days = (end - start).days + 1
    balances = [ledger.balance_on(start + timedelta(days=k)) for k in range(days)]
    return Actuals(total, variable, income, investments, balances[-1], min(balances))


def monthly_flows(ledger: Ledger, months: list[str]) -> tuple[list[float], list[float]]:
    """(income, investments) per history month, for the no-decomposition cash-flow baseline."""
    income = dict.fromkeys(months, 0.0)
    investments = dict.fromkeys(months, 0.0)
    for row in ledger.rows:
        key = f"{row.day:%Y-%m}"
        if key not in income:
            continue
        if is_income(row):
            income[key] += float(row.amount)
        elif row.txn_type == TXN_INVESTMENT_OUT:
            investments[key] += float(row.amount)
    return [income[m] for m in months], [investments[m] for m in months]


def truth_recurring_keys(ledger: Ledger, as_of: date) -> set[str]:
    """Ground-truth recurring payees with >= the detector's minimum occurrences on or before as_of."""
    counts: dict[str, int] = {}
    for row in ledger.rows:
        if row.recurring and row.day <= as_of:
            counts[ledger.key(row)] = counts.get(ledger.key(row), 0) + 1
    return {key for key, n in counts.items() if n >= settings.FORECAST_RECURRING_MIN_OCCURRENCES}


# ------------------------------------------------------------------ forecasters used only here


@dataclass(frozen=True)
class Interval:
    point: float
    lower: float
    upper: float


def fixed_model_forecast(series: list[float], weights: list[float], spec: ModelSpec) -> Interval | None:
    """
    A FIXED model, with the same point and interval maths the agent uses
    (variable_forecast.forecast_variable, minus the selection). None when the
    model needs more history than there is.
    """
    if len(series) < spec.min_history:
        return None
    monthly = [max(0.0, value) for value in spec.forecaster(series, len(weights))]
    point = weighted_sum(monthly, weights)
    errors = horizon_sum_errors(series, spec.forecaster, weights, max(MIN_TRAIN_MONTHS, spec.min_history))
    lower, upper, _ = prediction_interval(
        point, errors, coverage=settings.FORECAST_INTERVAL_COVERAGE,
        min_residuals=settings.FORECAST_MIN_INTERVAL_ERRORS,
        fallback_relative_width=settings.FORECAST_FALLBACK_INTERVAL_WIDTH, method=settings.FORECAST_INTERVAL_METHOD,
    )
    return Interval(point, lower, upper)


def selected_on_window(series: list[float], weights: list[float], window: int) -> Interval:
    """Ablation: the model is chosen by a backtest on the last `window` months only, then forecasts from all."""
    registry = default_model_registry()
    selection = select_best_model(series[-window:], registry, horizon=1, min_train=MIN_TRAIN_MONTHS,
                                  min_folds=settings.FORECAST_MIN_BACKTEST_FOLDS)
    spec = registry[selection.best.model_name] if selection else registry[FALLBACK_MODEL]
    return fixed_model_forecast(series, weights, spec)   # MA-3 / any selected model always fits here


def agent_variable(series: list[float], weights: list[float]) -> Interval:
    """S on any series: the agent's own variable forecaster with the configured settings."""
    forecast = forecast_variable(
        series, weights, coverage=settings.FORECAST_INTERVAL_COVERAGE, min_folds=settings.FORECAST_MIN_BACKTEST_FOLDS,
        min_interval_errors=settings.FORECAST_MIN_INTERVAL_ERRORS,
        fallback_width=settings.FORECAST_FALLBACK_INTERVAL_WIDTH, interval_method=settings.FORECAST_INTERVAL_METHOD,
    )
    return Interval(forecast.point, forecast.lower, forecast.upper)


@dataclass(frozen=True)
class FlatProjection:
    """The no-decomposition cash-flow baseline's answer for one window."""
    net: Interval
    end_balance: Interval
    crunch_lower: bool
    crunch_point: bool


def flat_projection(
    income_series: list[float], outflow_series: list[float], slices: list[MonthSlice],
    start: date, end: date, opening: float,
) -> FlatProjection:
    """
    Baseline A: income and outflow (expenses + investments) are each forecast as
    monthly totals by backtest selection and spread EVENLY over the days. It knows
    nothing about paydays or rent dates; the band comes from the outflow interval.
    """
    weights = [s.weight for s in slices]
    settings_kwargs = dict(
        coverage=settings.FORECAST_INTERVAL_COVERAGE, min_folds=settings.FORECAST_MIN_BACKTEST_FOLDS,
        min_interval_errors=settings.FORECAST_MIN_INTERVAL_ERRORS,
        fallback_width=settings.FORECAST_FALLBACK_INTERVAL_WIDTH, interval_method=settings.FORECAST_INTERVAL_METHOD,
    )
    income = forecast_variable(income_series, weights, **settings_kwargs)
    outflow = forecast_variable(outflow_series, weights, **settings_kwargs)
    per_day = {s.key: value / s.days_in_month for s, value in zip(slices, income.monthly)}
    flows = []
    day = start
    while day <= end:
        flows.append(ScheduledFlow(day, per_day[f"{day:%Y-%m}"], CREDIT))
        day += timedelta(days=1)
    projection = project_cashflow(start, end, opening, slices, outflow.monthly, outflow.lower, outflow.upper, flows)
    net_point = income.point - outflow.point
    net = Interval(net_point, net_point - (outflow.upper - outflow.point), net_point + (outflow.point - outflow.lower))
    last = projection.end
    return FlatProjection(
        net=net, end_balance=Interval(last.balance, last.lower, last.upper),
        crunch_lower=projection.cash_crunch_date is not None,
        crunch_point=any(p.balance < 0 for p in projection.trajectory),
    )


# ------------------------------------------------------------------ one user at one origin


@dataclass
class Collected:
    predictions: list[Prediction]
    flags: list[Flag]
    choices: list[Choice]


def evaluate_origin(
    db: Session, user_id: int, ledger: Ledger, user: str, as_of: date, horizons: tuple[int, ...],
    long_history: bool, out: Collected,
) -> None:
    """Every method, every horizon, for one user at one origin."""
    persona = ledger.persona
    set_profile_income(db, user_id, as_of)
    history: PreparedHistory = prepare_history(db, user_id, as_of)
    fixed = [r for r in history.recurring if r.kind == KIND_FIXED_EXPENSE]
    registry = default_model_registry()

    # Recurrence detection vs ground truth (once per origin, not per horizon)
    detected = {r.item.payee_key for r in history.recurring}
    truth = truth_recurring_keys(ledger, as_of)
    for key in sorted(detected | truth):
        out.flags.append(Flag("recurrence", "detector", "default", "", persona, user, as_of, key in truth, key in detected))
    with overridden_settings({"FORECAST_RECURRING_FREQUENCY_CHECK": False}):
        old_detected = {r.item.payee_key for r in prepare_history(db, user_id, as_of).recurring}
    for key in sorted(old_detected | truth):
        out.flags.append(Flag("recurrence", "detector", OLD_DETECTOR, "", persona, user, as_of, key in truth,
                              key in old_detected))

    income_series, investment_series = monthly_flows(ledger, history.months)
    outflow_series = [e + i for e, i in zip(history.total_expense_series, investment_series)]

    for horizon in horizons:
        start, end = as_of + timedelta(days=1), as_of + timedelta(days=horizon)
        slices = horizon_slices(history.first_forecast_month, start, end)
        weights = [s.weight for s in slices]
        actual = window_actuals(ledger, start, end, fixed)

        def add(experiment: str, method: str, config: str, truth_value: float, interval: Interval | None) -> None:
            if interval is not None:
                out.predictions.append(Prediction(experiment, method, config, horizon, persona, user, as_of,
                                                  truth_value, interval.point, interval.lower, interval.upper))

        # --- S: the real service, end to end ---
        with explainer(lambda: ForecastExplainer(chains=[])) as built:
            run = forecast_service.generate_forecast(db, user_id, horizon, as_of)
        variable = run["expected_variable_expenses"]
        s_var = Interval(variable["point"], variable["lower"], variable["upper"])
        out.choices.append(Choice(horizon, persona, run["model_name"], run["low_confidence"],
                                  sum(e.llm_calls for e in built)))
        fixed_total = run["expected_fixed_expenses"]
        net = run["net_cashflow"]
        band_up, band_down = s_var.upper - s_var.point, s_var.point - s_var.lower
        end_balance = run["projected_end_balance"]

        # --- variable spend: fixed models vs selection, plus ablations ---
        for name, label in FIXED_LABELS.items():
            add("variable_spend", label, "fixed", actual.variable, fixed_model_forecast(history.variable_series, weights, registry[name]))
        add("variable_spend", "S", "default", actual.variable, s_var)
        for window in SELECT_WINDOWS:
            add("variable_spend", "S", f"select_window={window}", actual.variable,
                selected_on_window(history.variable_series, weights, window))
        for method in INTERVAL_ABLATIONS:
            with overridden_settings({"FORECAST_INTERVAL_METHOD": method}):
                add("variable_spend", "S", f"interval={method}", actual.variable,
                    agent_variable(history.variable_series, weights))
        spread = s_var.point * settings.FORECAST_FALLBACK_INTERVAL_WIDTH
        add("variable_spend", "S", "interval=fixed_width", actual.variable,
            Interval(s_var.point, max(0.0, s_var.point - spread), s_var.point + spread))

        # --- total expenses: decomposition on/off ---
        add("total_expenses", "S", "decomposed", actual.total_expenses,
            Interval(fixed_total + s_var.point, fixed_total + s_var.lower, fixed_total + s_var.upper))
        add("total_expenses", "A", "no_decomposition", actual.total_expenses,
            agent_variable(history.total_expense_series, weights))
        add("total_expenses", "B1", "no_decomposition", actual.total_expenses,
            fixed_model_forecast(history.total_expense_series, weights, registry["moving_average_3"]))

        # --- net cash flow, end balance, cash crunch ---
        flat = flat_projection(income_series, outflow_series, slices, start, end, run["opening_balance"])
        add("net_cashflow", "S", "decomposed", actual.net_cashflow, Interval(net, net - band_up, net + band_down))
        add("net_cashflow", "A", "no_decomposition", actual.net_cashflow, flat.net)
        add("end_balance", "S", "decomposed", actual.end_balance,
            Interval(end_balance["point"], end_balance["lower"], end_balance["upper"]))
        add("end_balance", "A", "no_decomposition", actual.end_balance, flat.end_balance)
        s_point_crunch = any(p["balance"] < 0 for p in run["trajectory"])
        for method, config, predicted in (
            ("S", "lower_band", run["cash_crunch_date"] is not None), ("S", "point", s_point_crunch),
            ("A", "lower_band", flat.crunch_lower), ("A", "point", flat.crunch_point),
        ):
            out.flags.append(Flag("cash_crunch", method, config, horizon, persona, user, as_of, actual.crunch, predicted))

        # --- detector ablation: the whole service with the pre-F6.1 recurrence rule ---
        with overridden_settings({"FORECAST_RECURRING_FREQUENCY_CHECK": False}),                 explainer(lambda: ForecastExplainer(chains=[])):
            old = forecast_service.generate_forecast(db, user_id, horizon, as_of)
        old_var = old["expected_variable_expenses"]
        old_fixed, old_net = old["expected_fixed_expenses"], old["net_cashflow"]
        add("total_expenses", "S", OLD_DETECTOR, actual.total_expenses,
            Interval(old_fixed + old_var["point"], old_fixed + old_var["lower"], old_fixed + old_var["upper"]))
        add("net_cashflow", "S", OLD_DETECTOR, actual.net_cashflow,
            Interval(old_net, old_net - (old_var["upper"] - old_var["point"]), old_net + (old_var["point"] - old_var["lower"])))
        out.flags.append(Flag("cash_crunch", "S", OLD_DETECTOR, horizon, persona, user, as_of, actual.crunch,
                              old["cash_crunch_date"] is not None))

        # --- history length ablation (only where >= 12 months exist, same origins for every length) ---
        if long_history:
            for length in HISTORY_LENGTHS:
                series = history.variable_series[-length:]
                for name, label in FIXED_LABELS.items():
                    add("history_length", label, f"months={length}", actual.variable,
                        fixed_model_forecast(series, weights, registry[name]))
                add("history_length", "S", f"months={length}", actual.variable, agent_variable(series, weights))


# ------------------------------------------------------------------ LLM sample


def llm_sample(
    db: Session, users: dict[str, int], ledgers: dict[str, Ledger], as_of: date,
    factory: Callable[[], ForecastExplainer],
) -> list[dict]:
    """One 30-day forecast per user at `as_of` with the LLM explainer: faithfulness and calls per forecast."""
    records: list[dict] = []
    for user, user_id in users.items():
        set_profile_income(db, user_id, as_of)
        with explainer(factory) as built:
            run = forecast_service.generate_forecast(db, user_id, 30, as_of)
        faithful = run["explanation_faithful"]
        for metric, value in (
            ("llm_used", float(run["llm_used"])),
            ("explanation_faithful", float("nan") if faithful is None else float(faithful)),
            ("llm_calls_per_forecast", float(sum(e.llm_calls for e in built))),
        ):
            records.append({"experiment": "llm", "method": "S", "config": "explainer", "horizon_days": 30,
                            "persona": ledgers[user].persona, "user": user, "metric": metric, "value": value})
    return records


# ------------------------------------------------------------------ aggregation


def _nan_if_none(value: float | None) -> float:
    return float("nan") if value is None else value


def _record(experiment: str, method: str, config: str, horizon, persona: str, metric: str, value: float) -> dict:
    return {"experiment": experiment, "method": method, "config": config, "horizon_days": horizon,
            "persona": persona, "metric": metric, "value": value}


def _groups(items, key: Callable) -> dict:
    """Groups items by key(item) and ALSO pools every persona under ALL (key's last element is the persona)."""
    groups: dict[tuple, list] = {}
    for item in items:
        k = key(item)
        groups.setdefault(k, []).append(item)
        groups.setdefault((*k[:-1], ALL), []).append(item)
    return groups


def aggregate_predictions(predictions: list[Prediction]) -> list[dict]:
    """Point and interval metrics per experiment / method / config / horizon / persona (+ ALL)."""
    records: list[dict] = []
    for (experiment, method, config, horizon, persona), group in sorted(
        _groups(predictions, lambda p: (p.experiment, p.method, p.config, p.horizon_days, p.persona)).items()
    ):
        actual, point = [p.actual for p in group], [p.point for p in group]
        lower, upper = [p.lower for p in group], [p.upper for p in group]
        for metric, value in (
            ("n", float(len(group))), ("mae", series_mae(actual, point)), ("rmse", series_rmse(actual, point)),
            ("mape", _nan_if_none(series_mape(actual, point))), ("smape", series_smape(actual, point)),
            ("coverage", interval_coverage(actual, lower, upper)), ("width", mean_interval_width(lower, upper)),
            ("mean_actual", mean(actual)),
        ):
            records.append(_record(experiment, method, config, horizon, persona, metric, value))
    return records


def aggregate_flags(flags: list[Flag]) -> list[dict]:
    """Confusion counts, precision, recall, F1 and the base rate of the event."""
    records: list[dict] = []
    for (experiment, method, config, horizon, persona), group in sorted(
        _groups(flags, lambda f: (f.experiment, f.method, f.config, f.horizon_days, f.persona)).items(),
        key=lambda item: tuple(str(part) for part in item[0]),
    ):
        scores = confusion([f.actual for f in group], [f.predicted for f in group])
        scores["actual_rate"] = sum(f.actual for f in group) / len(group)
        scores["n"] = float(len(group))
        for metric, value in scores.items():
            records.append(_record(experiment, method, config, horizon, persona, metric, value))
    return records


def aggregate_selection(predictions: list[Prediction], choices: list[Choice]) -> list[dict]:
    """Regret of S vs the best always-available fixed model, chosen-model shares and the low-confidence rate."""
    records: list[dict] = []
    variable = [p for p in predictions if p.experiment == "variable_spend"]
    for (horizon, persona), group in sorted(_groups(variable, lambda p: (p.horizon_days, p.persona)).items(),
                                            key=lambda item: (item[0][0], str(item[0][1]))):
        def error(method: str, config: str) -> float:
            chosen = [p for p in group if p.method == method and p.config == config]
            return series_mae([p.actual for p in chosen], [p.point for p in chosen])

        regret, best = selection_regret(error("S", "default"), {m: error(m, "fixed") for m in ALWAYS_AVAILABLE})
        records.append(_record("selection", "S", "default", horizon, persona, "regret_mae", regret))
        records.append(_record("selection", "S", "default", horizon, persona, f"best_fixed={best}", 1.0))
    for (horizon, persona), group in sorted(_groups(choices, lambda c: (c.horizon_days, c.persona)).items(),
                                            key=lambda item: (item[0][0], str(item[0][1]))):
        for name, label in FIXED_LABELS.items():
            share = sum(c.model_name == name for c in group) / len(group)
            records.append(_record("selection", "S", "default", horizon, persona, f"chosen_share_{label}", share))
        records.append(_record("selection", "S", "default", horizon, persona, "low_confidence_rate",
                               sum(c.low_confidence for c in group) / len(group)))
        records.append(_record("llm", "S", "template", horizon, persona, "llm_calls_per_forecast",
                               mean(c.llm_calls for c in group)))
    return records


def aggregate_llm(rows: list[dict]) -> list[dict]:
    records: list[dict] = []
    for (metric, persona), group in sorted(_groups(rows, lambda r: (r["metric"], r["persona"])).items()):
        values = [r["value"] for r in group if r["value"] == r["value"]]          # drop NaN (no LLM text)
        records.append(_record("llm", "S", "explainer", 30, persona, metric, mean(values) if values else float("nan")))
    return records


# ------------------------------------------------------------------ run


@dataclass
class EvaluationResult:
    records: list[dict]
    predictions: list[Prediction]
    flags: list[Flag]
    info: dict


def run_evaluation(
    rows: list[PanelRow],
    origins: list[date] | None = None,
    horizons: tuple[int, ...] = HORIZONS,
    llm_mode: str = "none",
    llm_factory: Callable[[], ForecastExplainer] | None = None,
    out_csv: Path | None = None,
    out_md: Path | None = None,
    out_predictions: Path | None = None,
) -> EvaluationResult:
    """
    Runs every origin x user x horizon and writes the outputs that are given.
    The LLM sample runs when llm_mode='real' (the configured provider) or when
    `llm_factory` is given (tests inject a fake LLM).
    """
    started = time.perf_counter()
    origins = origins or default_origins()
    db, users = load_db(rows)
    by_user: dict[str, list[PanelRow]] = {}
    for row in rows:
        by_user.setdefault(row.user, []).append(row)
    ledgers = {user: Ledger(user_rows) for user, user_rows in by_user.items()}

    collected = Collected([], [], [])
    try:
        for as_of in origins:
            long_history = len(panel_months_before(rows, as_of)) >= LONG_HISTORY_MONTHS
            for user, user_id in users.items():
                evaluate_origin(db, user_id, ledgers[user], user, as_of, horizons, long_history, collected)
        llm_rows: list[dict] = []
        if llm_mode == "real" or llm_factory is not None:
            llm_rows = llm_sample(db, users, ledgers, origins[-1], llm_factory or ForecastExplainer.from_settings)
    finally:
        db.close()

    records = (aggregate_predictions(collected.predictions) + aggregate_flags(collected.flags)
               + aggregate_selection(collected.predictions, collected.choices) + aggregate_llm(llm_rows))
    for record in records:
        record["llm_mode"] = llm_mode
    info = {
        "users": len(users), "rows": len(rows), "sha256": panel_sha256(rows),
        "personas": {p: sum(1 for l in ledgers.values() if l.persona == p) for p in dict.fromkeys(l.persona for l in ledgers.values())},
        "origins": origins, "horizons": horizons, "forecasts": len(collected.choices),
        "long_origins": [o for o in origins if len(panel_months_before(rows, o)) >= LONG_HISTORY_MONTHS],
        "seconds": time.perf_counter() - started,
    }
    result = EvaluationResult(records, collected.predictions, collected.flags, info)
    if out_csv is not None:
        write_csv(out_csv, records)
    if out_md is not None:
        out_md.parent.mkdir(parents=True, exist_ok=True)
        out_md.write_text(markdown_report(result), encoding="utf-8", newline="\n")
    if out_predictions is not None:
        write_predictions(out_predictions, collected.predictions)
    return result


def panel_months_before(rows: list[PanelRow], as_of: date) -> list[str]:
    """Complete calendar months with data strictly before as_of's month (the history the service can use)."""
    first = min(r.day for r in rows).replace(day=1)
    months, current = [], first
    while current < as_of.replace(day=1):
        months.append(f"{current:%Y-%m}")
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return months


def _fmt(value: float) -> str:
    """Stable text for the CSV: 4 decimals, blank for 'not defined'."""
    return "" if value != value else f"{value:.4f}"      # NaN != NaN


def write_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        for record in records:
            writer.writerow({**{k: record[k] for k in FIELDS}, "value": _fmt(record["value"])})


def write_predictions(path: Path, predictions: list[Prediction]) -> None:
    """One row per forecast, for significance tests (e.g. Diebold-Mariano) later."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(Prediction.__dataclass_fields__)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for p in predictions:
            row = asdict(p)
            for key in ("actual", "point", "lower", "upper"):
                row[key] = f"{row[key]:.2f}"
            writer.writerow(row)


# ------------------------------------------------------------------ markdown for the paper


def _get(records: list[dict], experiment: str, method: str, config: str, horizon, persona: str, metric: str) -> float:
    for r in records:
        if (r["experiment"], r["method"], r["config"], r["horizon_days"], r["persona"], r["metric"]) == (
            experiment, method, config, horizon, persona, metric
        ):
            return r["value"]
    return float("nan")


def _rupees(value: float) -> str:
    return "n/a" if value != value else f"₹{round(value):,}"      # round() first: no "₹-0"


def _pct(value: float) -> str:
    return "n/a" if value != value else f"{value * 100:.1f}%"


def _num(value: float, digits: int = 1) -> str:
    return "n/a" if value != value else f"{value:.{digits}f}"


def markdown_report(result: EvaluationResult) -> str:
    """Short tables for the paper, built only from the records (so they always match the CSV)."""
    records, info = result.records, result.info
    horizons = info["horizons"]
    personas = list(info["personas"])
    origins = info["origins"]
    get = lambda *key: _get(records, *key)  # noqa: E731
    lines = [
        "# Financial Forecast Agent: evaluation results",
        "",
        f"Synthetic panel: {info['users']} users ({', '.join(f'{p} {n}' for p, n in info['personas'].items())}), "
        f"{info['rows']:,} rows, seed {DEFAULT_SEED}, SHA-256 `{info['sha256'][:16]}…`. "
        f"Time-based rolling origin: {len(origins)} origins ({origins[0]} … {origins[-1]}, the 15th of each month), "
        f"horizons {', '.join(map(str, horizons))} days, {info['forecasts']:,} forecasts through the real "
        f"`generate_forecast`. LLM mode: {records[0]['llm_mode']}.",
        "",
        "## 1. Variable spending: fixed models vs per-user backtest selection (all users)",
        "",
        "Target: window spending that is not a regular payment of a detected recurring bill. "
        "B2 needs 12 months, so its n is smaller (later origins only).",
        "",
        "| Horizon | Method | n | MAE | RMSE | sMAPE | MAPE | 80% coverage | Mean width |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for h in horizons:
        for method, config in (*((label, "fixed") for label in FIXED_LABELS.values()), ("S", "default")):
            k = ("variable_spend", method, config, h, ALL)
            lines.append(f"| {h} | {method} {'(selection)' if method == 'S' else config} | {get(*k, 'n'):.0f} | "
                         f"{_rupees(get(*k, 'mae'))} | {_rupees(get(*k, 'rmse'))} | {_num(get(*k, 'smape'))} | "
                         f"{_num(get(*k, 'mape'))} | {_pct(get(*k, 'coverage'))} | {_rupees(get(*k, 'width'))} |")

    lines += ["", "## 2. Variable-spend MAE per persona", "",
              "| Persona | Horizon | B0 | B1 | B2 | B3 | S | Regret (S − best of B0/B1/B3) | Best fixed |",
              "|---|---|---|---|---|---|---|---|---|"]
    for persona in [*personas, ALL]:
        for h in horizons:
            cells = [_rupees(get("variable_spend", m, "fixed", h, persona, "mae")) for m in FIXED_LABELS.values()]
            best = next((r["metric"].split("=")[1] for r in records if r["experiment"] == "selection"
                         and r["horizon_days"] == h and r["persona"] == persona and r["metric"].startswith("best_fixed=")), "n/a")
            lines.append(f"| {persona} | {h} | {' | '.join(cells)} | "
                         f"{_rupees(get('variable_spend', 'S', 'default', h, persona, 'mae'))} | "
                         f"{_rupees(get('selection', 'S', 'default', h, persona, 'regret_mae'))} | {best} |")

    lines += ["", "## 3. Which model the agent chose (share of forecasts, all users)", "",
              "| Horizon | B0 naive | B1 MA-3 | B2 seasonal | B3 exp. smoothing | Low confidence |", "|---|---|---|---|---|---|"]
    for h in horizons:
        shares = [_pct(get("selection", "S", "default", h, ALL, f"chosen_share_{label}")) for label in FIXED_LABELS.values()]
        lines.append(f"| {h} | {' | '.join(shares)} | {_pct(get('selection', 'S', 'default', h, ALL, 'low_confidence_rate'))} |")

    lines += ["", "## 4. Decomposition ablation (all users)", "",
              "S = recurring items on their dates + selected model on the variable rest. "
              "A = selection on the raw monthly total (expenses), or flat monthly income/outflow spread over the days "
              "(net cash flow, end balance).", "",
              "| Quantity | Horizon | Method | MAE | sMAPE | 80% coverage | Mean width |", "|---|---|---|---|---|---|---|"]
    for experiment, methods in (
        ("total_expenses", (("S", "decomposed"), ("A", "no_decomposition"), ("B1", "no_decomposition"))),
        ("net_cashflow", (("S", "decomposed"), ("A", "no_decomposition"))),
        ("end_balance", (("S", "decomposed"), ("A", "no_decomposition"))),
    ):
        for h in horizons:
            for method, config in methods:
                k = (experiment, method, config, h, ALL)
                lines.append(f"| {experiment} | {h} | {method} {config} | {_rupees(get(*k, 'mae'))} | "
                             f"{_num(get(*k, 'smape'))} | {_pct(get(*k, 'coverage'))} | {_rupees(get(*k, 'width'))} |")

    lines += ["", "## 5. Cash-crunch warning (balance below zero inside the window)", "",
              "`lower_band` = the service's rule (the LOWER balance band goes below 0); `point` = the point trajectory does.",
              "", "| Horizon | Method | Event rate | TP | FP | FN | TN | Precision | Recall | F1 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for h in horizons:
        for method, config in (("S", "lower_band"), ("S", "point"), ("A", "lower_band"), ("A", "point")):
            k = ("cash_crunch", method, config, h, ALL)
            lines.append(f"| {h} | {method} {config} | {_pct(get(*k, 'actual_rate'))} | {get(*k, 'tp'):.0f} | "
                         f"{get(*k, 'fp'):.0f} | {get(*k, 'fn'):.0f} | {get(*k, 'tn'):.0f} | {_pct(get(*k, 'precision'))} | "
                         f"{_pct(get(*k, 'recall'))} | {_pct(get(*k, 'f1'))} |")
    lines += ["", "| Persona | 30-day event rate | S lower_band recall | S lower_band precision |", "|---|---|---|---|"]
    for persona in personas:
        k = ("cash_crunch", "S", "lower_band", horizons[0], persona)
        lines.append(f"| {persona} | {_pct(get(*k, 'actual_rate'))} | {_pct(get(*k, 'recall'))} | {_pct(get(*k, 'precision'))} |")

    lines += ["", f"## 6. Ablations on S (variable spending, all users; default interval = "
              f"{settings.FORECAST_INTERVAL_METHOD})", "",
              "| Horizon | Configuration | MAE | 80% coverage | Mean width |", "|---|---|---|---|---|"]
    for h in horizons:
        for config in ("default", *(f"select_window={w}" for w in SELECT_WINDOWS),
                       *(f"interval={m}" for m in INTERVAL_ABLATIONS), "interval=fixed_width"):
            k = ("variable_spend", "S", config, h, ALL)
            lines.append(f"| {h} | {config} | {_rupees(get(*k, 'mae'))} | {_pct(get(*k, 'coverage'))} | "
                         f"{_rupees(get(*k, 'width'))} |")

    long_origins = info["long_origins"]
    if long_origins:
        lines += ["", f"## 7. History length (variable-spend MAE; {len(long_origins)} origins with ≥ 12 months: "
                  f"{long_origins[0]} … {long_origins[-1]})", "",
                  "| Horizon | Months of history | B0 | B1 | B2 | B3 | S | S coverage |", "|---|---|---|---|---|---|---|---|"]
        for h in horizons:
            for length in HISTORY_LENGTHS:
                cells = [_rupees(get("history_length", m, f"months={length}", h, ALL, "mae")) for m in FIXED_LABELS.values()]
                lines.append(f"| {h} | {length} | {' | '.join(cells)} | "
                             f"{_rupees(get('history_length', 'S', f'months={length}', h, ALL, 'mae'))} | "
                             f"{_pct(get('history_length', 'S', f'months={length}', h, ALL, 'coverage'))} |")

    lines += ["", "## 8. Recurrence detection vs ground truth (all user-origins)", "",
              "| Persona | Precision | Recall | F1 | TP | FP | FN |", "|---|---|---|---|---|---|---|"]
    for persona in [*personas, ALL]:
        k = ("recurrence", "detector", "default", "", persona)
        lines.append(f"| {persona} | {_pct(get(*k, 'precision'))} | {_pct(get(*k, 'recall'))} | {_pct(get(*k, 'f1'))} | "
                     f"{get(*k, 'tp'):.0f} | {get(*k, 'fp'):.0f} | {get(*k, 'fn'):.0f} |")

    lines += ["", "## 9. Recurrence detector: with vs without the frequency check (F6.1)", "",
              "Scored on targets that do not depend on what was detected.", "",
              "| Detector | Recurrence precision | Recall | Horizon | Total-expense MAE | Total-expense coverage "
              "| Net cash-flow MAE | Crunch F1 (lower band) |", "|---|---|---|---|---|---|---|---|"]
    for label, config, crunch_config in (("frequency check (default)", "decomposed", "lower_band"),
                                         ("no frequency check (pre-F6.1)", OLD_DETECTOR, OLD_DETECTOR)):
        rec_config = "default" if config == "decomposed" else OLD_DETECTOR
        rec = ("recurrence", "detector", rec_config, "", ALL)
        for h in horizons:
            lines.append(f"| {label} | {_pct(get(*rec, 'precision'))} | {_pct(get(*rec, 'recall'))} | {h} | "
                         f"{_rupees(get('total_expenses', 'S', config, h, ALL, 'mae'))} | "
                         f"{_pct(get('total_expenses', 'S', config, h, ALL, 'coverage'))} | "
                         f"{_rupees(get('net_cashflow', 'S', config, h, ALL, 'mae'))} | "
                         f"{_pct(get('cash_crunch', 'S', crunch_config, h, ALL, 'f1'))} |")

    lines += ["", "## 10. LLM explanation", "",
              f"Main runs: template only, {_num(get('llm', 'S', 'template', horizons[0], ALL, 'llm_calls_per_forecast'), 0)} "
              "LLM calls per forecast."]
    if any(r["experiment"] == "llm" and r["config"] == "explainer" for r in records):
        k = ("llm", "S", "explainer", 30, ALL)
        lines += [f"Sample ({info['users']} users × 30 days at {origins[-1]}): LLM used {_pct(get(*k, 'llm_used'))}, "
                  f"faithful {_pct(get(*k, 'explanation_faithful'))} (of the texts the LLM wrote), "
                  f"{_num(get(*k, 'llm_calls_per_forecast'), 2)} calls per forecast."]
    else:
        lines += ["Faithfulness was not measured in this run (use `--llm real`, synthetic data only)."]

    lines += ["", "Caveats: synthetic data (do not claim real-world accuracy). The variable target uses the recurring "
              "items detected at each origin, like the training series. Actual end balances include flows the agent "
              "does not model (refunds, own-account top-ups and sweeps). Interval coverage is a target of 80%; "
              "read it next to the width.", ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the Financial Forecast Agent on the synthetic panel.")
    parser.add_argument("--data", type=Path, default=None,
                        help="panel CSV from scripts.make_synthetic_panel (default: generate it in memory, seed 42)")
    parser.add_argument("--llm", choices=("none", "real"), default="none",
                        help="'real' adds the LLM explanation sample with the configured provider (SYNTHETIC data only)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    rows = read_csv(args.data) if args.data else generate_panel()
    if args.llm == "none":
        out_csv, out_md = EVAL_DIR / "forecast_metrics.csv", EVAL_DIR / "forecast_metrics.md"
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_csv, out_md = RESULTS_DIR / f"forecast_metrics_{stamp}.csv", RESULTS_DIR / f"forecast_metrics_{stamp}.md"

    result = run_evaluation(rows, llm_mode=args.llm, out_csv=out_csv, out_md=out_md,
                            out_predictions=RESULTS_DIR / "forecast_predictions.csv")
    print(markdown_report(result))
    print(f"Wrote {len(result.records)} metric rows to {out_csv} and the tables to {out_md} "
          f"({result.info['forecasts']} forecasts in {result.info['seconds']:.0f} s)")


if __name__ == "__main__":
    main()
