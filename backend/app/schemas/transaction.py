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


class TransactionPage(BaseModel):
    """One page of GET /api/transactions plus the total matching the filters."""
    total: int
    limit: int
    offset: int
    items: list[TransactionResponse]


def _known_category(value: str) -> str:
    canonical = canonical_category(value)
    if canonical is None:
        raise ValueError(f"Unknown category. Choose one of: {', '.join(ALL_CATEGORY_NAMES)}")
    return canonical


class CategoryCorrection(BaseModel):
    """Body for fixing a wrong category. This is how the agent learns."""
    category: str
    # Also update the user's other UNLOCKED rows of the same payee (UPI ID / merchant)
    apply_to_similar: bool = False
    # Opt-in: create a payee rule so FUTURE rows of this payee get this category
    remember_payee: bool = False

    _validate_category = field_validator("category")(_known_category)


class PayeeRuleResponse(BaseModel):
    id: int
    match_type: str                  # 'upi_id' | 'merchant_key'
    match_value: str
    category: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CorrectionResponse(BaseModel):
    transaction: TransactionResponse
    similar_updated: int             # how many other rows were fixed too
    payee_rule: PayeeRuleResponse | None = None


class BulkCategoryRequest(BaseModel):
    """Set one category on many rows at once (review-queue clean-up)."""
    transaction_ids: list[int] = Field(min_length=1, max_length=500)
    category: str

    _validate_category = field_validator("category")(_known_category)


class BulkCategoryResponse(BaseModel):
    updated: int
    updated_ids: list[int]
    skipped_incompatible_ids: list[int]   # category does not fit the row's direction
    not_found_ids: list[int]              # not yours, or does not exist


class CategoryTotal(BaseModel):
    category: str
    total: float
    count: int


class MonthlySummary(BaseModel):
    """GET /api/transactions/summary. Only txn_type='expense' counts as spending."""
    month: str                            # 'YYYY-MM'
    transaction_count: int
    expense_total: float
    income_total: float
    investment_out_total: float           # SIPs, broker top-ups, FDs (NOT spending)
    redemption_total: float               # money back from investments (NOT income)
    transfer_total: float                 # between your own accounts
    net_investment_flow: float            # invested minus redeemed (cash flow, not returns)
    savings_investment_rate: float | None  # (income - expenses) / income; None without income
    by_category: list[CategoryTotal]      # expenses only, largest first


class CategorizationSummary(BaseModel):
    """Result of a categorization run."""
    total_processed: int
    categorized_by_payee_rules: int  # matched one of your "remember this payee" rules
    categorized_by_memory: int       # matched something the user confirmed before
    categorized_by_rules: int        # matched a keyword rule or the investment/income dictionary
    categorized_by_llm: int          # decided by the LLM
    person_payees_guarded: int       # looked like a person: labelled Transfers locally, never sent to the LLM
    needs_review: int                # low-confidence results a human should check
    failed: int                      # still uncategorized; run again later
    message: str | None = None


class CsvImportSummary(BaseModel):
    """Result of POST /upload-csv (v2 shape, kept for old clients)."""
    imported: int
    skipped_invalid: int             # unreadable rows (bad date/amount, empty description)
    skipped_duplicates: int          # already in your account
    skipped_credits: int             # DEPRECATED: always 0, credits are imported since v3
    import_batch_id: int | None = None
    next_step: str
