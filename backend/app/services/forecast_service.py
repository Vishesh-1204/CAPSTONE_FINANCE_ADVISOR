# app/services/forecast_service.py
#
# Purpose: business logic of the Financial Forecast Agent (Phase 5).
#
# generate_forecast() for one user, one horizon (30/60/90 days), one as_of date:
#   1. prepare the history (data_preparation: 422 if under 1 complete month)
#   2. opening balance (request -> latest statement balance -> 400) and income source
#   3. window = as_of+1 .. as_of+horizon, split into calendar months with weights
#   4. variable spending: backtest-selected model + horizon-sum interval
#   5. scheduled flows: active recurring income/bills/SIPs on their dates
#      (profile income, when no salary is detected, is spread evenly per day)
#   6. daily trajectory, end balance band, cash-crunch date (LOWER band < 0)
#   7. budget link: categories projected above their Phase 4 limits
#   8. explanation: template or ONE guarded LLM call
#   9. save a forecast_runs row and return it
#
# Python does all the maths; the LLM only explains. Every query filters by the
# user_id from the JWT; someone else's run id is a 404.

import calendar
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.agents.forecast_explainer import BudgetWarningFact, ForecastExplainer, ForecastFacts, RecurringFact
from app.core.config import settings
from app.core.money import to_money
from app.core.transaction_types import CREDIT, DEBIT
from app.models.forecast import INCOME_FROM_PROFILE, ForecastRun
from app.schemas.forecast import HORIZON_CHOICES
from app.services.budget_service import list_budgets
from app.services.forecasting.cashflow_projector import Projection, ScheduledFlow, project_cashflow
from app.services.forecasting.data_preparation import (
    KIND_FIXED_EXPENSE, KIND_INCOME, KIND_INVESTMENT, DetectedRecurring, IncomeBasis, PreparedHistory,
    detect_user_recurring, month_to_date_spending, prepare_history, resolve_income, resolve_opening_balance,
    variable_category_shares,
)
from app.services.forecasting.horizon import MonthSlice, horizon_slices
from app.services.forecasting.recurrence_detector import project_occurrences
from app.services.forecasting.variable_forecast import VariableForecast, forecast_variable

logger = logging.getLogger(__name__)

RECURRING_PREVIEW_DAYS = 30        # GET /recurring lists the dates expected in the next 30 days
_ZERO = Decimal("0.00")


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def resolve_as_of(as_of: date | None) -> date:
    """Today by default; a past date is allowed (demo/evaluation), a future one is a 422."""
    today = today_utc()
    if as_of is None:
        return today
    if as_of > today:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "as_of cannot be in the future.")
    return as_of


def _build_explainer() -> ForecastExplainer:
    """Replaced by a fake in the tests (monkeypatch), like budget_service._build_planner."""
    return ForecastExplainer.from_settings()


# --- Scheduled flows ---

def _scheduled_flows(
    recurring: list[DetectedRecurring], income: IncomeBasis, start: date, end: date
) -> list[tuple[str, ScheduledFlow]]:
    """(kind, flow) for every expected payment in [start, end]."""
    flows: list[tuple[str, ScheduledFlow]] = []
    for detected in recurring:
        if not detected.active:
            continue
        direction = CREDIT if detected.kind == KIND_INCOME else DEBIT
        for on in project_occurrences(detected.item, start, end):
            flows.append((detected.kind, ScheduledFlow(on, detected.item.typical_amount, direction)))
    if income.source == INCOME_FROM_PROFILE and income.monthly_amount > 0:
        # No payday is known, so the profile income is spread evenly over the days
        day = start
        while day <= end:
            per_day = float(income.monthly_amount) / calendar.monthrange(day.year, day.month)[1]
            flows.append((KIND_INCOME, ScheduledFlow(day, per_day, CREDIT)))
            day += timedelta(days=1)
    return flows


def _total(flows: list[tuple[str, ScheduledFlow]], kind: str) -> Decimal:
    return to_money(sum(flow.amount for k, flow in flows if k == kind))


# --- Budget link ---

def _budget_warnings(
    db: Session, user_id: int, history: PreparedHistory, slices: list[MonthSlice], variable: VariableForecast,
    as_of: date,
) -> list[dict]:
    """
    For each month in the window that has Phase 4 budgets, projects the WHOLE
    month per category and lists the categories above their limit:
        actual spent so far this month (only as_of's month)
      + that month's variable forecast for the days left after as_of x the category's share
      + regular fixed payments (rent, EMI) still due in that month.
    """
    shares = variable_category_shares(db, user_id, history, settings.BUDGET_LOOKBACK_MONTHS)
    month_to_date = month_to_date_spending(db, user_id, as_of)
    fixed = [r for r in history.recurring if r.kind == KIND_FIXED_EXPENSE and r.active and r.category]
    warnings: list[dict] = []
    for month_slice, monthly in zip(slices, variable.monthly):
        if month_slice.days_in_window == 0:
            continue
        budgets = list_budgets(db, user_id, month_slice.key)
        if not budgets:
            continue
        month_end = month_slice.first_day.replace(day=month_slice.days_in_month)
        rest_start = max(as_of + timedelta(days=1), month_slice.first_day)
        rest_fraction = ((month_end - rest_start).days + 1) / month_slice.days_in_month
        actual = month_to_date if month_slice.first_day <= as_of else {}
        scheduled: dict[str, float] = {}
        for item in fixed:
            due = len(list(project_occurrences(item.item, rest_start, month_end)))
            scheduled[item.category] = scheduled.get(item.category, 0.0) + due * item.item.typical_amount
        for budget in budgets:
            projected = to_money(
                actual.get(budget.category, 0.0)
                + monthly * rest_fraction * shares.get(budget.category, 0.0)
                + scheduled.get(budget.category, 0.0)
            )
            if projected > budget.limit_amount:
                warnings.append({
                    "month": month_slice.key, "category": budget.category, "projected": float(projected),
                    "limit": float(budget.limit_amount), "over_by": float(projected - budget.limit_amount),
                })
    return sorted(warnings, key=lambda w: (w["month"], -w["over_by"]))


# --- Serialisation ---

def _recurring_to_dict(detected: DetectedRecurring, projected: list[date]) -> dict:
    item = detected.item
    return {
        "key": item.payee_key, "kind": detected.kind, "category": detected.category,
        "typical_amount": item.typical_amount, "typical_day": item.typical_day, "occurrences": item.occurrences,
        "last_seen": item.last_seen.isoformat(), "active": detected.active,
        "projected_dates": [d.isoformat() for d in projected],
    }


def run_to_response(run: ForecastRun) -> dict:
    """A saved run in the ForecastRunResponse shape."""
    return {
        "id": run.id, "as_of": run.as_of, "horizon_days": run.horizon_days, "created_at": run.created_at,
        "model_name": run.model_name, "history_months": run.history_months, "low_confidence": run.low_confidence,
        "opening_balance": float(run.opening_balance), "opening_balance_source": run.opening_balance_source,
        "opening_balance_date": run.opening_balance_date, "income_source": run.income_source,
        "expected_income": float(run.expected_income), "expected_fixed_expenses": float(run.expected_fixed_expenses),
        "expected_variable_expenses": {
            "point": float(run.expected_variable_expenses),
            "lower": float(run.expected_variable_expenses_lower),
            "upper": float(run.expected_variable_expenses_upper),
        },
        "expected_investments": float(run.expected_investments), "net_cashflow": float(run.net_cashflow),
        "projected_end_balance": {
            "point": float(run.projected_end_balance),
            "lower": float(run.projected_end_balance_lower),
            "upper": float(run.projected_end_balance_upper),
        },
        "cash_crunch_date": run.cash_crunch_date, "summary": run.summary, "llm_used": run.llm_used,
        "explanation_faithful": run.explanation_faithful, "model_scores": run.model_scores,
        "recurring_items": run.recurring_items, "budget_warnings": run.budget_warnings,
        "trajectory": run.trajectory,
    }


def _history_item(run: ForecastRun) -> dict:
    return {
        "id": run.id, "as_of": run.as_of, "horizon_days": run.horizon_days, "model_name": run.model_name,
        "low_confidence": run.low_confidence, "net_cashflow": float(run.net_cashflow),
        "projected_end_balance": float(run.projected_end_balance), "cash_crunch_date": run.cash_crunch_date,
        "llm_used": run.llm_used, "created_at": run.created_at,
    }


# --- Generate ---

def generate_forecast(
    db: Session, user_id: int, horizon_days: int, as_of: date, opening_balance: Decimal | None = None
) -> dict:
    """Builds, explains and saves one forecast; returns it in the ForecastRunResponse shape."""
    _check_horizon(horizon_days)

    # 1-2. Inputs (422 for too little history first, then 400 for a missing balance)
    history = prepare_history(db, user_id, as_of)
    opening = resolve_opening_balance(db, user_id, as_of, opening_balance)
    income = resolve_income(db, user_id, history.recurring)

    # 3. The window, as weighted calendar months
    start, end = as_of + timedelta(days=1), as_of + timedelta(days=horizon_days)
    slices = horizon_slices(history.first_forecast_month, start, end)

    # 4. Variable spending with its interval
    variable = forecast_variable(
        history.variable_series, [s.weight for s in slices],
        coverage=settings.FORECAST_INTERVAL_COVERAGE, min_folds=settings.FORECAST_MIN_BACKTEST_FOLDS,
        min_interval_errors=settings.FORECAST_MIN_INTERVAL_ERRORS,
        fallback_width=settings.FORECAST_FALLBACK_INTERVAL_WIDTH,
    )

    # 5-6. Scheduled flows and the daily trajectory
    flows = _scheduled_flows(history.recurring, income, start, end)
    projection: Projection = project_cashflow(
        start, end, float(opening.amount), slices, variable.monthly, variable.lower, variable.upper,
        [flow for _, flow in flows],
    )
    expected_income = _total(flows, KIND_INCOME)
    fixed_expenses = _total(flows, KIND_FIXED_EXPENSE)
    investments = _total(flows, KIND_INVESTMENT)
    variable_point = to_money(variable.point)
    net_cashflow = expected_income - fixed_expenses - variable_point - investments
    end_point = projection.end

    # 7. Budget link
    warnings = _budget_warnings(db, user_id, history, slices, variable, as_of)

    # Recurring items with their dates inside the window (stale ones listed, never projected)
    recurring_json = [
        _recurring_to_dict(r, list(project_occurrences(r.item, start, end)) if r.active else [])
        for r in history.recurring
    ]

    # 8. Explanation (template or one guarded LLM call)
    facts = ForecastFacts(
        as_of=as_of, horizon_days=horizon_days, end_date=end, model_name=variable.model_name,
        history_months=history.history_months, low_confidence=variable.low_confidence,
        model_selected_by_backtest=variable.selected_by_backtest,
        opening_balance=opening.amount, opening_balance_source=opening.source, opening_balance_date=opening.on,
        income_source=income.source, expected_income=expected_income, fixed_expenses=fixed_expenses,
        variable_expenses=variable_point, variable_lower=to_money(variable.lower),
        variable_upper=to_money(variable.upper), investments=investments, net_cashflow=net_cashflow,
        end_balance=to_money(end_point.balance), end_balance_lower=to_money(end_point.lower),
        end_balance_upper=to_money(end_point.upper), cash_crunch_date=projection.cash_crunch_date,
        recurring=[
            RecurringFact(r.kind, r.category, to_money(r.item.typical_amount), r.item.typical_day)
            for r in history.recurring if r.active
        ],
        budget_warnings=[
            BudgetWarningFact(w["month"], w["category"], to_money(w["projected"]), to_money(w["limit"]),
                              to_money(w["over_by"]))
            for w in warnings
        ],
    )
    explanation = _build_explainer().explain(facts)

    # 9. Save
    run = ForecastRun(
        user_id=user_id, as_of=as_of, horizon_days=horizon_days, model_name=variable.model_name,
        history_months=history.history_months, low_confidence=variable.low_confidence,
        opening_balance=opening.amount, opening_balance_source=opening.source, opening_balance_date=opening.on,
        income_source=income.source, expected_income=expected_income, expected_fixed_expenses=fixed_expenses,
        expected_variable_expenses=variable_point, expected_variable_expenses_lower=facts.variable_lower,
        expected_variable_expenses_upper=facts.variable_upper, expected_investments=investments,
        net_cashflow=net_cashflow, projected_end_balance=facts.end_balance,
        projected_end_balance_lower=facts.end_balance_lower, projected_end_balance_upper=facts.end_balance_upper,
        cash_crunch_date=projection.cash_crunch_date, summary=explanation.summary, llm_used=explanation.llm_used,
        explanation_faithful=explanation.faithful,
        trajectory=[
            {"date": p.on.isoformat(), "balance": round(p.balance, 2), "lower": round(p.lower, 2),
             "upper": round(p.upper, 2)}
            for p in projection.trajectory
        ],
        recurring_items=recurring_json, budget_warnings=warnings, model_scores=variable.scores,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    logger.info(
        f"Forecast generated run={run.id} user_id={user_id} as_of={as_of} horizon={horizon_days} "
        f"model={variable.model_name} history_months={history.history_months} low_conf={variable.low_confidence} "
        f"crunch={projection.cash_crunch_date} warnings={len(warnings)} llm_used={explanation.llm_used} "
        f"faithful={explanation.faithful}"
    )
    return run_to_response(run)


# --- Read endpoints ---

def get_run(db: Session, user_id: int, run_id: int) -> dict:
    """One of the user's runs. Someone else's id is a 404, so we never reveal that it exists."""
    run = db.query(ForecastRun).filter(ForecastRun.id == run_id, ForecastRun.user_id == user_id).first()
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Forecast not found.")
    return run_to_response(run)


def _check_horizon(horizon_days: int) -> None:
    if horizon_days not in HORIZON_CHOICES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"horizon_days must be one of {HORIZON_CHOICES}.")


def latest_run(db: Session, user_id: int, horizon_days: int | None = None) -> dict:
    """The newest run (of one horizon, if given)."""
    if horizon_days is not None:
        _check_horizon(horizon_days)
    query = db.query(ForecastRun).filter(ForecastRun.user_id == user_id)
    if horizon_days is not None:
        query = query.filter(ForecastRun.horizon_days == horizon_days)
    run = query.order_by(ForecastRun.id.desc()).first()
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No forecast yet. Generate one with POST /api/forecast/generate.")
    return run_to_response(run)


def list_runs(db: Session, user_id: int, limit: int) -> list[dict]:
    """Summary lines, newest first (id order: created_at can tie within a second)."""
    runs = (
        db.query(ForecastRun).filter(ForecastRun.user_id == user_id)
        .order_by(ForecastRun.id.desc()).limit(limit).all()
    )
    return [_history_item(run) for run in runs]


def recurring_overview(db: Session, user_id: int, as_of: date) -> dict:
    """Detected recurring items (read-only) with the dates expected in the next 30 days."""
    start, end = as_of + timedelta(days=1), as_of + timedelta(days=RECURRING_PREVIEW_DAYS)
    items = [
        _recurring_to_dict(r, list(project_occurrences(r.item, start, end)) if r.active else [])
        for r in detect_user_recurring(db, user_id, as_of)
    ]
    return {"as_of": as_of, "items": items}
