# app/schemas/transaction.py

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.categories import CATEGORY_NAMES, canonical_category


class TransactionCreate(BaseModel):
    """Shape of data required to manually log a new expense."""
    description: str
    amount: float
    transaction_date: datetime


class TransactionResponse(BaseModel):
    """Shape of transaction data returned to the client."""
    id: int
    description: str
    amount: float
    category: str | None
    category_confidence: float | None
    category_reasoning: str | None
    category_source: str | None      # 'rule' | 'memory' | 'llm' | 'user'
    needs_review: bool
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
            raise ValueError(f"Unknown category. Choose one of: {', '.join(CATEGORY_NAMES)}")
        return canonical


class CorrectionResponse(BaseModel):
    transaction: TransactionResponse
    similar_updated: int             # how many other rows were fixed too


class CategorizationSummary(BaseModel):
    """Result of a categorization run."""
    total_processed: int
    categorized_by_memory: int       # matched something the user confirmed before
    categorized_by_rules: int        # matched a keyword rule
    categorized_by_llm: int          # decided by the LLM
    needs_review: int                # low-confidence results a human should check
    failed: int                      # still uncategorized; run again later
    message: str | None = None


class CsvImportSummary(BaseModel):
    """Result of a CSV upload."""
    imported: int
    skipped_invalid: int             # unreadable rows (bad date/amount, empty description)
    skipped_duplicates: int          # already in your account
    skipped_credits: int             # money coming in (salary, refunds); not expenses
    next_step: str
