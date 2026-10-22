# app/models/financial_profile.py
#
# Purpose: the `financial_profiles` table. One row per user holding the two
# numbers the Budget Planning Agent needs from the user: monthly income and
# the savings target (a percentage of income set aside before budgeting).
# Schema changes need an Alembic revision in migrations/versions/.

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

DEFAULT_SAVINGS_TARGET_PCT = Decimal("20.00")


class FinancialProfile(Base):
    """A user's income and savings target. Exactly one per user (UNIQUE user_id)."""
    __tablename__ = "financial_profiles"
    __table_args__ = (
        # Named so the migration and the model describe the same constraint
        UniqueConstraint("user_id", name="uq_financial_profiles_user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)

    # Numeric(14,2) stores rupees and paise exactly (never float for money)
    monthly_income: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    # Percentage of income to save, e.g. 20.00 means 20%
    savings_target_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=DEFAULT_SAVINGS_TARGET_PCT, server_default="20"
    )

    # onupdate refreshes the timestamp every time the row is changed through the ORM
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
