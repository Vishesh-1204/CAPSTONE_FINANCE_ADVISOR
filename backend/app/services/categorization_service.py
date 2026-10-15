# app/services/categorization_service.py
#
# Purpose: runs the Expense Intelligence Agent over a user's transactions.
# This is the ORCHESTRATOR. For each uncategorized transaction it tries the
# cheapest, most reliable method first and only asks the LLM when it must:
#
#   Layer 1  MEMORY  - "you told me this exact description is Groceries before"
#   Layer 2  RULES   - keyword match (Swiggy, Netflix ...), exactly one category
#   Layer 3  LLM     - batched, with the user's own corrections as examples
#
# Every result records WHO decided (category_source), how sure it was, and
# whether a human should double-check it (needs_review).

import logging
import statistics
import time
from collections import defaultdict

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.agents.expense_categorizer import CategorizationResult, ExpenseCategorizerAgent, PendingItem
from app.core.config import settings
from app.models.transaction import Transaction
from app.services.rule_based_categorizer import RULE_CONFIDENCE, rule_categorize
from app.services.text_normalizer import normalize_description

logger = logging.getLogger(__name__)

# Maps a result's source to the summary counter it increments
_SUMMARY_FIELD = {"memory": "categorized_by_memory", "rule": "categorized_by_rules", "llm": "categorized_by_llm"}

# Stop calling the LLM after this many batches in a row fail (quota used up, bad key...)
_MAX_CONSECUTIVE_FAILURES = 2


def _apply(transaction: Transaction, result: CategorizationResult) -> None:
    """Writes a decision onto a transaction row."""
    transaction.category = result.category
    transaction.category_confidence = result.confidence
    transaction.category_reasoning = result.reasoning
    transaction.category_source = result.source
    transaction.needs_review = result.confidence < settings.CATEGORIZER_REVIEW_THRESHOLD


def _load_memory(db: Session, user_id: int, keys: set[str]) -> dict[str, str]:
    """
    Description-key -> category for rows the USER confirmed.
    Ordered oldest to newest, so a newer correction overwrites an older one.
    """
    if not keys:
        return {}
    rows = (
        db.query(Transaction.description_key, Transaction.category)
        .filter(
            Transaction.user_id == user_id,
            Transaction.category_source == "user",
            Transaction.description_key.in_(list(keys)),
        )
        .order_by(Transaction.id.asc())
        .all()
    )
    return {key: category for key, category in rows}


def _load_examples(db: Session, user_id: int) -> list[tuple[str, str]]:
    """The user's most recent distinct corrections, shown to the LLM as examples."""
    limit = settings.CATEGORIZER_FEW_SHOT_LIMIT
    rows = (
        db.query(Transaction.description, Transaction.category)
        .filter(Transaction.user_id == user_id, Transaction.category_source == "user")
        .order_by(Transaction.id.desc())
        .limit(limit * 3)           # fetch extra: duplicates get dropped below
        .all()
    )
    seen: set[str] = set()
    examples: list[tuple[str, str]] = []
    for description, category in rows:
        key = normalize_description(description)
        if key in seen:
            continue
        seen.add(key)
        examples.append((description, category))
        if len(examples) >= limit:
            break
    return examples


def _reset_ai_results(db: Session, user_id: int) -> int:
    """Clears every category that was NOT confirmed by the user, so it can be redone."""
    cleared = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.category.is_not(None),
            or_(Transaction.category_source.is_(None), Transaction.category_source != "user"),
        )
        .update(
            {
                Transaction.category: None,
                Transaction.category_confidence: None,
                Transaction.category_reasoning: None,
                Transaction.category_source: None,
                Transaction.needs_review: False,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    logger.info(f"Reset {cleared} AI-assigned categories for user_id={user_id}")
    return cleared


def _categorize_with_llm(
    db: Session, user_id: int, unresolved: dict[str, list[Transaction]], summary: dict
) -> None:
    """Layer 3: sends the leftover unique descriptions to the LLM, batch by batch."""
    total_rows = sum(len(rows) for rows in unresolved.values())

    try:
        agent = ExpenseCategorizerAgent()
    except Exception as exc:  # most often: API key missing
        logger.error(f"LLM unavailable: {exc}")
        summary["failed"] += total_rows
        summary["message"] = (
            f"LLM unavailable ({exc}). Memory and rule matches were saved; "
            "fix the configuration and run /categorize again for the rest."
        )
        return

    examples = _load_examples(db, user_id)
    keys = list(unresolved)
    batch_size = settings.CATEGORIZER_BATCH_SIZE
    consecutive_failures = 0

    for start in range(0, len(keys), batch_size):
        batch_keys = keys[start:start + batch_size]

        if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
            skipped = sum(len(unresolved[k]) for k in keys[start:])
            summary["failed"] += skipped
            summary["message"] = "Stopped early: the LLM kept failing (quota or key problem). Run again later."
            logger.error(f"Aborting LLM stage, {skipped} transactions left uncategorized")
            return

        if start > 0:
            time.sleep(settings.CATEGORIZER_BATCH_DELAY_SECONDS)   # respect free-tier rate limits

        # One item per UNIQUE description; occurrences + median amount give the model context
        items = [
            PendingItem(
                item_id=index,
                description=unresolved[key][0].description,
                amount=statistics.median(t.amount for t in unresolved[key]),
                occurrences=len(unresolved[key]),
            )
            for index, key in enumerate(batch_keys)
        ]
        results = agent.categorize_batch(items, examples)
        consecutive_failures = consecutive_failures + 1 if not results else 0

        for index, key in enumerate(batch_keys):
            rows = unresolved[key]
            result = results.get(index)
            if result is None:
                summary["failed"] += len(rows)     # leave category empty so it is retried next run
                continue
            for transaction in rows:
                _apply(transaction, result)
            summary["categorized_by_llm"] += len(rows)

        db.commit()   # save progress after every batch, so a crash never loses finished work


def categorize_pending_transactions(db: Session, user_id: int, reset_ai_results: bool = False) -> dict:
    """
    Categorizes every uncategorized transaction of one user.

    reset_ai_results=True first clears all non-user-confirmed categories, which is
    how you re-run the improved agent over old data. Confirmed corrections are kept.
    """
    if reset_ai_results:
        _reset_ai_results(db, user_id)

    pending = (
        db.query(Transaction)
        .filter(Transaction.user_id == user_id, Transaction.category.is_(None))
        .order_by(Transaction.id.asc())
        .all()
    )
    summary: dict = {
        "total_processed": len(pending), "categorized_by_memory": 0, "categorized_by_rules": 0,
        "categorized_by_llm": 0, "needs_review": 0, "failed": 0, "message": None,
    }
    if not pending:
        return summary

    # Group identical descriptions: classify "Monthly house rent" once, apply to all 3 rows
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for transaction in pending:
        transaction.description_key = normalize_description(transaction.description)
        groups[transaction.description_key].append(transaction)

    memory = _load_memory(db, user_id, set(groups)) if settings.CATEGORIZER_ENABLE_MEMORY else {}

    unresolved: dict[str, list[Transaction]] = {}
    for key, rows in groups.items():
        result: CategorizationResult | None = None

        if key in memory:
            result = CategorizationResult(memory[key], 1.0, "Matches a category you confirmed earlier.", "memory")
        elif settings.CATEGORIZER_ENABLE_RULES:
            match = rule_categorize(rows[0].description)
            if match:
                result = CategorizationResult(match.category, RULE_CONFIDENCE, f"Matched keyword '{match.keyword}'.", "rule")

        if result is None:
            unresolved[key] = rows
            continue
        for transaction in rows:
            _apply(transaction, result)
        summary[_SUMMARY_FIELD[result.source]] += len(rows)
    db.commit()

    if unresolved:
        _categorize_with_llm(db, user_id, unresolved, summary)

    summary["needs_review"] = sum(1 for t in pending if t.needs_review)
    if summary["failed"] and not summary["message"]:
        summary["message"] = (
            f"{summary['failed']} transaction(s) could not be categorized (LLM error or rate limit). "
            "They were left empty, so run /categorize again later. Check the server logs for details."
        )
    db.commit()

    logger.info(f"Categorization run for user_id={user_id}: { {k: v for k, v in summary.items() if k != 'message'} }")
    return summary


def apply_user_correction(
    db: Session, user_id: int, transaction_id: int, category: str, apply_to_similar: bool
) -> tuple[Transaction, int]:
    """
    The user fixes (or confirms) a category. This is the agent's learning signal:
      * the row becomes source='user' with confidence 1.0
      * from now on it is MEMORY (same description is auto-categorized next time)
      * it can appear as an EXAMPLE in the LLM prompt
    The user_id filter means you can only touch your own rows; someone else's id
    returns 404 (not 403) so we don't reveal that it exists.
    """
    transaction = (
        db.query(Transaction)
        .filter(Transaction.id == transaction_id, Transaction.user_id == user_id)
        .first()
    )
    if transaction is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found.")

    key = normalize_description(transaction.description)
    transaction.description_key = key
    _apply(transaction, CategorizationResult(category, 1.0, "Confirmed by you.", "user"))

    similar_updated = 0
    if apply_to_similar:
        similar = (
            db.query(Transaction)
            .filter(
                Transaction.user_id == user_id,
                Transaction.id != transaction.id,
                Transaction.description_key == key,
                or_(Transaction.category_source.is_(None), Transaction.category_source != "user"),
            )
            .all()
        )
        for row in similar:
            _apply(row, CategorizationResult(category, 1.0, "Same description as a category you confirmed.", "memory"))
        similar_updated = len(similar)

    db.commit()
    db.refresh(transaction)
    logger.info(f"User correction user_id={user_id} tx={transaction_id} -> {category} (+{similar_updated} similar)")
    return transaction, similar_updated
