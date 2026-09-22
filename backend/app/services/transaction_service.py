# app/services/transaction_service.py

import logging
from sqlalchemy.orm import Session

from app.models.transaction import Transaction
from app.schemas.transaction import TransactionCreate

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