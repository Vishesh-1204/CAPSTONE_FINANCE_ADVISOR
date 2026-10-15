# app/services/transaction_service.py
#
# Purpose: plain storage and retrieval of transactions (no AI here).
# Categorization lives in categorization_service.py (Single Responsibility).

import logging

from sqlalchemy.orm import Session

from app.models.transaction import Transaction
from app.schemas.transaction import TransactionCreate

logger = logging.getLogger(__name__)


def create_transaction(db: Session, user_id: int, data: TransactionCreate) -> Transaction:
    """Saves one expense for a user. Category stays empty until the agent runs."""
    transaction = Transaction(
        user_id=user_id,
        description=data.description.strip(),
        amount=data.amount,
        transaction_date=data.transaction_date,
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    logger.info(f"Transaction created for user_id={user_id}: {transaction.description} ({transaction.amount})")
    return transaction


def get_transactions_for_user(
    db: Session, user_id: int, needs_review: bool | None = None
) -> list[Transaction]:
    """
    A user's transactions, newest first.
    needs_review=True returns only the low-confidence ones (lowest confidence first).
    """
    query = db.query(Transaction).filter(Transaction.user_id == user_id)

    if needs_review is not None:
        query = query.filter(Transaction.needs_review == needs_review)
    if needs_review:
        return query.order_by(Transaction.category_confidence.asc(), Transaction.id.asc()).all()

    return query.order_by(Transaction.transaction_date.desc(), Transaction.id.desc()).all()
