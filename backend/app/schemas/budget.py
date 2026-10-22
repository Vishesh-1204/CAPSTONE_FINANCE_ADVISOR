# app/schemas/budget.py
#
# Purpose: request/response shapes for the Phase 4 endpoints
# (/api/profile/financial and /api/budgets/...).
#
# Requests take Decimal so money arrives exact (at most 2 decimal places).
# Responses use float, like MonthlySummary, because JSON has no Decimal type.

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.financial_profile import DEFAULT_SAVINGS_TARGET_PCT

BudgetSource = Literal["agent", "user"]
PlanMethod = Literal["history", "baseline"]
BudgetStatusValue = Literal["ok", "warning", "exceeded", "unbudgeted"]


# --- Financial profile ---

class FinancialProfileUpdate(BaseModel):
    """PUT /api/profile/financial. The savings target is a percentage of income."""
    monthly_income: Decimal = Field(gt=0, max_digits=14, decimal_places=2, examples=[60000])
    savings_target_pct: Decimal = Field(
        default=DEFAULT_SAVINGS_TARGET_PCT, ge=0, lt=100, max_digits=5, decimal_places=2, examples=[20]
    )


class FinancialProfileResponse(BaseModel):
    """The profile plus the two numbers derived from it."""
    monthly_income: float
    savings_target_pct: float
    savings_target_amount: float   # income x pct / 100
    spendable_income: float        # income minus the savings target (what the budget splits)
    updated_at: datetime | None


class MonthlyIncome(BaseModel):
    """Income received in one month of the suggestion window."""
    month: str
    total: float


class IncomeSuggestion(BaseModel):
    """
    GET /api/profile/financial/suggestion: average monthly income from your
    income credits (salary, interest, dividends; refunds excluded) in the
    lookback months before `month`. Read-only: nothing is saved.
    """
    month: str                                 # the month the suggestion is for
    lookback_months: int
    window_start: str                          # first month looked at ('YYYY-MM')
    window_end: str                            # last month looked at ('YYYY-MM')
    months_used: int                           # months in the window that have ANY transaction
    income_by_month: list[MonthlyIncome]       # one entry per month used (0 if no income that month)
    suggested_monthly_income: float | None     # None when there is no income in the window
    message: str


# --- Budgets ---

class BudgetItem(BaseModel):
    """One category limit for one month."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    month: str
    category: str
    limit_amount: float            # the limit in force (agent's or user's)
    agent_limit_amount: float      # the planner's original number
    source: BudgetSource
    reasoning: str | None


class BudgetUpdate(BaseModel):
    """PUT /api/budgets/{id}: the user's own limit (marks the row source='user')."""
    limit_amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2, examples=[4500])


class DataQuality(BaseModel):
    """How trustworthy the history behind the plan is. Weak data = weak plan."""
    months_used: int               # lookback months that had categorized expenses
    uncategorized_count: int       # expenses in the lookback with no category yet
    needs_review_count: int        # expenses the categorizer was unsure about
    transfers_total: float         # person-to-person payments in the lookback (could hide rent, etc.)


class BudgetPlanResponse(BaseModel):
    """POST /api/budgets/generate."""
    month: str
    method: PlanMethod
    monthly_income: float
    savings_target_amount: float
    spendable_income: float
    feasible: bool                 # False when essentials alone exceed spendable income
    shortfall: float               # by how much (0 when feasible)
    summary: str                   # one-paragraph explanation (LLM or template)
    llm_used: bool
    explanation_faithful: bool | None
    data_quality: DataQuality
    budgets: list[BudgetItem]      # every limit for the month, including your overrides


# --- Status (actual spending vs limits) ---

class BudgetStatusItem(BaseModel):
    """Spending vs limit for one category. limit is None for an unbudgeted category."""
    category: str
    limit: float | None
    spent: float
    remaining: float | None
    percent_used: float | None     # None when there is no limit or the limit is 0
    status: BudgetStatusValue
    source: BudgetSource | None


class BudgetStatusResponse(BaseModel):
    """GET /api/budgets/status. Only expenses count as spending."""
    month: str
    items: list[BudgetStatusItem]
    total_limit: float
    total_spent: float
    total_remaining: float
    invested_this_month: float         # investment_out total (saved, NOT spent)
    savings_target_amount: float | None  # None when no financial profile exists
