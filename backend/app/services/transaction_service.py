# app/services/transaction_service.py

import logging
from sqlalchemy.orm import Session

from app.models.transaction import Transaction
from app.schemas.transaction import TransactionCreate
from app.agents.expense_categorizer import ExpenseCategorizerAgent

logger = logging.getLogger(__name__)


def create_transaction(db: Session, user_id: int, data: TransactionCreate) -> Transaction:
    """
    Saves a single expense entry for a given user.
    Category is left as None here — it gets filled in separately
    by the categorization agent (Pass B), keeping storage and
    reasoning as two independent steps.
    """
    transaction = Transaction(
        user_id=user_id,
        description=data.description,
        amount=data.amount,
        transaction_date=data.transaction_date,
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)

    logger.info(f"Transaction created for user_id={user_id}: {data.description} (${data.amount})")
    return transaction


def get_transactions_for_user(db: Session, user_id: int) -> list[Transaction]:
    """Fetches all transactions belonging to a specific user, most recent first."""
    return (
        db.query(Transaction)
        .filter(Transaction.user_id == user_id)
        .order_by(Transaction.transaction_date.desc())
        .all()
    )


def categorize_pending_transactions(db: Session, user_id: int) -> dict[str, int]:
    """
    Finds all uncategorized transactions for a user and runs the
    Expense Intelligence Agent on each one.

    Processed sequentially, not in parallel — free-tier LLM APIs are
    rate-limited to roughly 10-15 requests per minute, and firing
    parallel requests would trigger 429 errors immediately.
    """
    pending = (
        db.query(Transaction)
        .filter(Transaction.user_id == user_id, Transaction.category.is_(None))
        .all()
    )

    if not pending:
        logger.info(f"No pending transactions to categorize for user_id={user_id}")
        return {"total_processed": 0, "successfully_categorized": 0, "failed": 0}

    agent = ExpenseCategorizerAgent()
    succeeded = 0
    failed = 0

    for transaction in pending:
        result = agent.categorize(transaction.description, transaction.amount)

        transaction.category = result.category
        transaction.category_confidence = result.confidence
        transaction.category_reasoning = result.reasoning

        # confidence of 0.0 means our fallback path was used
        if result.confidence > 0.0:
            succeeded += 1
        else:
            failed += 1

    db.commit()

    logger.info(
        f"Categorization run for user_id={user_id}: "
        f"{succeeded} succeeded, {failed} failed out of {len(pending)}"
    )
    return {
        "total_processed": len(pending),
        "successfully_categorized": succeeded,
        "failed": failed,
    }