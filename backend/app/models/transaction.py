# app/models/transaction.py
#
# Purpose: the `transactions` table. One row = one bank-statement line (debit
# or credit) of one user. Schema changes need an Alembic revision in
# migrations/versions/ (the drift test fails otherwise).

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Numeric, String, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.transaction_types import DEBIT
from app.db.base_class import Base


class Transaction(Base):
    """
    A single money movement belonging to a user.
    'category' starts as None and is filled by the Expense Intelligence Agent
    (memory -> rules -> LLM) or by the user.
    """
    __tablename__ = "transactions"
    __table_args__ = (
        # Payee lookups ("other rows from this payee", payee rules) are always per user
        Index("ix_transactions_user_id_payee_key", "user_id", "payee_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # Links every transaction to exactly one user
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)

    description: Mapped[str] = mapped_column(String(500), nullable=False)
    # Always POSITIVE; `direction` says whether it went out or came in.
    # Numeric(14,2) stores paise exactly (never use float for money).
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    # 'debit' (money out) | 'credit' (money in)
    direction: Mapped[str] = mapped_column(String(6), nullable=False, default=DEBIT, server_default=DEBIT)
    # 'expense' | 'income' | 'investment_out' | 'investment_in' | 'transfer'
    txn_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 'sip' | 'lump_sum' | 'wallet_topup' (only for investment_out)
    investment_subtype: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 'upi' | 'neft' | 'imps' | 'nach' | 'card' | 'atm' | 'cheque' | 'other'
    channel: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # UPI ID (lowercased) if the narration has one, otherwise the description_key
    payee_key: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Running balance printed on the statement, and whether it agreed with the
    # previous balance +/- amount (filled by statement imports from S4/S5)
    balance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    balance_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Nullable because it's empty until the agent categorizes it
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Filled by the Expense Intelligence Agent during categorization
    category_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    category_reasoning: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Normalised description (lowercase, digits/UPI noise removed). Indexed because
    # the memory lookup ("did the user correct this description before?") filters on it.
    description_key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)

    # Who decided the category: 'rule' | 'memory' | 'llm' | 'user'
    category_source: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # The agent's ORIGINAL suggestion. Kept when the user overrides it, so we can
    # measure how often users disagree with the agent (override rate).
    agent_category: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # True once the user set the category: the agent never overwrites it.
    user_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())

    # True when the agent was unsure (confidence below CATEGORIZER_REVIEW_THRESHOLD).
    # server_default puts FALSE in the table definition itself, not only in Python.
    needs_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    # Which upload this row came from (NULL for manual entries and older rows)
    import_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_batches.id", name="fk_transactions_import_batch_id"), nullable=True, index=True
    )

    transaction_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
