# app/services/payee_rule_service.py
#
# Purpose: per-user "remember this payee" rules (opt-in, created from
# PATCH /category with remember_payee=true).
#
#   * A rule is keyed on the payee's UPI ID when the narration has one (the most
#     reliable identity: two different people can share a first name, but never
#     a UPI ID), otherwise on the normalized merchant key.
#   * Rules have the highest priority after a manual lock (spec section 4).
#   * A rule only applies when its category fits the row's direction, so a
#     "Food & Dining" rule for a restaurant never labels its REFUND (a credit).

import logging

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.categories import category_fits_direction
from app.models.payee_rule import MATCH_MERCHANT_KEY, MATCH_UPI_ID, PayeeRule
from app.models.transaction import Transaction
from app.services.narration_parser import parse_narration

logger = logging.getLogger(__name__)


def rule_target(transaction: Transaction) -> tuple[str, str]:
    """(match_type, match_value) a rule for this transaction would use: UPI ID first, else merchant key."""
    info = parse_narration(transaction.description)
    if info.upi_id:
        return MATCH_UPI_ID, info.upi_id
    return MATCH_MERCHANT_KEY, info.description_key


class PayeeRuleIndex:
    """All of one user's rules in memory, for fast matching during a categorization run."""

    def __init__(self, rules: list[PayeeRule]) -> None:
        self._categories: dict[tuple[str, str], str] = {(r.match_type, r.match_value): r.category for r in rules}

    def __len__(self) -> int:
        return len(self._categories)

    def match(self, transaction: Transaction) -> tuple[str, str] | None:
        """Returns (category, match_type) for the first matching rule, or None."""
        if not self._categories:
            return None
        info = parse_narration(transaction.description)
        candidates = []
        if info.upi_id:
            candidates.append((MATCH_UPI_ID, info.upi_id))
        candidates.append((MATCH_MERCHANT_KEY, info.description_key))

        for key in candidates:
            category = self._categories.get(key)
            if category and category_fits_direction(category, transaction.direction):
                return category, key[0]
        return None


def load_rule_index(db: Session, user_id: int) -> PayeeRuleIndex:
    return PayeeRuleIndex(db.query(PayeeRule).filter(PayeeRule.user_id == user_id).all())


def upsert_rule(db: Session, user_id: int, transaction: Transaction, category: str) -> PayeeRule:
    """
    Creates the rule for this transaction's payee, or updates its category if it
    already exists (the UNIQUE constraint allows one rule per payee). Caller commits.
    """
    match_type, match_value = rule_target(transaction)
    rule = (
        db.query(PayeeRule)
        .filter(PayeeRule.user_id == user_id, PayeeRule.match_type == match_type, PayeeRule.match_value == match_value)
        .first()
    )
    if rule is None:
        rule = PayeeRule(user_id=user_id, match_type=match_type, match_value=match_value, category=category)
        db.add(rule)
    else:
        rule.category = category
    db.flush()                        # assigns rule.id without committing yet
    logger.info(f"Payee rule saved id={rule.id} user_id={user_id} type={match_type}")
    return rule


def list_rules(db: Session, user_id: int) -> list[PayeeRule]:
    return db.query(PayeeRule).filter(PayeeRule.user_id == user_id).order_by(PayeeRule.id.desc()).all()


def delete_rule(db: Session, user_id: int, rule_id: int) -> None:
    """Deletes one of the user's rules. Someone else's id is a 404, so we don't reveal it exists."""
    rule = db.query(PayeeRule).filter(PayeeRule.id == rule_id, PayeeRule.user_id == user_id).first()
    if rule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Payee rule not found.")
    db.delete(rule)
    db.commit()
    logger.info(f"Payee rule deleted id={rule_id} user_id={user_id}")
