# app/schemas/transaction.py
#
# Purpose: Pydantic request/response shapes for the transaction endpoints.
# Amounts are Decimal in the database; responses send them as JSON numbers.

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.categories import ALL_CATEGORY_NAMES, canonical_category


class TransactionCreate(BaseModel):
    """Shape of data required to manually log one transaction."""
    description: str = Field(min_length=1, max_length=500)
    amount: float = Field(gt=0, description="Always positive; use `direction` for money in vs out.")
    transaction_date: datetime
    direction: Literal["debit", "credit"] = "debit"


class TransactionResponse(BaseModel):
    """Shape of transaction data returned to the client."""
    id: int
    description: str
    amount: float
    direction: str                   # 'debit' | 'credit'
    txn_type: str | None             # 'expense' | 'income' | 'investment_out' | 'investment_in' | 'transfer'
    investment_subtype: str | None   # 'sip' | 'lump_sum' | 'wallet_topup'
    channel: str | None
    payee_key: str | None
    category: str | None
    category_confidence: float | None
    category_reasoning: str | None
    category_source: str | None      # 'rule' | 'memory' | 'llm' | 'user'
    agent_category: str | None       # the agent's original suggestion
    user_locked: bool
    needs_review: bool
    balance: float | None
    balance_ok: bool | None
    import_batch_id: int | None
    transaction_date: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CategoryCorrection(BaseModel):
    """Body for fixing a wrong category. This is how the agent learns."""
    category: str
    # Also fix other already-categorized rows with the same description
    apply_to_similar: bool = True

    @field_validator("category")
    @classmethod
    def category_must_be_known(cls, value: str) -> str:
        canonical = canonical_category(value)
        if canonical is None:
            raise ValueError(f"Unknown category. Choose one of: {', '.join(ALL_CATEGORY_NAMES)}")
        return canonical


class CorrectionResponse(BaseModel):
    transaction: TransactionResponse
    similar_updated: int             # how many other rows were fixed too


class CategorizationSummary(BaseModel):
    """Result of a categorization run."""
    total_processed: int
    categorized_by_memory: int       # matched something the user confirmed before
    categorized_by_rules: int        # matched a keyword rule or the investment/income dictionary
    categorized_by_llm: int          # decided by the LLM
    needs_review: int                # low-confidence results a human should check
    failed: int                      # still uncategorized; run again later
    message: str | None = None


class CsvImportSummary(BaseModel):
    """Result of a CSV upload."""
    imported: int
    skipped_invalid: int             # unreadable rows (bad date/amount, empty description)
    skipped_duplicates: int          # already in your account
    skipped_credits: int             # money coming in; imported from S4 (statement upload)
    next_step: str
