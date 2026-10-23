# app/models/investment.py
#
# Purpose: the three Investment Advisor Agent tables (Phase 6).
#   * `investment_profiles` - one per user: questionnaire answers, finance facts
#                             and the risk profile computed from them.
#   * `holdings`            - what the user holds, in rupees per asset class
#                             (typed in by hand; there is no broker integration).
#   * `investment_plans`    - one row per generated plan: its inputs, results,
#                             assumptions and how its explanation was produced
#                             (research log for the paper's explanation metrics).
# Schema changes need an Alembic revision in migrations/versions/.

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
    UniqueConstraint, false, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

# The four asset classes. Must match services/investing/allocation.ASSET_CLASSES
# (tests/test_investing_data_model.py checks it). Stored as a short string with a
# CHECK rule rather than a native Postgres ENUM, which is awkward to migrate.
HOLDING_ASSET_CLASSES: tuple[str, ...] = ("equity", "debt", "gold", "cash")

EXPLANATION_FROM_LLM = "llm"
EXPLANATION_FROM_TEMPLATE = "template"


class InvestmentProfile(Base):
    """A user's risk questionnaire, finance facts and computed risk profile. Exactly one per user."""
    __tablename__ = "investment_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_investment_profiles_user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    # {reaction_to_20pct_drop, investing_experience, return_vs_safety}, each 1-5
    tolerance_answers: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Non-money facts: {income_stability, dependents, horizon_years, emergency_fund_months}
    capacity_facts: Mapped[dict] = mapped_column(JSON, nullable=False)
    # Money facts get real Numeric columns (exact, checkable), not JSON
    liquid_savings: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    existing_monthly_sip: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=Decimal("0.00"), server_default="0"
    )

    # Computed when the profile is saved; POST /plan always recomputes them
    tolerance_score: Mapped[Decimal] = mapped_column(Numeric(5, 1), nullable=False)
    capacity_score: Mapped[Decimal] = mapped_column(Numeric(5, 1), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)          # conservative | moderate | aggressive
    limiting_factor: Mapped[str] = mapped_column(String(10), nullable=False)   # tolerance | capacity | both

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Holding(Base):
    """Rupees the user holds in one asset class (optionally split by a free-text label)."""
    __tablename__ = "holdings"
    __table_args__ = (
        # label is NOT NULL ('' = no label): NULLs never collide in a UNIQUE constraint,
        # so a nullable label would allow duplicate (user, asset_class) rows.
        UniqueConstraint("user_id", "asset_class", "label", name="uq_holdings_user_class_label"),
        CheckConstraint(
            "asset_class IN (" + ", ".join(f"'{name}'" for name in HOLDING_ASSET_CLASSES) + ")",
            name="ck_holdings_asset_class",
        ),
        CheckConstraint("amount >= 0", name="ck_holdings_amount_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # No separate user_id index: the unique constraint's index starts with user_id
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    asset_class: Mapped[str] = mapped_column(String(10), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False, default="", server_default="")
    as_of: Mapped[date] = mapped_column(Date, nullable=False)


class InvestmentPlan(Base):
    """One generated plan, stored so it can be reopened, compared over time and measured."""
    __tablename__ = "investment_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Indexed by the composite (user_id, created_at DESC) index below
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Everything the plan was built from (incl. forecast_run_id), what it produced, and
    # the return/volatility assumptions and Monte Carlo seed (so it can be reproduced)
    inputs_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    assumptions_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    engine_version: Mapped[str] = mapped_column(String(20), nullable=False)

    # NULL when there is no explanation text (e.g. the plan was stored before explaining)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Research log. explanation_faithful / claims_compliant are NULL when there was no LLM text to check.
    llm_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    explanation_faithful: Mapped[bool | None] = mapped_column(Boolean, nullable=True)   # numeric guard passed
    claims_compliant: Mapped[bool | None] = mapped_column(Boolean, nullable=True)       # claims guard passed
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Prohibited phrases found in the RAW LLM text, before the guard swapped it out
    raw_prohibited_claims: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    @property
    def explanation_source(self) -> str:
        """'llm' only when LLM text was used AND passed both guards; otherwise the template was shown."""
        if self.llm_used and self.explanation_faithful and self.claims_compliant:
            return EXPLANATION_FROM_LLM
        return EXPLANATION_FROM_TEMPLATE


# /plan/latest and /plans both read "this user's plans, newest first"
Index("ix_investment_plans_user_id_created_at", InvestmentPlan.user_id, InvestmentPlan.created_at.desc())
