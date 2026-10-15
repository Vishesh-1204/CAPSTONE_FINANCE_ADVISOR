# app/services/transaction_service.py
#
# Purpose: plain storage and retrieval of transactions (no AI here).
# Categorization lives in categorization_service.py (Single Responsibility).

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Query, Session

from app.core.categories import ALL_CATEGORY_NAMES, canonical_category
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


# Review-queue statuses. They partition the rows (each row has exactly one):
#   uncategorized - no category yet
#   needs_review  - the agent was unsure (and the user has not locked it)
#   categorized   - the agent decided confidently (not locked)
#   locked        - the user set or confirmed the category
STATUSES = ("needs_review", "uncategorized", "categorized", "locked")


@dataclass(frozen=True)
class TransactionFilters:
    """Query-string filters for GET /api/transactions (all optional)."""
    status: str | None = None
    txn_type: str | None = None
    category: str | None = None
    date_from: date | None = None
    date_to: date | None = None          # inclusive
    needs_review: bool | None = None     # v2 alias, kept for old clients
    limit: int = 50
    offset: int = 0


def _apply_status(query: Query, status_name: str) -> Query:
    if status_name == "uncategorized":
        return query.filter(Transaction.category.is_(None))
    if status_name == "needs_review":
        return query.filter(Transaction.needs_review.is_(True), Transaction.user_locked.is_(False))
    if status_name == "categorized":
        return query.filter(
            Transaction.category.is_not(None),
            Transaction.needs_review.is_(False),
            Transaction.user_locked.is_(False),
        )
    return query.filter(Transaction.user_locked.is_(True))       # 'locked'


def _start_of_day(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def list_transactions(db: Session, user_id: int, filters: TransactionFilters) -> tuple[int, list[Transaction]]:
    """
    A page of the user's transactions plus the TOTAL number matching the filters
    (so a UI can show "page 2 of 7"). Review queue: least confident first;
    everything else: newest first.
    """
    query = db.query(Transaction).filter(Transaction.user_id == user_id)

    status_name = filters.status
    if status_name is None and filters.needs_review is True:
        status_name = "needs_review"
    elif status_name is None and filters.needs_review is False:
        query = query.filter(Transaction.needs_review.is_(False))
    if status_name:
        query = _apply_status(query, status_name)

    if filters.txn_type:
        query = query.filter(Transaction.txn_type == filters.txn_type)
    if filters.category:
        category = canonical_category(filters.category)
        if category is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Unknown category. Choose one of: {', '.join(ALL_CATEGORY_NAMES)}",
            )
        query = query.filter(Transaction.category == category)
    if filters.date_from:
        query = query.filter(Transaction.transaction_date >= _start_of_day(filters.date_from))
    if filters.date_to:
        query = query.filter(Transaction.transaction_date < _start_of_day(filters.date_to + timedelta(days=1)))

    total = query.count()
    if status_name == "needs_review":
        query = query.order_by(Transaction.category_confidence.asc(), Transaction.id.asc())
    else:
        query = query.order_by(Transaction.transaction_date.desc(), Transaction.id.desc())
    return total, query.offset(filters.offset).limit(filters.limit).all()
