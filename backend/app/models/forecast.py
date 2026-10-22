# app/models/forecast.py
#
# Purpose: the `forecast_runs` table of the Financial Forecast Agent (Phase 5).
# One row = one forecast the user generated: the headline numbers (with their
# 80% interval), the daily balance trajectory, the recurring items it used and
# the budget warnings. Saved so the user can reopen it (GET /latest, /history)
# and so a later step can compare it with what really happened (accuracy).
# Schema changes need an Alembic revision in migrations/versions/.

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

# Where the opening balance came from
OPENING_FROM_REQUEST = "request"       # the user typed it
OPENING_FROM_STATEMENT = "statement"   # the latest running balance printed on an imported statement

# Where the expected income came from
INCOME_FROM_RECURRING = "recurring"    # detected recurring credits (salary)
INCOME_FROM_PROFILE = "profile"        # Phase 4 financial profile monthly_income
INCOME_NONE = "none"                   # neither exists: income is forecast as 0


class ForecastRun(Base):
    """One saved cash-flow forecast. Every money column is Numeric(14,2) (exact rupees and paise)."""
    __tablename__ = "forecast_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    # The "today" the forecast was made from (injected, so tests are reproducible)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)          # 30 | 60 | 90

    # Which statistical model forecast the variable spending, and on how much history
    model_name: Mapped[str] = mapped_column(String(40), nullable=False)
    history_months: Mapped[int] = mapped_column(Integer, nullable=False)
    # True when the history is too short for a backtest-based interval (told to the user)
    low_confidence: Mapped[bool] = mapped_column(Boolean, nullable=False)

    opening_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    opening_balance_source: Mapped[str] = mapped_column(String(10), nullable=False)
    # Date of the statement row the balance was read from (NULL when typed by the user)
    opening_balance_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    income_source: Mapped[str] = mapped_column(String(10), nullable=False)

    # Totals over the horizon
    expected_income: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    expected_fixed_expenses: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    expected_variable_expenses: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    expected_variable_expenses_lower: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    expected_variable_expenses_upper: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    expected_investments: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    net_cashflow: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    projected_end_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    projected_end_balance_lower: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    projected_end_balance_upper: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # First day the LOWER balance band goes below zero (NULL = no crunch expected)
    cash_crunch_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # The explanation shown to the user (LLM text that passed the guard, or the template)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    llm_used: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # True = LLM text passed the guard; False = it invented a number (template used); NULL = no LLM text
    explanation_faithful: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Lists stored as JSON (they are always read and written as a whole)
    trajectory: Mapped[list] = mapped_column(JSON, nullable=False)        # [{date, balance, lower, upper}]
    recurring_items: Mapped[list] = mapped_column(JSON, nullable=False)   # detected items used in this run
    budget_warnings: Mapped[list] = mapped_column(JSON, nullable=False)   # categories projected over their limit
    model_scores: Mapped[list] = mapped_column(JSON, nullable=False)      # backtest MAE of every candidate model

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
