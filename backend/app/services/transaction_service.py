# app/services/transaction_service.py
#
# Purpose: plain storage and retrieval of transactions (no AI here).
# Categorization lives in categorization_service.py (Single Responsibility).

import logging

from sqlalchemy.orm import Session

from app.core.money import to_money
from app.models.transaction import Transaction
from app.schemas.transaction import TransactionCreate
from app.services.transaction_enrichment import enrich_transaction

logger = logging.getLogger(__name__)


def create_transaction(db: Session, user_id: int, data: TransactionCreate) -> Transaction:
    """Saves one transaction for a user. Category stays empty until the agent runs."""
    transaction = Transaction(
        user_id=user_id,
        description=data.description.strip(),
        amount=to_money(data.amount),
        direction=data.direction,
        transaction_date=data.transaction_date,
    )
    enrich_transaction(transaction)          # channel, payee_key, txn_type from the narration
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    # Log ids and types only: descriptions can contain names / UPI IDs
    logger.info(f"Transaction created id={transaction.id} user_id={user_id} type={transaction.txn_type}")
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
