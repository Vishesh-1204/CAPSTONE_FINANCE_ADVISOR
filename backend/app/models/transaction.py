# app/models/transaction.py

from sqlalchemy import Boolean, String, Float, DateTime, ForeignKey, false, func
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime

from app.db.base_class import Base


class Transaction(Base):
    """
    Represents a single expense entry belonging to a user.
    'category' starts as None and gets filled in by the
    Expense Intelligence Agent (LLM categorization) in Pass B.
    """
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # Links every transaction to exactly one user
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    description: Mapped[str] = mapped_column(String(500), nullable=False)
    amount: Mapped[float] = mapped_column(Float, nullable=False)

    # Nullable because it's empty until the agent categorizes it (Pass B)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)

        # Filled by the Expense Intelligence Agent during categorization
    category_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    category_reasoning: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Normalised description (lowercase, digits/UPI noise removed). Indexed because
    # the memory lookup ("did the user correct this description before?") filters on it.
    description_key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)

    # Who decided the category: 'rule' | 'memory' | 'llm' | 'user'
    category_source: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # True when the agent was unsure (confidence below CATEGORIZER_REVIEW_THRESHOLD).
    # server_default puts FALSE in the table definition itself, not only in Python.
    needs_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    transaction_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())