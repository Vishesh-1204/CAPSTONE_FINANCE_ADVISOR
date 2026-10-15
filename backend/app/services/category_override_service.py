# app/services/category_override_service.py
#
# Purpose: everything the USER does to a category (manual override):
#   * apply_user_correction  - PATCH /{id}/category (+ apply_to_similar, remember_payee)
#   * bulk_set_category      - POST /bulk-category (review-queue clean-up)
#   * unlock_transaction     - POST /{id}/unlock
#
# Rules (spec section 7):
#   * a user-set category is LOCKED: no agent run overwrites it;
#   * agent_category keeps the agent's original suggestion (override-rate metric);
#   * the category must fit the direction (no "Salary" on a debit), and
#     txn_type follows the chosen category so totals stay consistent.
# Separate from categorization_service (the agent run): Single Responsibility.

import logging
from dataclasses import dataclass

from fastapi import HTTPException, status
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.core.categories import categories_for_direction, category_fits_direction
from app.models.payee_rule import PayeeRule
from app.models.transaction import Transaction
from app.services.payee_rule_service import upsert_rule
from app.services.text_normalizer import normalize_description
from app.services.transaction_enrichment import sync_txn_type_with_category

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CorrectionOutcome:
    transaction: Transaction
    similar_updated: int                 # other rows of the same payee that were updated too
    payee_rule: PayeeRule | None         # the rule created/updated when remember_payee=True


def _get_own_transaction(db: Session, user_id: int, transaction_id: int) -> Transaction:
    """Loads one of the user's rows. Someone else's id is a 404 (not 403), so we don't reveal it exists."""
    transaction = (
        db.query(Transaction)
        .filter(Transaction.id == transaction_id, Transaction.user_id == user_id)
        .first()
    )
    if transaction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found.")
    return transaction


def _ensure_fits(transaction: Transaction, category: str) -> None:
    if not category_fits_direction(category, transaction.direction):
        allowed = ", ".join(categories_for_direction(transaction.direction))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{category}' does not fit a {transaction.direction} transaction. Choose one of: {allowed}",
        )


def _write_category(transaction: Transaction, category: str, reasoning: str, source: str) -> None:
    """Writes a user-driven category and keeps txn_type consistent with it."""
    transaction.description_key = normalize_description(transaction.description)
    transaction.category = category
    transaction.category_confidence = 1.0
    transaction.category_reasoning = reasoning
    transaction.category_source = source
    transaction.needs_review = False
    sync_txn_type_with_category(transaction)      # e.g. user picks "Investments & Savings" -> investment_out
    # agent_category is deliberately NOT touched: it keeps the agent's original suggestion


def _set_user_category(transaction: Transaction, category: str) -> None:
    _write_category(transaction, category, "Set by you.", "user")
    transaction.user_locked = True        # the agent must never overwrite a user's choice


def apply_user_correction(
    db: Session,
    user_id: int,
    transaction_id: int,
    category: str,
    apply_to_similar: bool = False,
    remember_payee: bool = False,
) -> CorrectionOutcome:
    """
    The user fixes (or confirms) a category. This is the agent's learning signal:
      * the row becomes source='user', confidence 1.0, LOCKED
      * it becomes MEMORY and can appear as an EXAMPLE in the LLM prompt
      * apply_to_similar: unlocked rows of the same payee get the same category
      * remember_payee: a payee rule labels all FUTURE rows of this payee
    """
    transaction = _get_own_transaction(db, user_id, transaction_id)
    _ensure_fits(transaction, category)
    _set_user_category(transaction, category)

    similar_updated = 0
    if apply_to_similar:
        # Same payee: payee_key (UPI ID or merchant key); rows that predate payee_key
        # fall back to the description key.
        same_payee = (
            or_(
                Transaction.payee_key == transaction.payee_key,
                and_(Transaction.payee_key.is_(None), Transaction.description_key == transaction.description_key),
            )
            if transaction.payee_key
            else Transaction.description_key == transaction.description_key
        )
        similar = (
            db.query(Transaction)
            .filter(
                Transaction.user_id == user_id,
                Transaction.id != transaction.id,
                Transaction.direction == transaction.direction,
                Transaction.user_locked.is_(False),
                same_payee,
            )
            .all()
        )
        for row in similar:
            _write_category(row, category, "Same payee as a category you confirmed.", "memory")
        similar_updated = len(similar)

    payee_rule = upsert_rule(db, user_id, transaction, category) if remember_payee else None

    db.commit()
    db.refresh(transaction)
    logger.info(
        f"User correction user_id={user_id} tx={transaction_id} -> {category} "
        f"(+{similar_updated} similar, rule={'yes' if payee_rule else 'no'})"
    )
    return CorrectionOutcome(transaction, similar_updated, payee_rule)


def bulk_set_category(db: Session, user_id: int, transaction_ids: list[int], category: str) -> dict:
    """
    Sets one category on many of the user's rows (all get locked).
    Ids that are not the user's, or rows the category does not fit, are skipped and counted.
    """
    unique_ids = list(dict.fromkeys(transaction_ids))
    rows = db.query(Transaction).filter(Transaction.user_id == user_id, Transaction.id.in_(unique_ids)).all()

    updated_ids: list[int] = []
    incompatible_ids: list[int] = []
    for row in rows:
        if category_fits_direction(category, row.direction):
            _set_user_category(row, category)
            updated_ids.append(row.id)
        else:
            incompatible_ids.append(row.id)
    db.commit()

    found = {row.id for row in rows}
    not_found_ids = [i for i in unique_ids if i not in found]
    logger.info(
        f"Bulk category user_id={user_id} -> {category}: {len(updated_ids)} updated, "
        f"{len(incompatible_ids)} incompatible, {len(not_found_ids)} not found"
    )
    return {
        "updated": len(updated_ids),
        "updated_ids": sorted(updated_ids),
        "skipped_incompatible_ids": sorted(incompatible_ids),
        "not_found_ids": not_found_ids,
    }


def unlock_transaction(db: Session, user_id: int, transaction_id: int) -> Transaction:
    """
    Clears the lock. The category stays as the user set it; the agent may
    redo the row on the next `/categorize?reset_ai_results=true` run.
    """
    transaction = _get_own_transaction(db, user_id, transaction_id)
    transaction.user_locked = False
    db.commit()
    db.refresh(transaction)
    logger.info(f"Transaction unlocked id={transaction_id} user_id={user_id}")
    return transaction
