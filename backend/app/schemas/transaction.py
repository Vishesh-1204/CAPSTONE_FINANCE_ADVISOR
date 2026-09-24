# app/schemas/transaction.py

from pydantic import BaseModel, ConfigDict
from datetime import datetime


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
    transaction_date: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class CategorizationSummary(BaseModel):
    """Result summary returned after a batch categorization run."""
    total_processed: int
    successfully_categorized: int
    failed: int