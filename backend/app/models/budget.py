# app/models/budget.py
#
# Purpose: the two Budget Planning Agent tables.
#   * `budgets`     - one monthly spending limit per (user, month, category).
#   * `budget_runs` - a research log: one row every time a plan is generated,
#                     recording HOW it was built (history or baseline, LLM used,
#                     explanation faithful). Used for the Phase 4 metrics.
# Schema changes need an Alembic revision in migrations/versions/.

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

# Who set the current limit_amount
SOURCE_AGENT = "agent"   # the planner's number (refreshed on regeneration)
SOURCE_USER = "user"     # the user overrode it (regeneration never touches it)


class Budget(Base):
    """A monthly spending limit for one category of one user."""
    __tablename__ = "budgets"
    __table_args__ = (
        # One limit per category per month; regeneration UPDATES instead of duplicating
        UniqueConstraint("user_id", "month", "category", name="uq_budgets_user_month_category"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    month: Mapped[str] = mapped_column(String(7), nullable=False)          # 'YYYY-MM'
    category: Mapped[str] = mapped_column(String(100), nullable=False)     # an expense category name

    # The limit in force right now (the agent's or the user's)
    limit_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # The planner's ORIGINAL number. Never changed by a user override, so we can
    # measure the override rate and size, and "reset" can restore it.
    agent_limit_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    source: Mapped[str] = mapped_column(String(10), nullable=False, default=SOURCE_AGENT, server_default=SOURCE_AGENT)
    reasoning: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BudgetRun(Base):
    """One budget generation, logged for the research metrics (never shown as advice)."""
    __tablename__ = "budget_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    month: Mapped[str] = mapped_column(String(7), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)          # 'history' | 'baseline'
    spendable: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    feasible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    shortfall: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    months_used: Mapped[int] = mapped_column(Integer, nullable=False)       # months of history behind the plan

    llm_used: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # True = every LLM sentence passed the guard; False = at least one was replaced
    # by the template; NULL = no LLM text to check (LLM off or unavailable)
    explanation_faithful: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
