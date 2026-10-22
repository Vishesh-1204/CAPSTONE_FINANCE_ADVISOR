# app/schemas/forecast.py
#
# Purpose: response shapes for the Phase 5 endpoints (/api/forecast/...).
# Responses use float, like the budget schemas, because JSON has no Decimal
# type; the database keeps the exact Numeric(14,2) values.
# horizon_days is validated in the routes with the HorizonDays Literal, so 45
# or 365 is rejected with a 422 before any code runs.

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

HorizonDays = Literal[30, 60, 90]
OpeningBalanceSource = Literal["request", "statement"]
IncomeSource = Literal["recurring", "profile", "none"]
# What a recurring item is: money in, a fixed bill (rent, EMI, subscription), or an investment (SIP)
RecurringKind = Literal["income", "fixed_expense", "investment"]


class _ForecastSchema(BaseModel):
    """
    Base for schemas with fields named model_name / model_scores. Pydantic
    reserves the "model_" prefix for its own methods and warns about it;
    clearing protected_namespaces says these names are intentional.
    """
    model_config = ConfigDict(protected_namespaces=())


class Band(BaseModel):
    """A point forecast with its 80% prediction interval."""
    point: float
    lower: float
    upper: float


class TrajectoryPoint(BaseModel):
    """Projected end-of-day balance, with the band that comes from variable-spending uncertainty."""
    date: date
    balance: float
    lower: float
    upper: float


class RecurringItemOut(BaseModel):
    """A payment detected as repeating about monthly (explainability: the user can check it)."""
    key: str                         # the payee, as the forecast grouped it
    kind: RecurringKind
    category: str | None             # most common category of its transactions
    typical_amount: float
    typical_day: int                 # day of month it usually lands on
    occurrences: int
    last_seen: date
    active: bool                     # False = not seen recently, so it is NOT projected
    projected_dates: list[date] = [] # when it is expected inside the forecast horizon


class BudgetWarning(BaseModel):
    """A budget category projected to end its month above its Phase 4 limit."""
    month: str                       # 'YYYY-MM'
    category: str
    projected: float                 # month-to-date actual + projected rest of the month
    limit: float
    over_by: float


class ModelScore(_ForecastSchema):
    """Backtest result of one candidate model (lower MAE = better)."""
    model_name: str
    mae: float
    folds: int


class ForecastRunResponse(_ForecastSchema):
    """POST /api/forecast/generate, GET /api/forecast/latest and /runs/{id}."""
    id: int
    as_of: date
    horizon_days: int
    created_at: datetime | None
    model_name: str
    history_months: int
    low_confidence: bool
    opening_balance: float
    opening_balance_source: OpeningBalanceSource
    opening_balance_date: date | None
    income_source: IncomeSource
    expected_income: float
    expected_fixed_expenses: float
    expected_variable_expenses: Band
    expected_investments: float
    net_cashflow: float              # income - fixed - variable - investments (point values)
    projected_end_balance: Band
    cash_crunch_date: date | None
    cash_crunch_basis: Literal["lower"] = "lower"   # the crunch is read from the LOWER balance band
    summary: str
    llm_used: bool
    explanation_faithful: bool | None
    model_scores: list[ModelScore]
    recurring_items: list[RecurringItemOut]
    budget_warnings: list[BudgetWarning]
    trajectory: list[TrajectoryPoint]


class ForecastHistoryItem(_ForecastSchema):
    """GET /api/forecast/history: one line per saved run, newest first."""
    id: int
    as_of: date
    horizon_days: int
    model_name: str
    low_confidence: bool
    net_cashflow: float
    projected_end_balance: float
    cash_crunch_date: date | None
    llm_used: bool
    created_at: datetime | None


class RecurringListResponse(BaseModel):
    """GET /api/forecast/recurring (read-only in this phase)."""
    as_of: date
    items: list[RecurringItemOut]
