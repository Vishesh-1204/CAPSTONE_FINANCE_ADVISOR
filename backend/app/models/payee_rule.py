# app/models/payee_rule.py
#
# Purpose: the `payee_rules` table. A per-user, opt-in rule created when the
# user ticks "remember this payee": every future transaction to that payee gets
# the chosen category automatically (highest priority after a manual lock).
# Keyed on the UPI ID when there is one (most reliable identity), otherwise on
# the normalized merchant key.

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

MATCH_UPI_ID = "upi_id"
MATCH_MERCHANT_KEY = "merchant_key"


class PayeeRule(Base):
    """'Payments to <match_value> are always <category>' for one user."""
    __tablename__ = "payee_rules"
    __table_args__ = (
        # One rule per payee per user; remembering again UPDATES the rule
        UniqueConstraint("user_id", "match_type", "match_value", name="uq_payee_rules_user_match"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    match_type: Mapped[str] = mapped_column(String(20), nullable=False)     # 'upi_id' | 'merchant_key'
    match_value: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
